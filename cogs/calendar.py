"""Calendar cog — modal-based booking, country-aware timezones."""
######################################################################
import logging
import re
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .calendar_data import COUNTRY_TZ

log = logging.getLogger(__name__)

FIRESTORE_CALENDAR = (
    "https://firestore.googleapis.com/v1/projects/esef-514bf"
    "/databases/(default)/documents/calendar_events"
)

DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# Default timezone: South Africa (UTC+2)
DEFAULT_COUNTRY = "South Africa"
DEFAULT_TITLE = "Demo Review"


def _find_offset(country: str) -> float | None:
    """Look up a country's UTC offset. Case-insensitive fuzzy match."""
    c = country.strip().lower()
    if not c:
        return None
    # Exact match
    for name, offset in COUNTRY_TZ.items():
        if c == name.lower():
            return offset
    # Starts with
    for name, offset in COUNTRY_TZ.items():
        if name.lower().startswith(c):
            return offset
    # Substring anywhere
    for name, offset in COUNTRY_TZ.items():
        if c in name.lower():
            return offset
    return None


class BookModal(discord.ui.Modal, title="Book an Event"):
    """Popup form for booking calendar events."""

    date = discord.ui.TextInput(
        label="Date (DD-MM-YYYY)",
        placeholder="20-07-2026",
        min_length=10, max_length=10,
    )
    time = discord.ui.TextInput(
        label="Time (24h, e.g. 14:30)",
        placeholder="14:30",
        min_length=5, max_length=5,
    )
    title = discord.ui.TextInput(
        label="Title",
        placeholder="Demo Review",
        default="Demo Review",
        max_length=80,
    )
    country = discord.ui.TextInput(
        label="Country / Timezone",
        placeholder="South Africa",
        default="South Africa",
        max_length=30,
    )
    description = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="Notes...",
        max_length=200, required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        date = self.date.value.strip()
        time_str = self.time.value.strip()
        title = self.title.value.strip()
        country = self.country.value.strip()
        description = self.description.value.strip()

        if not DATE_RE.match(date):
            return await interaction.response.send_message(
                "❌ Date must be `DD-MM-YYYY`, e.g. `20-07-2026`", ephemeral=True
            )
        if not TIME_RE.match(time_str):
            return await interaction.response.send_message(
                "❌ Time must be `HH:MM` (24h), e.g. `14:30`", ephemeral=True
            )
        try:
            day, month, year = date.split("-")
            datetime(int(year), int(month), int(day))
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "❌ That date doesn't exist.", ephemeral=True
            )

        offset = _find_offset(country)
        if offset is None:
            return await interaction.response.send_message(
                f"❌ Unknown country. Try: Turkey, UK, US East, Japan, etc.\n"
                f"Type a country name from the list.",
                ephemeral=True,
            )

        tz = timezone(timedelta(hours=offset))
        local_dt = datetime(
            int(year), int(month), int(day),
            *map(int, time_str.split(":")),
        ).replace(tzinfo=tz)
        utc_ts = local_dt.astimezone(timezone.utc).isoformat()

        payload = {
            "fields": {
                "title":       {"stringValue": title},
                "date":        {"stringValue": f"{year}-{month}-{day}"},
                "time":        {"stringValue": time_str},
                "country":     {"stringValue": country},
                "utc":         {"stringValue": utc_ts},
                "description": {"stringValue": description},
                "createdBy":   {"stringValue": str(interaction.user)},
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(FIRESTORE_CALENDAR, json=payload) as resp:
                if resp.status in (200, 201):
                    desc = f" — {description}" if description else ""
                    await interaction.response.send_message(
                        f"📅 Booked **{date} at {time_str} ({country})**: **{title}**{desc}"
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Failed to save. Try again.", ephemeral=True
                    )


class Calendar(commands.Cog):
    """Book events via a popup form."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="book", description="Open a form to add a calendar event")
    async def book_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BookModal())

    @app_commands.command(name="unbook", description="Remove an event")
    @app_commands.describe(date="Date (DD-MM-YYYY)", title="Exact title to remove")
    async def unbook_cmd(self, interaction: discord.Interaction, date: str, title: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not DATE_RE.match(date) or not title.strip():
            return await interaction.followup.send("❌ Usage: `/unbook date:20-07-2026 title:Demo Review`")

        day, month, year = date.split("-")
        iso_date = f"{year}-{month}-{day}"

        deleted = False
        async with aiohttp.ClientSession() as session:
            async with session.get(FIRESTORE_CALENDAR) as resp:
                if resp.status != 200:
                    return await interaction.followup.send("❌ Could not fetch events.")
                data = await resp.json()

            for doc in data.get("documents", []):
                fields = doc.get("fields", {})
                d = fields.get("date", {}).get("stringValue", "")
                t = fields.get("title", {}).get("stringValue", "")
                if d == iso_date and t.lower() == title.strip().lower():
                    doc_name = doc["name"].split("/")[-1]
                    async with session.delete(f"{FIRESTORE_CALENDAR}/{doc_name}") as dresp:
                        if dresp.status == 200:
                            deleted = True
                            break

        if deleted:
            await interaction.followup.send(f"🗑️ Removed **{title}** from {date}.")
        else:
            await interaction.followup.send(f"❌ No event **{title}** found on {date}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Calendar(bot))
