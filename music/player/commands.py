import wavelink
import discord
import logging
from discord.ext import commands
from ..shared.constants import (
    C_MAIN, C_QUEUE, C_OK, C_WARN, C_ERR,
    LOOP_OFF, LOOP_TRACK, LOOP_QUEUE, LOOP_LABEL,
    MODE_MUSIC, MODE_RADIO,
)
from ..shared.utils import fmt_duration, progress_bar, is_dj

log = logging.getLogger(__name__)


class PlayerCommands:
    """Mixin: core playback commands (play, pause, resume, skip, stop, seek, loop, autoplay, nowplaying)."""

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

            # ── playlist ──────────────────────────────────────────────────────
            if isinstance(results, wavelink.Playlist):
                tracks = list(results.tracks)
                for t in tracks:
                    t.extras = {"requester_id": ctx.author.id}
                    st.queue.append(t)

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
                embed.add_field(name="Tracks",    value=str(len(tracks)))
                embed.add_field(name="Added by",  value=ctx.author.mention)
                await ctx.reply(embed=embed)

            # ── single track ──────────────────────────────────────────────────
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
                    embed.add_field(name="Duration",     value=fmt_duration(track.length))
                    embed.add_field(name="Position",     value=f"#{len(st.queue)}")
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
        st.current  = None
        st.loop     = LOOP_OFF
        st.autoplay = False
        st.mode     = None

        vc = self._get_player(ctx)
        if vc:
            await self._set_channel_status(vc)
            await vc.disconnect()

        self._voice_channels.pop(ctx.guild.id, None)
        self._radio_channels.pop(ctx.guild.id, None)
        self._locks.pop(ctx.guild.id, None)

        msg = "⏹️ Stopped radio and disconnected." if current_mode == MODE_RADIO else "⏹️ Stopped and disconnected."
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
        await ctx.reply(embed=discord.Embed(description=f"⏩ Seeked to `{timestamp}`.", color=C_OK))

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

        track  = st.current
        pos_ms = vc.position
        dur_ms = track.length
        bar    = progress_bar(pos_ms, dur_ms)

        if track.extras and isinstance(track.extras, dict):
            requester_id = track.extras.get("requester_id")
            requester    = f"<@{requester_id}>" if requester_id else "Unknown"
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
