"""Timestamp converter cog — generate Discord timestamp markdown via modal.
######################################################################
Usage:
    /timestamp   →  opens a popup form (day, time, country)
                    and returns the Long Date+Time Discord <t:unix:F> tag.
"""
import calendar as cal_mod
import logging
import re
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from .tz_data import find_offset

log = logging.getLogger(__name__)

DAY_RE      = re.compile(r"^\d{1,2}$")                # "4" or "14"
DAY_NAME_RE = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$", re.IGNORECASE)
TIME_RE     = re.compile(r"^\d{2}:\d{2}$")         # "14:30"

_WEEKDAY: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# ==========================================
# MODAL
# ==========================================

class TimestampModal(discord.ui.Modal, title="Timestamp Converter"):
    """Popup form: day of month, 24h time, and a country for timezone."""

    day = discord.ui.TextInput(
        label="Day (number or name)",
        placeholder="e.g. 4 or Sunday",
        min_length=2, max_length=9,
    )
    time = discord.ui.TextInput(
        label="Time (24h, HH:MM)",
        placeholder="e.g. 14:30",
        min_length=4, max_length=5,
    )
    country = discord.ui.TextInput(
        label="Country / Timezone",
        placeholder="e.g. Turkey, Japan, US East …",
        default="Turkey",
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        day_raw  = self.day.value.strip()
        time_str = self.time.value.strip()
        country  = self.country.value.strip()

        # --- day: number or day name ---
        day_int: int | None = None
        if DAY_RE.match(day_raw):
            day_int = int(day_raw)
            if not (1 <= day_int <= 31):
                return await interaction.response.send_message(
                    "Day must be between 1 and 31.", ephemeral=True,
                )
        elif not DAY_NAME_RE.match(day_raw):
            return await interaction.response.send_message(
                "Day must be a number (1-31) or day name like Sunday, Mon, etc.",
                ephemeral=True,
            )

        # --- validate time ---
        if not TIME_RE.match(time_str):
            return await interaction.response.send_message(
                "Time must be HH:MM (24h), e.g. 14:30", ephemeral=True,
            )
        try:
            parsed_time = datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "Invalid time value.", ephemeral=True,
            )

        # --- timezone lookup ---
        offset = find_offset(country)
        if offset is None:
            return await interaction.response.send_message(
                "Unknown country. Try: Turkey, UK, US East, Japan, etc.",
                ephemeral=True,
            )
        tz = timezone(timedelta(hours=offset))

        # --- resolve month / year ---
        now = datetime.now(tz)
        if day_int is not None:
            # --- numeric day: same rollover logic as book ---
            if day_int < now.day:
                next_month = now.month + 1
                if next_month > 12:
                    month_int = 1
                    year_int  = now.year + 1
                else:
                    month_int = next_month
                    year_int  = now.year
            else:
                month_int = now.month
                year_int  = now.year
            # --- make sure the day exists in the resolved month ---
            max_days = cal_mod.monthrange(year_int, month_int)[1]
            if day_int > max_days:
                month_name = datetime(year_int, month_int, 1).strftime("%B")
                return await interaction.response.send_message(
                    f"Day {day_int} doesn't exist in {month_name} {year_int}.",
                    ephemeral=True,
                )
        else:
            # --- day name: find next occurrence of that weekday ---
            target_wd = _WEEKDAY[day_raw.lower()]
            today_wd  = now.weekday()
            days_ahead = (target_wd - today_wd) % 7
            target_date = now + timedelta(days=days_ahead)
            day_int   = target_date.day
            month_int = target_date.month
            year_int  = target_date.year

        # --- build datetime & unix timestamp ---
        local_dt = datetime(
            year_int, month_int, day_int,
            parsed_time.hour, parsed_time.minute,
        ).replace(tzinfo=tz)
        unix_ts = int(local_dt.astimezone(timezone.utc).timestamp())

        # --- output single Long Date+Time format ---
        lines = [
            f"**{local_dt.strftime('%d %B at %H:%M')}**",
            f"`<t:{unix_ts}:F>`　← Long Date+Time",
            "",
            "*Times display in each viewer's local timezone.*",
        ]

        await interaction.response.send_message(
            "\n".join(lines),
        )


# ==========================================
# COG
# ==========================================

class Timestamp(commands.Cog):
    """Slash command that opens the timestamp-converter modal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="timestamp", description="Convert a date+time into Discord timestamp markdown")
    async def timestamp_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TimestampModal())


# ==========================================
# setup
# ==========================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Timestamp(bot))
