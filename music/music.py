import wavelink
import discord
import random
import asyncio
import logging
import json
from pathlib import Path
from collections import deque
from discord.ext import commands

log = logging.getLogger(__name__)

# ── colours & assets ────────────────────────────────────────────────────────
C_MAIN   = 0x8BE9FD   # cyan  – now playing
C_QUEUE  = 0xBD93F9   # purple – queue
C_OK     = 0x50FA7B   # green  – success
C_WARN   = 0xFFB86C   # orange – warnings / loop
C_ERR    = 0xFF5555   # red    – errors
C_HIST   = 0x6272A4   # muted  – history
C_RADIO  = 0xFF79C6   # pink  – radio

LOOP_OFF   = 0
LOOP_TRACK = 1
LOOP_QUEUE = 2

LOOP_LABEL = {
    LOOP_OFF:   "Off",
    LOOP_TRACK: "🔂 Track",
    LOOP_QUEUE: "🔁 Queue",
}

MODE_MUSIC = "music"
MODE_RADIO = "radio"

# ── per-guild state ──────────────────────────────────────────────────────────
class GuildState:
    def __init__(self):
        self.queue:    deque              = deque()
        self.history:  list               = []   # last 10 tracks
        self.loop:     int                = LOOP_OFF
        self.autoplay: bool               = False
        self.current:  wavelink.Playable | None = None
        self.mode:     str | None         = None

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

# ── UI views ─────────────────────────────────────────────────────────────────
class _CategoryView(discord.ui.View):
    """Step 1: buttons for each radio category."""
    def __init__(self, bot: commands.Bot, stations: dict, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.stations = stations
        self.message: discord.Message | None = None

        for i, category in enumerate(stations.keys()):
            # Truncate long category names for button labels (max 80 chars)
            label = category[:80]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                row=i // 5,  # 5 buttons per row
            )
            btn.callback = self._make_category_callback(category)
            self.add_item(btn)

    def _make_category_callback(self, category: str):
        async def callback(interaction: discord.Interaction):
            station_list = self.stations[category]
            view = _StationSelect(self.bot, category, station_list, interaction, timeout=self.timeout)
            embed = discord.Embed(
                title=f"📻 {category}",
                description="Pick a station from the dropdown below:",
                color=C_RADIO,
            )
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message
        return callback

    async def on_timeout(self):
        if self.message:
            try:
                for child in self.children:
                    child.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass


class _StationSelect(discord.ui.View):
    """Step 2: dropdown to pick a specific station."""
    def __init__(self, bot: commands.Bot, category: str, stations: list[dict], interaction: discord.Interaction, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.category = category
        self.stations = stations
        self._interaction = interaction
        self.message: discord.Message | None = None
        self._picked: dict | None = None

        options = []
        for s in stations:
            label = s["name"][:100]  # Discord limit
            desc = s["desc"][:100] if s["desc"] else "\u200b"  # zero-width space if empty
            options.append(discord.SelectOption(label=label, description=desc, value=s["name"]))

        select = discord.ui.Select(
            placeholder="Choose a station…",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        station_name = interaction.data["values"][0]  # type: ignore
        for s in self.stations:
            if s["name"] == station_name:
                self._picked = s
                break
        # Disable the dropdown immediately
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Do the actual radio playback
        cog = self.bot.get_cog("Music")
        if cog and self._picked:
            await cog._play_radio_stream(interaction, self.category, self._picked)
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                for child in self.children:
                    child.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass


# ── cog ──────────────────────────────────────────────────────────────────────
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._states: dict[int, GuildState] = {}
        self._voice_channels: dict[int, int] = {}  # guild_id → channel_id for disconnect cleanup
        self._radio_channels: dict[int, int] = {}  # guild_id → channel_id for radio-drop notifications
        self._locks: dict[int, asyncio.Lock] = {}

        # Load radio stations
        stations_path = Path(__file__).parent / "radio_stations.json"
        with open(stations_path, "r", encoding="utf-8") as f:
            self._radio_stations: dict[str, list[dict]] = json.load(f)
        log.info(f"Loaded radio stations: {sum(len(v) for v in self._radio_stations.values())} stations in {len(self._radio_stations)} categories")

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

    def _lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create a per-guild asyncio.Lock."""
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _is_current_track(self, st: GuildState, event_track: wavelink.Playable | None) -> bool:
        """True if event_track is the same track currently stored in guild state."""
        if st.current is None or event_track is None:
            return False
        return event_track.identifier == st.current.identifier

    # ── channel status ────────────────────────────────────────────────────────
    async def _set_channel_status(self, player: wavelink.Player, track: wavelink.Playable | None = None, *, title: str | None = None):
        """Set (or clear) the voice channel status. Pass *title* for radio; otherwise uses track.title."""
        channel = player.channel
        if channel is None:
            return
        try:
            if title is not None:
                status = f"📻 {title}"[:500]
            elif track is not None:
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
            self._locks.pop(guild_id, None)
            self._radio_channels.pop(guild_id, None)
            await self._clear_channel_status(guild_id)

    # ── track end → advance queue ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return

        st = self.state(player.guild.id)

        # Ignore stale events for a track that is no longer current
        # (e.g. track_end for an old radio stream arriving after a switch)
        if not self._is_current_track(st, payload.track):
            return

        if st.loop == LOOP_TRACK and st.current:
            await player.play(st.current)
            await self._set_channel_status(player, st.current)
            return

        if st.loop == LOOP_QUEUE and st.current:
            st.queue.append(st.current)

        if st.current:
            st.push_history(st.current)

        if st.queue:
            next_track = st.queue.popleft()
            st.current = next_track
            await player.play(next_track)
            await self._set_channel_status(player, next_track)
        elif st.autoplay:
            # wavelink built-in autoplay
            await player.set_autoplay(wavelink.AutoPlayMode.enabled)
        else:
            # If a radio stream dropped unexpectedly, notify the channel
            if st.mode == MODE_RADIO:
                await self._notify_radio_drop(player.guild.id)
            st.current = None
            st.mode = None
            await self._set_channel_status(player)

    # ── track exception / stuck → radio stream failures ──────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return
        st = self.state(player.guild.id)
        if st.mode == MODE_RADIO:
            if not self._is_current_track(st, payload.track):
                return
            log.warning(f"Radio stream exception in guild {player.guild.id}: {payload.exception}")
            await self._notify_radio_drop(player.guild.id)
            st.mode = None
            await self._set_channel_status(player)

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return
        st = self.state(player.guild.id)
        if st.mode == MODE_RADIO:
            if not self._is_current_track(st, payload.track):
                return
            log.warning(f"Radio stream stuck in guild {player.guild.id}, threshold {payload.threshold_ms}ms")
            await self._notify_radio_drop(player.guild.id)
            st.mode = None
            await self._set_channel_status(player)

    async def _notify_radio_drop(self, guild_id: int):
        """Send a notification to the channel where radio was started."""
        channel_id = self._radio_channels.pop(guild_id, None)
        if channel_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            return
        try:
            await channel.send(embed=discord.Embed(
                description="📻 Radio stream ended unexpectedly. Use `!radio` to restart.",
                color=C_WARN,
            ))
        except Exception:
            log.warning(f"Failed to send radio-drop notification in guild {guild_id}", exc_info=True)

    # ── track start → update status ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return
        st = self.state(player.guild.id)
        st.current = payload.track
        # Only auto-update status for music mode (radio sets its own title)
        if st.mode != MODE_RADIO:
            await self._set_channel_status(player, st.current)

    # ── !play ─────────────────────────────────────────────────────────────────
    @commands.command(aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song or playlist. Accepts search terms or YouTube URLs."""
        if not ctx.author.voice:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Join a voice channel first.", color=C_ERR))

        st = self.state(ctx.guild.id)

        # Mode check: refuse if radio is active
        if st.mode == MODE_RADIO:
            return await ctx.reply(embed=discord.Embed(
                description="📻 Radio is currently active. Use `!stop` first to switch back to music.",
                color=C_WARN))

        async with self._lock(ctx.guild.id):
            vc = self._get_player(ctx)
            if not vc:
                vc = await ctx.author.voice.channel.connect(cls=wavelink.Player)
                vc.autoplay = wavelink.AutoPlayMode.disabled
                vc.inactive_timeout = None
                self._voice_channels[ctx.guild.id] = ctx.author.voice.channel.id

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
                    st.current = st.queue.popleft()
                    await vc.play(st.current)
                    await self._set_channel_status(vc, st.current)

                st.mode = MODE_MUSIC

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

                st.mode = MODE_MUSIC

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
        """Stop playback (music or radio), clear the queue, and disconnect from voice."""
        st = self.state(ctx.guild.id)
        current_mode = st.mode

        st.queue.clear()
        st.current = None
        st.loop = LOOP_OFF
        st.autoplay = False
        st.mode = None

        vc = self._get_player(ctx)
        if vc:
            await self._set_channel_status(vc)
            await vc.disconnect()

        self._voice_channels.pop(ctx.guild.id, None)
        self._radio_channels.pop(ctx.guild.id, None)
        self._locks.pop(ctx.guild.id, None)

        if current_mode == MODE_RADIO:
            msg = "⏹️ Stopped radio and disconnected."
        else:
            msg = "⏹️ Stopped and disconnected."
        await ctx.reply(embed=discord.Embed(description=msg, color=C_OK))

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
            for i, t in enumerate(list(st.queue)[:15], 1):
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
        q = list(st.queue)
        removed = q.pop(position - 1)
        st.queue = deque(q)
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
        q = list(st.queue)
        track = q.pop(from_pos - 1)
        q.insert(to_pos - 1, track)
        st.queue = deque(q)
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
        q = list(st.queue)
        random.shuffle(q)
        st.queue = deque(q)
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

    # ── !radio ────────────────────────────────────────────────────────────────
    @commands.command()
    async def radio(self, ctx: commands.Context):
        """Browse and play radio stations with a two-step interactive picker."""
        if not ctx.author.voice:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Join a voice channel first.", color=C_ERR))

        st = self.state(ctx.guild.id)

        # Mode check: refuse if music is active
        if st.mode == MODE_MUSIC and (st.current or st.queue):
            return await ctx.reply(embed=discord.Embed(
                description="🎵 Music is currently playing. Use `!stop` first to switch to radio.",
                color=C_WARN))

        # Step 1: show category buttons
        view = _CategoryView(self.bot, self._radio_stations)
        embed = discord.Embed(
            title="📻  Radio — Choose a Category",
            description="Select a genre to browse stations:",
            color=C_RADIO,
        )
        await ctx.reply(embed=embed, view=view)

    # ── radio playback helper (called from _StationSelect callback) ──────────
    async def _play_radio_stream(self, interaction: discord.Interaction, category: str, station: dict):
        """Connect, play a radio stream, and set mode. Called from the station picker callback."""
        guild = interaction.guild
        if guild is None:
            return

        member = guild.get_member(interaction.user.id)
        if member is None or member.voice is None:
            await interaction.followup.send(
                embed=discord.Embed(description="❌ You must be in a voice channel.", color=C_ERR),
                ephemeral=True,
            )
            return

        st = self.state(guild.id)

        async with self._lock(guild.id):
            # Re-check mode — music may have started between !radio and now
            if st.mode == MODE_MUSIC:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description="🎵 Music is currently playing. Use `!stop` first to switch to radio.",
                        color=C_WARN,
                    ),
                    ephemeral=True,
                )
                return

            # Connect or reuse existing player
            vc = guild.voice_client
            if not isinstance(vc, wavelink.Player):
                try:
                    vc = await member.voice.channel.connect(cls=wavelink.Player)
                except Exception as e:
                    await interaction.followup.send(
                        embed=discord.Embed(description=f"❌ Failed to connect: {e}", color=C_ERR),
                        ephemeral=True,
                    )
                    return
                vc.autoplay = wavelink.AutoPlayMode.disabled
                vc.inactive_timeout = None
                self._voice_channels[guild.id] = member.voice.channel.id

            # Clear mode before skipping so the old track_end handler
            # doesn't interfere with the new station's state
            if vc.playing or vc.paused:
                st.mode = None
                st.current = None
                await vc.skip()

            # Resolve and play the stream URL via Lavalink
            try:
                results = await wavelink.Playable.search(station["url"])
                if not results:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            description=f"❌ Could not resolve stream: `{station['url']}`\n"
                                         "Lavalink may need the **LavaSrc** plugin for direct HTTP/Icecast URLs.",
                            color=C_ERR,
                        ),
                        ephemeral=True,
                    )
                    return
                track = results[0]
            except Exception as e:
                await interaction.followup.send(
                    embed=discord.Embed(description=f"❌ Failed to load stream: {e}", color=C_ERR),
                    ephemeral=True,
                )
                return

            # Set radio state *before* play so on_wavelink_track_start
            # sees MODE_RADIO and doesn't overwrite our status text
            st.queue.clear()
            st.current = track
            st.loop = LOOP_OFF
            st.autoplay = False
            st.mode = MODE_RADIO
            self._radio_channels[guild.id] = interaction.channel_id

            try:
                await vc.play(track)
            except Exception as e:
                st.mode = None
                self._radio_channels.pop(guild.id, None)
                await interaction.followup.send(
                    embed=discord.Embed(description=f"❌ Failed to play stream: {e}", color=C_ERR),
                    ephemeral=True,
                )
                return

            # Set channel status with the JSON station name
            await self._set_channel_status(vc, title=station["name"])

            # Send now-playing embed
            embed = discord.Embed(
                title="📻  Now Playing",
                description=f"### {station['name']}",
                color=C_RADIO,
            )
            if station["desc"]:
                embed.add_field(name="Description", value=station["desc"], inline=False)
            embed.add_field(name="Category", value=category, inline=True)
            embed.add_field(name="Source", value=station["url"], inline=True)
            embed.set_footer(text="yuuka radio · use !stop to disconnect")
            await interaction.followup.send(embed=embed)

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
        embed.add_field(name="Radio", value=(
            "`!radio` — Browse & play radio stations\n"
            "Use `!stop` to stop either music or radio"
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
