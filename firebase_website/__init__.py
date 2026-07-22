"""firebase_website — Bot → Firestore sync using the Admin SDK.

Requires GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service-account JSON.

This package is loaded as a Discord extension purely for lifecycle hooks
(setup / teardown).  It does not register a Cog — it starts a background
SysInfoSync task instead.

Usage from main.py:
    await bot.load_extension("firebase_website")
"""
######################################################################
import logging

from discord.ext import commands

from .config import get_db  # noqa: re‑export for calendar, bucketlist, etc.
from .sysinfo_sync import SysInfoSync

log = logging.getLogger(__name__)

_sync: SysInfoSync | None = None


async def setup(bot: commands.Bot) -> None:
    global _sync
    get_db()  # fail fast if credentials are missing
    _sync = SysInfoSync()
    _sync.start()


async def teardown(bot: commands.Bot) -> None:
    global _sync
    if _sync is not None:
        _sync.stop()
