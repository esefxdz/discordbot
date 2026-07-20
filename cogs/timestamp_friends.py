"""Timestamp converter for friends — detects "my time" in messages.
######################################################################
No slash commands.  When a hardcoded friend writes *my time* anywhere
in their message the bot parses the surrounding text for time expressions,
converts them to the friend's local timezone, and replies with ready-to-use
Discord <t:unix:FORMAT> markdown.

Requires:  pip install dateparser
Friend map: TIMESTAMP_FRIEND_{DISCORD_USER_ID}=Country  in credentials.env
"""
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from .tz_data import find_offset

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# optional dependency
# ---------------------------------------------------------------------------
try:
    import dateparser
    from dateparser.search import search_dates as _search_dates
    HAS_DATEPARSER = True
except ImportError:
    HAS_DATEPARSER = False
    log.warning(
        "timestamp_friends: dateparser not installed; "
        "install with `pip install dateparser`"
    )

# ---------------------------------------------------------------------------
# trigger phrase & morning-indicator regex
# ---------------------------------------------------------------------------
_TRIGGER_RE = re.compile(r"my time", re.IGNORECASE)

# Morning indicators that prevent the PM default from kicking in.
# (?:\b|(?<=\d)) handles "3 am" (space), "3am" (digit-prefixed), and
# "a.m." at sentence start — \b alone fails when am/a.m. follows a digit.
_MORNING_RE = re.compile(
    r"(?:\b|(?<=\d))(?:am|a\.m|morning|dawn|sunrise|early\s*morning)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _all_formats(unix_ts: int) -> list[tuple[str, str]]:
    """Return every Discord timestamp format for *unix_ts*."""
    return [
        ("Short Time",       f"<t:{unix_ts}:t>"),
        ("Long Time",        f"<t:{unix_ts}:T>"),
        ("Short Date",       f"<t:{unix_ts}:d>"),
        ("Long Date",        f"<t:{unix_ts}:D>"),
        ("Short Date+Time",  f"<t:{unix_ts}:f>"),
        ("Long Date+Time",   f"<t:{unix_ts}:F>"),
        ("Relative",         f"<t:{unix_ts}:R>"),
    ]


def _load_friend_map() -> dict[int, tuple[str, float]]:
    """Scan environment for ``TIMESTAMP_FRIEND_{ID}=Country`` entries.

    Returns ``{user_id: (country_name, utc_offset)}``.
    """
    friends: dict[int, tuple[str, float]] = {}
    for key, raw in os.environ.items():
        if not key.startswith("TIMESTAMP_FRIEND_"):
            continue
        uid_str = key.removeprefix("TIMESTAMP_FRIEND_")
        try:
            uid = int(uid_str)
        except ValueError:
            log.warning("timestamp_friends: invalid user ID in env var %s", key)
            continue
        country = raw.strip()
        offset = find_offset(country)
        if offset is None:
            log.warning(
                "timestamp_friends: unknown country '%s' for user %d (env var %s)",
                country, uid, key,
            )
            continue
        friends[uid] = (country, offset)
    return friends


def _apply_pm_default(
    dt: datetime, matched_text: str,
) -> datetime:
    """If *matched_text* has no morning indicator and the hour is 1–11,
    bump by 12 h to treat the bare number as PM."""
    if _MORNING_RE.search(matched_text):
        return dt
    if 1 <= dt.hour <= 11:
        return dt + timedelta(hours=12)
    return dt


# ==========================================
# COG
# ==========================================

class TimestampFriends(commands.Cog):
    """Automatically convert time expressions when a friend says 'my time'."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.friends = _load_friend_map()
        if not self.friends:
            log.info("timestamp_friends: no friends configured.")
        else:
            log.info("timestamp_friends: loaded %d friend(s).", len(self.friends))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # --- quick bail-outs ---
        if message.author.bot:
            return
        if not HAS_DATEPARSER:
            return
        if message.author.id not in self.friends:
            return
        if not _TRIGGER_RE.search(message.content):
            return

        # --- strip the trigger phrase ---
        cleaned = _TRIGGER_RE.sub(" ", message.content).strip()
        if not cleaned:
            return  # message was literally just "my time"

        # --- parse time expressions ---
        try:
            raw_matches = _search_dates(
                cleaned,
                languages=["en"],
                settings={
                    "PREFER_DATES_FROM": "future",
                    "PREFER_DAY_OF_MONTH": "first",
                },
            )
        except Exception:
            log.debug("timestamp_friends: search_dates failed", exc_info=True)
            return  # quiet fail
        if not raw_matches:
            return  # quiet fail — nothing parseable

        country, offset = self.friends[message.author.id]
        tz = timezone(timedelta(hours=offset))

        # --- process each match ---
        results: list[tuple[datetime, int]] = []  # (local_dt, unix_ts)
        seen: set[int] = set()  # dedupe by unix ts

        for matched_text, dt_naive in raw_matches:
            # PM default when no meridian given
            dt_adjusted = _apply_pm_default(dt_naive, matched_text)

            # attach friend's timezone, then convert to UTC
            dt_aware = dt_adjusted.replace(tzinfo=tz)
            unix_ts = int(dt_aware.astimezone(timezone.utc).timestamp())

            if unix_ts not in seen:
                seen.add(unix_ts)
                results.append((dt_adjusted, unix_ts))

        if not results:
            return  # quiet fail

        # --- build reply ---
        lines: list[str] = []
        if len(results) > 1:
            lines.append(f"*{len(results)} times found*")

        for local_dt, unix_ts in results:
            if lines:
                lines.append("")
            lines.append(f"**{local_dt.strftime('%d %B at %H:%M')}**")
            lines.append(f"Unix: `{unix_ts}`")
            lines.append("")
            for label, tag in _all_formats(unix_ts):
                lines.append(f"`{tag}`　← {label}")

        lines.append("")
        lines.append("*Times display in each viewer's local timezone.*")

        await message.reply("\n".join(lines), mention_author=False)


# ==========================================
# setup
# ==========================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimestampFriends(bot))
