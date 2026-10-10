"""firebase_website — Bot → Firestore access using the Admin SDK.

Requires GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service-account JSON.

Loaded as a Discord extension only so missing credentials fail at startup.
The calendar and bucketlist cogs use get_db().

Usage from main.py:
    await bot.load_extension("firebase_website")
"""
######################################################################
from discord.ext import commands

from .config import get_db  # noqa: re‑export for calendar, bucketlist, etc.


async def setup(bot: commands.Bot) -> None:
    get_db()  # fail fast if credentials are missing
