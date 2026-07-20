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
from datetime import timezone, timedelta
import discord
from discord.ext import commands
from .tz_data import find_offset, has_ampm, normalize_bare_hours, parse_bare_hours
log = logging.getLogger(__name__)
import dateparser
from dateparser.search import search_dates as _search_dates

# ---------------------------------------------------------------------------
# trigger phrase
# ---------------------------------------------------------------------------
_TRIGGER_RE = re.compile(r"my time", re.IGNORECASE)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tag(unix_ts: int) -> str:
    """Return a single Discord Short Date+Time tag."""
    return f"<t:{unix_ts}:f>"


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
        original = _TRIGGER_RE.sub(" ", message.content).strip()
        if not original:
            return  # message was literally just "my time"

        # --- normalize bare hours so dateparser doesn't mistake them for dates ---
        cleaned = normalize_bare_hours(original)

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
        country, offset = self.friends[message.author.id]

        if not raw_matches:
            # --- fallback: use original text (not normalized) ---
            results = parse_bare_hours(original, offset)
            if results:
                tags = [_tag(ts) for ts in results]
                await message.reply("\n".join(tags), mention_author=False)
            return

        tz = timezone(timedelta(hours=offset))

        # --- process each match ---
        results: list[int] = []
        seen: set[int] = set()  # dedupe by unix ts

        for matched_text, dt_naive in raw_matches:
            # bare times without AM/PM default to PM
            if not has_ampm(matched_text) and 1 <= dt_naive.hour <= 11:
                dt_naive = dt_naive + timedelta(hours=12)

            # attach friend's timezone, then convert to UTC
            dt_aware = dt_naive.replace(tzinfo=tz)
            unix_ts = int(dt_aware.astimezone(timezone.utc).timestamp())

            if unix_ts not in seen:
                seen.add(unix_ts)
                results.append(unix_ts)

        if not results:
            return  # quiet fail

        tags = [_tag(ts) for ts in results]
        await message.reply("\n".join(tags), mention_author=False)



# ==========================================
# setup
# ==========================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimestampFriends(bot))
