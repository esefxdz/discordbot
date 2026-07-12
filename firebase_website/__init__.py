"""firebase_website — Bot → Firestore sync for the esefos.netlify.app dashboard.

Add integrations here as they grow:
  sysinfo_sync  — CPU, RAM, disk, uptime pushed every 10 s
  (future)      — gacha stats, now-playing, radio listeners, …

Usage from main.py:
    await bot.load_extension("firebase_website")
"""
######################################################################
from discord.ext import commands

from .sysinfo_sync import SysInfoSync

_sync: SysInfoSync | None = None


async def setup(bot: commands.Bot) -> None:
    """Called by bot.load_extension(). Starts the sysinfo sync loop."""
    global _sync
    _sync = SysInfoSync()
    _sync.start()


async def teardown(bot: commands.Bot) -> None:
    """Called on bot unload / shutdown. Stops all sync tasks."""
    global _sync
    if _sync is not None:
        _sync.stop()
