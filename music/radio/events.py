import wavelink
import logging
from discord.ext import commands
from ..shared.constants import MODE_RADIO

log = logging.getLogger(__name__)


class RadioEvents:
    """Mixin: wavelink failure events that only apply to radio mode."""

    # ── track exception → radio stream failure ────────────────────────────────
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

    # ── track stuck → radio stream failure ────────────────────────────────────
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
