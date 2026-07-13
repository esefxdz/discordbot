"""firebase_website — Bot → Firestore sync using the Admin SDK.

Requires GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service-account JSON.

Usage from main.py:
    await bot.load_extension("firebase_website")

Provides:
    get_db() → google.cloud.firestore.AsyncClient  (shared, lazy-init)
"""
######################################################################
import logging
import os

from discord.ext import commands
from google.cloud.firestore import AsyncClient

from .sysinfo_sync import SysInfoSync

log = logging.getLogger(__name__)

_db: AsyncClient | None = None
_sync: SysInfoSync | None = None


def get_db() -> AsyncClient:
    """Return the shared Async Firestore client, creating it on first call."""
    global _db
    if _db is None:
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path is None:
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS environment variable is not set. "
                "Point it to your service-account JSON file."
            )
        _db = AsyncClient()
        log.info("Firestore Admin SDK initialised (project %s)", _db.project)
    return _db


async def setup(bot: commands.Bot) -> None:
    """Start the sysinfo sync loop."""
    global _sync
    get_db()  # early init — fails fast if credentials are missing
    _sync = SysInfoSync()
    _sync.start()


async def teardown(bot: commands.Bot) -> None:
    """Stop the sysinfo sync loop."""
    global _sync
    if _sync is not None:
        _sync.stop()
