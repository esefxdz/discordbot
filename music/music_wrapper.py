import wavelink
import discord
import asyncio
import json
import logging
from pathlib import Path
from discord.ext import commands

from .shared.constants import C_MAIN
from .shared.state import GuildState
from .player.commands import PlayerCommands
from .player.queue import QueueCommands
from .player.events import MusicEvents
from .radio.commands import RadioCommands
from .radio.events import RadioEvents

log = logging.getLogger(__name__)


class Music(PlayerCommands, QueueCommands, MusicEvents, RadioCommands, RadioEvents, commands.Cog):
    """
    Main Music cog. Inherits all command/event mixins:
      - PlayerCommands  (play, pause, resume, skip, stop, seek, loop, autoplay, nowplaying)
      - QueueCommands   (queue, remove, move, shuffle, clear, history)
      - MusicEvents     (on_ready, on_voice_state_update, on_wavelink_track_end/start)
      - RadioCommands   (radio, _play_radio_stream, _notify_radio_drop)
      - RadioEvents     (on_wavelink_track_exception, on_wavelink_track_stuck)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Per-guild state and infrastructure
        self._states:         dict[int, GuildState]    = {}
        self._voice_channels: dict[int, int]           = {}  # guild_id → voice channel id (for status cleanup)
        self._radio_channels: dict[int, int]           = {}  # guild_id → text channel id (for drop notifications)
        self._music_channels: dict[int, int]           = {}  # guild_id → text channel id (for now-playing announcements)
        self._locks:          dict[int, asyncio.Lock]  = {}

        # Load radio stations
        stations_path = Path(__file__).parent / "radio_stations.json"
        with open(stations_path, "r", encoding="utf-8") as f:
            self._radio_stations: dict[str, list[dict]] = json.load(f)
        log.info(
            f"Loaded radio stations: "
            f"{sum(len(v) for v in self._radio_stations.values())} stations "
            f"in {len(self._radio_stations)} categories"
        )

    # ── shared helpers (used by all mixins via self) ──────────────────────────
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
