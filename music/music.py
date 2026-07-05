import wavelink
import discord
import random
import asyncio
import logging
from discord.ext import commands

log = logging.getLogger(__name__)

# ── colours & assets ────────────────────────────────────────────────────────
C_MAIN   = 0x8BE9FD   # cyan  – now playing
C_QUEUE  = 0xBD93F9   # purple – queue
C_OK     = 0x50FA7B   # green  – success
C_WARN   = 0xFFB86C   # orange – warnings / loop
C_ERR    = 0xFF5555   # red    – errors
C_HIST   = 0x6272A4   # muted  – history

LOOP_OFF   = 0
LOOP_TRACK = 1
LOOP_QUEUE = 2

LOOP_LABEL = {
    LOOP_OFF:   "Off",
    LOOP_TRACK: "🔂 Track",
    LOOP_QUEUE: "🔁 Queue",
}

# ── per-guild state ──────────────────────────────────────────────────────────
class GuildState:
    def __init__(self):
        self.queue:    list               = []
        self.history:  list               = []   # last 10 tracks
        self.loop:     int                = LOOP_OFF
        self.autoplay: bool               = False
        self.current:  wavelink.Playable | None = None

    def push_history(self, track: wavelink.Playable):
        self.history.insert(0, track)
        if len(self.history) > 10:
            self.history.pop()

# ── helpers ──────────────────────────────────────────────────────────────────
def fmt_duration(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"

def progress_bar(position_ms: int, duration_ms: int, width: int = 14) -> str:
    if duration_ms == 0:
        return "▬" * width
    pct   = min(position_ms / duration_ms, 1.0)
    filled = int(pct * width)
    return "▬" * filled + "🔘" + "▬" * (width - filled)

def is_dj():
    """Soft DJ check — allows anyone to use info/play, restricts control commands."""
    async def predicate(ctx: commands.Context):
        if any(r.name.lower() == "dj" for r in ctx.author.roles):
            return True
        await ctx.reply(
            embed=discord.Embed(
                description="🎧 You need the **DJ** role to use this command.",
                color=C_ERR,
            )
        )
        return False
    return commands.check(predicate)

# ── cog ──────────────────────────────────────────────────────────────────────
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._states: dict[int, GuildState] = {}
        self._voice_channels: dict[int, int] = {}  # guild_id → channel_id for disconnect cleanup

    def state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        """Get the wavelink Player for a context, or None."""
        vc = ctx.voice_client
        if isinstance(vc, wavelink.Player):
            return vc
        return None

    # ── channel status ────────────────────────────────────────────────────────
    async def _set_channel_status(self, player: wavelink.Player, track: wavelink.Playable | None):
        """Set (or clear) the voice channel status to reflect the current track."""
        channel = player.channel
        if channel is None:
            return
        try:
            if track:
                status = f"🎵 {track.title}"[:500]
            else:
                status = None
            route = discord.http.Route(
                'PUT',
                '/channels/{channel_id}/voice-status',
                channel_id=channel.id,
            )
            await self.bot.http.request(route, json={'status': status})
        except Exception:
            log.warning(f"Failed to set voice channel status in {player.guild.id}", exc_info=True)

    async def _clear_channel_status(self, guild_id: int):
        """Clear voice channel status after the bot was disconnected."""
        channel_id = self._voice_channels.pop(guild_id, None)
        if channel_id is None:
            return
        try:
            route = discord.http.Route(
                'PUT',
                '/channels/{channel_id}/voice-status',
                channel_id=channel_id,
            )
            await self.bot.http.request(route, json={'status': None})
        except Exception:
            log.warning(f"Failed to clear voice channel status in guild {guild_id}", exc_info=True)

    # ── connect lavalink ─────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_ready(self):
        node = wavelink.Node(uri="http://127.0.0.1:2333", password="yuukabot")
        await wavelink.Pool.connect(nodes=[node], client=self.bot)
        log.info("Wavelink connected to Lavalink")

    # ── voice state update → clear status on disconnect ──────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id != self.bot.user.id:
            return
        # Bot was in a channel and left (kicked, moved, or disconnected)
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            # Also clean up guild state
            st = self._states.pop(guild_id, None)
            if st:
                st.queue.clear()
            await self._clear_channel_status(guild_id)

    # ── track end → advance queue ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return

        st = self.state(player.guild.id)

        if st.loop == LOOP_TRACK and st.current:
            await player.play(st.current)
            await self._set_channel_status(player, st.current)
            return

        if st.loop == LOOP_QUEUE and st.current:
            st.queue.append(st.current)

        if st.current:
            st.push_history(st.current)

        if st.queue:
            next_track = st.queue.pop(0)
            st.current = next_track
            await player.play(next_track)
            await self._set_channel_status(player, next_track)
        elif st.autoplay:
            # wavelink built-in autoplay
            await player.set_autoplay(wavelink.AutoPlayMode.enabled)
        else:
            st.current = None
            await self._set_channel_status(player, None)

    # ── track start → update status ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return
        st = self.state(player.guild.id)
        st.current = payload.track
        await self._set_channel_status(player, st.current)

    # ── !play ─────────────────────────────────────────────────────────────────
    @commands.command(aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song or playlist. Accepts search terms or YouTube URLs."""
        if not ctx.author.voice:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Join a voice channel first.", color=C_ERR))

        vc = self._get_player(ctx)
        if not vc:
            vc = await ctx.author.voice.channel.connect(cls=wavelink.Player)
            vc.autoplay = wavelink.AutoPlayMode.disabled
            vc.inactive_timeout = None
            # Track channel for disconnect cleanup
            self._voice_channels[ctx.guild.id] = ctx.author.voice.channel.id

        st = self.state(ctx.guild.id)

        async with ctx.typing():
            results = await wavelink.Playable.search(query)

        if not results:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Nothing found.", color=C_ERR))

        # playlist
        if isinstance(results, wavelink.Playlist):
            tracks = list(results.tracks)
            for t in tracks:
                t.extras = {"requester_id": ctx.author.id}
                st.queue.append(t)

            # Start the first track if nothing is playing
            if not vc.playing and not vc.paused and st.queue:
                st.current = st.queue.pop(0)
                await vc.play(st.current)
                await self._set_channel_status(vc, st.current)

            embed = discord.Embed(
                title="📋  Playlist Queued",
                description=f"**{results.name}**",
                color=C_QUEUE,
            )
            embed.add_field(name="Tracks", value=str(len(tracks)))
            embed.add_field(name="Added by", value=ctx.author.mention)
            await ctx.reply(embed=embed)

        else:
            track = results[0]
            track.extras = {"requester_id": ctx.author.id}

            if vc.playing or vc.paused:
                st.queue.append(track)
                embed = discord.Embed(
                    title="➕  Added to Queue",
                    description=f"**{track.title}**",
                    color=C_QUEUE,
                )
                embed.add_field(name="Duration",  value=fmt_duration(track.length))
                embed.add_field(name="Position",  value=f"#{len(st.queue)}")
                embed.add_field(name="Requested by", value=ctx.author.mention)
                if track.artwork:
                    embed.set_thumbnail(url=track.artwork)
                return await ctx.reply(embed=embed)

            st.current = track
            await vc.play(track)
            await self._set_channel_status(vc, track)

            embed = discord.Embed(
                title="▶️  Now Playing",
                description=f"**{track.title}**",
                color=C_MAIN,
            )
            embed.add_field(name="Duration",     value=fmt_duration(track.length))
            embed.add_field(name="Requested by", value=ctx.author.mention)
            embed.add_field(name="Loop",         value=LOOP_LABEL[st.loop], inline=True)
            embed.add_field(name="Autoplay",     value="On" if st.autoplay else "Off", inline=True)
            if track.artwork:
                embed.set_thumbnail(url=track.artwork)
            await ctx.reply(embed=embed)

    # ── !pause / !resume ──────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def pause(self, ctx: commands.Context):
        vc = self._get_player(ctx)
        if vc and vc.playing:
            await vc.pause(True)
            await ctx.reply(embed=discord.Embed(description="⏸️ Paused.", color=C_OK))
        else:
            await ctx.reply(embed=discord.Embed(description="❌ Nothing is playing.", color=C_ERR))

    @commands.command()
    @is_dj()
    async def resume(self, ctx: commands.Context):
        vc = self._get_player(ctx)
        if vc and vc.paused:
            await vc.pause(False)
            await ctx.reply(embed=discord.Embed(description="▶️ Resumed.", color=C_OK))
        else:
            await ctx.reply(embed=discord.Embed(description="❌ Nothing is paused.", color=C_ERR))

    # ── !skip ─────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def skip(self, ctx: commands.Context):
        vc = self._get_player(ctx)
        if vc and (vc.playing or vc.paused):
            await vc.skip()
            await ctx.reply(embed=discord.Embed(description="⏭️ Skipped.", color=C_OK))
        else:
            await ctx.reply(embed=discord.Embed(description="❌ Nothing to skip.", color=C_ERR))

    # ── !stop / !leave ────────────────────────────────────────────────────────
    @commands.command(aliases=["leave", "disconnect"])
    @is_dj()
    async def stop(self, ctx: commands.Context):
        """Stop playback, clear the queue, and disconnect from voice."""
        st = self.state(ctx.guild.id)
        st.queue.clear()
        st.current = None
        st.loop = LOOP_OFF
        st.autoplay = False

        vc = self._get_player(ctx)
        if vc:
            await self._set_channel_status(vc, None)
            await vc.disconnect()

        self._voice_channels.pop(ctx.guild.id, None)
        await ctx.reply(embed=discord.Embed(description="⏹️ Stopped and disconnected.", color=C_OK))

    # ── !seek ─────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def seek(self, ctx: commands.Context, timestamp: str):
        """Seek to a timestamp. Format: 1:30 or 90 (seconds)."""
        vc = self._get_player(ctx)
        if not vc or not vc.playing:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Nothing is playing.", color=C_ERR))
        try:
            if ":" in timestamp:
                parts = timestamp.split(":")
                if len(parts) == 2:
                    ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
                else:
                    ms = (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
            else:
                ms = int(timestamp) * 1000
        except ValueError:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Invalid format. Use `1:30` or `90`.", color=C_ERR))

        await vc.seek(ms)
        await ctx.reply(embed=discord.Embed(
            description=f"⏩ Seeked to `{timestamp}`.", color=C_OK))

    # ── !looptrack / !loopqueue ───────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def looptrack(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        st.loop = LOOP_OFF if st.loop == LOOP_TRACK else LOOP_TRACK
        await ctx.reply(embed=discord.Embed(
            description=f"🔂 Loop track: **{'On' if st.loop == LOOP_TRACK else 'Off'}**",
            color=C_WARN))

    @commands.command()
    @is_dj()
    async def loopqueue(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        st.loop = LOOP_OFF if st.loop == LOOP_QUEUE else LOOP_QUEUE
        await ctx.reply(embed=discord.Embed(
            description=f"🔁 Loop queue: **{'On' if st.loop == LOOP_QUEUE else 'Off'}**",
            color=C_WARN))

    # ── !autoplay ─────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def autoplay(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        vc = self._get_player(ctx)
        st.autoplay = not st.autoplay
        if vc:
            mode = wavelink.AutoPlayMode.enabled if st.autoplay else wavelink.AutoPlayMode.disabled
            await vc.set_autoplay(mode)
        await ctx.reply(embed=discord.Embed(
            description=f"✨ Autoplay: **{'On' if st.autoplay else 'Off'}**",
            color=C_OK))

    # ── !nowplaying ───────────────────────────────────────────────────────────
    @commands.command(aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        vc = self._get_player(ctx)
        st = self.state(ctx.guild.id)

        if not vc or not vc.playing or not st.current:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Nothing is playing.", color=C_ERR))

        track    = st.current
        pos_ms   = vc.position
        dur_ms   = track.length
        bar      = progress_bar(pos_ms, dur_ms)

        if track.extras and isinstance(track.extras, dict):
            requester_id = track.extras.get("requester_id")
            requester = f"<@{requester_id}>" if requester_id else "Unknown"
        else:
            requester = "Unknown"

        embed = discord.Embed(
            title="🎵  Now Playing",
            description=f"### {track.title}",
            color=C_MAIN,
        )
        embed.add_field(
            name="Progress",
            value=f"`{fmt_duration(pos_ms)}` {bar} `{fmt_duration(dur_ms)}`",
            inline=False,
        )
        embed.add_field(name="Requested by", value=requester,               inline=True)
        embed.add_field(name="Loop",         value=LOOP_LABEL[st.loop],     inline=True)
        embed.add_field(name="Autoplay",     value="On" if st.autoplay else "Off", inline=True)
        embed.add_field(name="Queue",        value=f"{len(st.queue)} track(s) up next", inline=True)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        embed.set_footer(text="yuuka music")
        await ctx.reply(embed=embed)

    # ── !queue ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["q"])
    async def queue(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)

        if not st.current and not st.queue:
            return await ctx.reply(embed=discord.Embed(
                description="📋 Queue is empty.", color=C_QUEUE))

        embed = discord.Embed(title="📋  Queue", color=C_QUEUE)

        if st.current:
            embed.add_field(
                name="▶️  Now Playing",
                value=f"**{st.current.title}** `{fmt_duration(st.current.length)}`",
                inline=False,
            )

        if st.queue:
            lines  = []
            for i, t in enumerate(st.queue[:15], 1):
                lines.append(f"`{i:02}.` **{t.title}** `{fmt_duration(t.length)}`")
            if len(st.queue) > 15:
                lines.append(f"*... and {len(st.queue) - 15} more*")
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)

        total_ms = sum(t.length for t in st.queue)
        embed.set_footer(text=f"{len(st.queue)} tracks · {fmt_duration(total_ms)} remaining · Loop: {LOOP_LABEL[st.loop]}")
        await ctx.reply(embed=embed)

    # ── !remove ───────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def remove(self, ctx: commands.Context, position: int):
        st = self.state(ctx.guild.id)
        if position < 1 or position > len(st.queue):
            return await ctx.reply(embed=discord.Embed(
                description=f"❌ Position must be between 1 and {len(st.queue)}. Use `!queue` to see positions.", color=C_ERR))
        removed = st.queue.pop(position - 1)
        await ctx.reply(embed=discord.Embed(
            description=f"🗑️ Removed **{removed.title}** from queue.", color=C_OK))

    # ── !move ─────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        st = self.state(ctx.guild.id)
        size = len(st.queue)
        if not (1 <= from_pos <= size) or not (1 <= to_pos <= size):
            return await ctx.reply(embed=discord.Embed(
                description=f"❌ Invalid positions. Queue has {size} items. Use `!queue` to see positions.", color=C_ERR))
        track = st.queue.pop(from_pos - 1)
        st.queue.insert(to_pos - 1, track)
        await ctx.reply(embed=discord.Embed(
            description=f"↕️ Moved **{track.title}** to position **{to_pos}**.", color=C_OK))

    # ── !shuffle ──────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def shuffle(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        if not st.queue:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Queue is empty.", color=C_ERR))
        random.shuffle(st.queue)
        await ctx.reply(embed=discord.Embed(
            description="🔀 Queue shuffled.", color=C_OK))

    # ── !clear ────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def clear(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        st.queue.clear()
        await ctx.reply(embed=discord.Embed(
            description="🗑️ Queue cleared.", color=C_OK))

    # ── !history ──────────────────────────────────────────────────────────────
    @commands.command(aliases=["h"])
    async def history(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        if not st.history:
            return await ctx.reply(embed=discord.Embed(
                description="📜 No history yet.", color=C_HIST))
        lines = [f"`{i:02}.` **{t.title}** `{fmt_duration(t.length)}`"
                 for i, t in enumerate(st.history, 1)]
        embed = discord.Embed(
            title="📜  Recently Played",
            description="\n".join(lines),
            color=C_HIST,
        )
        await ctx.reply(embed=embed)

    # ── !music ────────────────────────────────────────────────────────────────
    @commands.command()
    async def music(self, ctx: commands.Context):
        """List all music commands."""
        embed = discord.Embed(title="🎵  Music Commands", color=C_MAIN)
        embed.add_field(name="Playback", value=(
            "`!play <query/url>` · `!p`\n"
            "`!pause` · `!resume` · `!skip` · `!stop`\n"
            "`!seek <1:30>` · `!looptrack` · `!loopqueue`\n"
            "`!autoplay`"
        ), inline=False)
        embed.add_field(name="Queue", value=(
            "`!queue` · `!q`\n"
            "`!remove <pos>` · `!move <from> <to>`\n"
            "`!shuffle` · `!clear`"
        ), inline=False)
        embed.add_field(name="Info", value=(
            "`!nowplaying` · `!np`\n"
            "`!history` · `!h`"
        ), inline=False)
        embed.set_footer(text="🎧 DJ role required for control commands")
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
