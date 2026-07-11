import wavelink
import discord
import logging
from discord.ext import commands
from ..shared.constants import LOOP_TRACK, LOOP_QUEUE, MODE_RADIO, MODE_MUSIC, C_MAIN
from ..shared.utils import fmt_duration

log = logging.getLogger(__name__)


class MusicEvents:
    """Mixin: wavelink + Discord events that belong to the music player."""

    # ── channel status helpers ────────────────────────────────────────────────
    async def _set_channel_status(
        self,
        player: wavelink.Player,
        track: wavelink.Playable | None = None,
        *,
        title: str | None = None,
    ):
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
                "PUT",
                "/channels/{channel_id}/voice-status",
                channel_id=channel.id,
            )
            await self.bot.http.request(route, json={"status": status})
        except Exception:
            log.warning(f"Failed to set voice channel status in {player.guild.id}", exc_info=True)

    async def _clear_channel_status(self, guild_id: int):
        """Clear voice channel status after the bot was disconnected."""
        channel_id = self._voice_channels.pop(guild_id, None)
        if channel_id is None:
            return
        try:
            route = discord.http.Route(
                "PUT",
                "/channels/{channel_id}/voice-status",
                channel_id=channel_id,
            )
            await self.bot.http.request(route, json={"status": None})
        except Exception:
            log.warning(f"Failed to clear voice channel status in guild {guild_id}", exc_info=True)

    # ── connect lavalink ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_ready(self):
        node = wavelink.Node(uri="http://127.0.0.1:2333", password="yuukabot")
        await wavelink.Pool.connect(nodes=[node], client=self.bot)
        log.info("Wavelink connected to Lavalink")

    # ── voice state update → clean up on disconnect ───────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.id != self.bot.user.id:
            return
        # Bot left a channel (kicked, moved out, or disconnected)
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            st = self._states.pop(guild_id, None)
            if st:
                st.queue.clear()
            self._locks.pop(guild_id, None)
            self._radio_channels.pop(guild_id, None)
            self._music_channels.pop(guild_id, None)
            await self._clear_channel_status(guild_id)

    # ── track end → advance queue ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return

        st = self.state(player.guild.id)

        # Ignore stale events (e.g. track_end for an old radio stream after a switch)
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
            await player.set_autoplay(wavelink.AutoPlayMode.enabled)
        else:
            if st.mode == MODE_RADIO:
                await self._notify_radio_drop(player.guild.id)
            st.current = None
            st.mode    = None
            await self._set_channel_status(player)

    # ── track start → update voice status & announce ─────────────────────────
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if player is None:
            return
        st         = self.state(player.guild.id)
        st.current = payload.track

        if st.mode != MODE_RADIO:
            await self._set_channel_status(player, st.current)

            # Send polished now-playing announcement to the music text channel
            if not st.suppress_np:
                await self._send_now_playing(player.guild.id, st.current)
            st.suppress_np = False

    # ── now-playing announcement helper ───────────────────────────────────────
    async def _send_now_playing(self, guild_id: int, track: wavelink.Playable):
        """Send a compact, polished 'Now Playing' embed to the music channel."""
        channel_id = self._music_channels.get(guild_id)
        if channel_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            self._music_channels.pop(guild_id, None)
            return

        # Build a clean now-playing embed
        embed = discord.Embed(color=C_MAIN)
        desc_parts = [f"## 🎵 {track.title}"]
        if track.author:
            desc_parts.append(f"-# {track.author}")
        embed.description = "\n".join(desc_parts)

        meta: list[str] = []
        if track.length:
            meta.append(f"`{fmt_duration(track.length)}`")
        if track.extras and isinstance(track.extras, dict):
            requester_id = track.extras.get("requester_id")
            if requester_id:
                meta.append(f"Requested by <@{requester_id}>")
        if meta:
            embed.add_field(name="\u200b", value=" · ".join(meta), inline=False)

        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        embed.set_footer(text="yuuka music")

        try:
            await channel.send(embed=embed)
        except Exception:
            log.warning(f"Failed to send now-playing message in guild {guild_id}", exc_info=True)
