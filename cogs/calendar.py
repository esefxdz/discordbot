"""Calendar cog — modal-based booking, Firestore Admin SDK."""
######################################################################
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from firebase_website import get_db
from .calendar_data import COUNTRY_TZ

log = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

COLLECTION = "calendar_events"
TR_TZ = timezone(timedelta(hours=3))


def _find_offset(country: str) -> float | None:
    """Look up a country's UTC offset. Case-insensitive fuzzy match."""
    c = country.strip().lower()
    if not c:
        return None
    for name, offset in COUNTRY_TZ.items():
        if c == name.lower():
            return offset
    for name, offset in COUNTRY_TZ.items():
        if name.lower().startswith(c):
            return offset
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
    event_title = discord.ui.TextInput(
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
        title = self.event_title.value.strip()
        country = self.country.value.strip()
        description = self.description.value.strip()

        if not DATE_RE.match(date):
            return await interaction.response.send_message(
                "Date must be DD-MM-YYYY, e.g. 20-07-2026", ephemeral=True
            )
        if not TIME_RE.match(time_str):
            return await interaction.response.send_message(
                "Time must be HH:MM (24h), e.g. 14:30", ephemeral=True
            )
        try:
            day, month, year = date.split("-")
            datetime(int(year), int(month), int(day))
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "That date doesn't exist.", ephemeral=True
            )

        offset = _find_offset(country)
        if offset is None:
            return await interaction.response.send_message(
                "Unknown country. Try: Turkey, UK, US East, Japan, etc.",
                ephemeral=True,
            )

        tz = timezone(timedelta(hours=offset))
        local_dt = datetime(
            int(year), int(month), int(day),
            *map(int, time_str.split(":")),
        ).replace(tzinfo=tz)
        utc_ts = local_dt.astimezone(timezone.utc).isoformat()

        event_data = {
            "title":       title,
            "date":        f"{year}-{month}-{day}",
            "time":        time_str,
            "country":     country,
            "utc":         utc_ts,
            "description": description,
            "createdBy":   str(interaction.user),
        }

        try:
            db = get_db()
            await db.collection(COLLECTION).add(event_data)
        except Exception:
            log.exception("Failed to add calendar event")
            return await interaction.response.send_message(
                "Failed to save. Try again.", ephemeral=True
            )

        desc = f" — {description}" if description else ""
        await interaction.response.send_message(
            f"Booked **{date} at {time_str} ({country})**: **{title}**{desc}", delete_after=10) 


class BookButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Book an Event", style=discord.ButtonStyle.primary, emoji="📅")
    async def open_modal(self, interaction: discord.Interaction, _button):
        await interaction.response.send_modal(BookModal())


class Calendar(commands.Cog):
    """Book events via a popup form — restricted to authorized users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        raw = os.getenv("CALENDAR_USERS", "")
        self._allowed: set[int] = {
            int(uid.strip()) for uid in raw.split(",") if uid.strip()
        }

    def _check(self, user_id: int) -> bool:
        return user_id in self._allowed

    @commands.command(name="book")
    async def book_cmd(self, ctx: commands.Context) -> None:
        """Send a button that opens the booking form."""
        if not self._check(ctx.author.id):
            return await ctx.reply("You are not a maomao member.", delete_after=5)
        await ctx.reply("Click below to book an event:", view=BookButton())

    @commands.command(name="unbook")
    async def unbook_cmd(self, ctx: commands.Context, date: str = "", *, title: str = "") -> None:
        """Remove an event. Usage: !unbook DD-MM-YYYY Title"""
        if not self._check(ctx.author.id):
            return await ctx.reply("You don't have access to the calendar.", delete_after=5)
        if not DATE_RE.match(date) or not title.strip():
            return await ctx.reply("Usage: `!unbook 20-07-2026 Demo Review`")

        day, month, year = date.split("-")
        iso_date = f"{year}-{month}-{day}"

        try:
            db = get_db()
            docs = (
                await db.collection(COLLECTION)
                .where("date", "==", iso_date)
                .where("title", "==", title.strip())
                .get()
            )
        except Exception:
            log.exception("Failed to query calendar events")
            return await ctx.reply("Could not fetch events.")

        deleted = False
        for doc in docs:
            await doc.reference.delete()
            deleted = True

        if deleted:
            await ctx.reply(f"Removed **{title}** from {date}.")
        else:
            await ctx.reply(f"No event **{title}** found on {date}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Calendar(bot))
