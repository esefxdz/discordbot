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

WH_NAME = "koharu"


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
        msg = f"Booked **{date} at {time_str} ({country})**: **{title}**{desc}"
        await interaction.response.send_message(msg, ephemeral=True)

        cog = interaction.client.get_cog("Calendar")
        if cog:
            await cog._send_via_webhook(interaction.channel, msg)


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

        # --- webhook plumbing (same pattern as ai_roleplay) ---
        self._webhooks: dict[int, discord.Webhook] = {}
        self._wh_avatar_set: set[int] = set()

        avatar_dir = os.path.dirname(os.path.dirname(__file__))
        avatar_path = os.path.join(avatar_dir, "calendar_asset", "webhook_agent_calendar_pfp.jpg")
        self._avatar_bytes: bytes | None = None
        if os.path.exists(avatar_path):
            with open(avatar_path, "rb") as f:
                self._avatar_bytes = f.read()
        else:
            log.warning("Calendar webhook avatar not found at %s", avatar_path)

    def _check(self, user_id: int) -> bool:
        return user_id in self._allowed

    # ------------------------------------------------------------------
    # webhook helpers (mirrors ai_roleplay.get_webhook / send_as_char)
    # ------------------------------------------------------------------

    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        """Return (or create) the 'koharu' webhook for *channel*, with avatar."""
        if channel.id in self._webhooks:
            return self._webhooks[channel.id]

        try:
            whs = await channel.webhooks()
            wh = next((w for w in whs if w.name == WH_NAME), None)
            if not wh:
                wh = await channel.create_webhook(name=WH_NAME, avatar=self._avatar_bytes)
            self._webhooks[channel.id] = wh
            self._wh_avatar_set.add(channel.id)
            return wh
        except discord.Forbidden:
            return None

    async def _send_via_webhook(self, channel: discord.TextChannel, content: str) -> bool:
        """Send *content* through the koharu webhook. Returns True on success."""
        wh = await self._get_webhook(channel)
        if wh is None:
            return False

        for attempt in range(2):
            try:
                if channel.id not in self._wh_avatar_set and self._avatar_bytes:
                    await wh.edit(avatar=self._avatar_bytes)
                    self._wh_avatar_set.add(channel.id)
                await wh.send(content=content, username=WH_NAME)
                return True
            except discord.NotFound:
                # webhook deleted externally — purge and recreate once
                self._webhooks.pop(channel.id, None)
                self._wh_avatar_set.discard(channel.id)
                wh = await self._get_webhook(channel)
                if wh is None:
                    return False
        return False

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    @commands.command(name="book")
    async def book_cmd(self, ctx: commands.Context) -> None:
        """Send a button that opens the booking form."""
        if not self._check(ctx.author.id):
            return await ctx.reply("You are not a maomao member.", delete_after=5)

        wh = await self._get_webhook(ctx.channel)
        if wh:
            try:
                await wh.send(content="Click below to book an event:", view=BookButton(), username=WH_NAME)
                return
            except discord.NotFound:
                self._webhooks.pop(ctx.channel.id, None)
                self._wh_avatar_set.discard(ctx.channel.id)

        # fallback
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
            msg = f"Removed **{title}** from {date}."
        else:
            msg = f"No event **{title}** found on {date}."

        if not await self._send_via_webhook(ctx.channel, msg):
            await ctx.reply(msg)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Calendar(bot))
