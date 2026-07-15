"""Calendar cog — modal-based booking, Firestore Admin SDK."""
######################################################################
import calendar
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from firebase_website import get_db
from .calendar_data import COUNTRY_TZ

log = logging.getLogger(__name__)

DAY_RE  = re.compile(r"^\d{1,2}$")   # 1–31
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


# ==========================================
# BOOK MODAL
# ==========================================

class BookModal(discord.ui.Modal, title="Book an Event"):
    """Popup form for booking calendar events."""

    day = discord.ui.TextInput(
        label="Day of month",
        placeholder="e.g. 4",
        min_length=1, max_length=2,
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
        day_raw     = self.day.value.strip()
        time_str    = self.time.value.strip()
        title       = self.event_title.value.strip()
        country     = self.country.value.strip()
        description = self.description.value.strip()

        # --- validate day ---
        if not DAY_RE.match(day_raw):
            return await interaction.response.send_message(
                "Day must be a number, e.g. 4 or 14.", ephemeral=True
            )
        day_int = int(day_raw)
        if not (1 <= day_int <= 31):
            return await interaction.response.send_message(
                "Day must be between 1 and 31.", ephemeral=True
            )

        # --- validate time ---
        if not TIME_RE.match(time_str):
            return await interaction.response.send_message(
                "Time must be HH:MM (24h), e.g. 14:30", ephemeral=True
            )
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "Invalid time value.", ephemeral=True
            )

        # --- resolve month / year with rollover ---
        # Rule: day < today  →  next month
        #       day >= today →  this month (includes booking same day)
        now = datetime.now(TR_TZ)
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
        max_days = calendar.monthrange(year_int, month_int)[1]
        if day_int > max_days:
            month_name = datetime(year_int, month_int, 1).strftime("%B")
            return await interaction.response.send_message(
                f"Day {day_int} doesn't exist in {month_name} {year_int}.",
                ephemeral=True,
            )

        # --- timezone lookup ---
        offset = _find_offset(country)
        if offset is None:
            return await interaction.response.send_message(
                "Unknown country. Try: Turkey, UK, US East, Japan, etc.",
                ephemeral=True,
            )

        # --- build datetime & UTC timestamp ---
        tz = timezone(timedelta(hours=offset))
        local_dt = datetime(
            year_int, month_int, day_int,
            *map(int, time_str.split(":")),
        ).replace(tzinfo=tz)
        utc_ts = local_dt.astimezone(timezone.utc).isoformat()

        day_str      = f"{day_int:02d}"
        month_str    = f"{month_int:02d}"
        year_str     = str(year_int)
        date_display = f"{day_str}-{month_str}-{year_str}"

        event_data = {
            "title":       title,
            "date":        f"{year_str}-{month_str}-{day_str}",
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

        desc = f" \u2014 {description}" if description else ""
        msg = f"Booked **{date_display} at {time_str} ({country})**: **{title}**{desc}"
        await interaction.response.send_message(msg, ephemeral=True)

        cog = interaction.client.get_cog("Calendar")
        if cog:
            await cog._send_text(interaction.channel, msg)


# ==========================================
# UNBOOK DROPDOWN
# ==========================================

class UnbookSelect(discord.ui.Select):
    """Dropdown of upcoming events — pick one to delete."""

    def __init__(self, events: list[dict]) -> None:
        self._events = {ev["doc_id"]: ev for ev in events}
        super().__init__(
            placeholder="Pick an event to remove…",
            options=[
                discord.SelectOption(
                    label=f"{ev['date_display']} {ev['time']} — {ev['title']}"[:100],
                    description=f"{ev['country']} — {ev['createdBy']}"[:100],
                    value=ev["doc_id"],
                )
                for ev in events[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        doc_id = self.values[0]
        ev = self._events[doc_id]

        try:
            db = get_db()
            await db.collection(COLLECTION).document(doc_id).delete()
        except Exception:
            log.exception("Failed to delete calendar event")
            return await interaction.response.send_message(
                "Failed to remove. Try again.", ephemeral=True
            )

        msg = f"Removed **{ev['title']}** from {ev['date_display']}."
        await interaction.response.send_message(msg, ephemeral=True)

        cog = interaction.client.get_cog("Calendar")
        if cog:
            await cog._send_text(interaction.channel, msg)


class UnbookView(discord.ui.View):
    def __init__(self, events: list[dict]) -> None:
        super().__init__(timeout=60)
        self.add_item(UnbookSelect(events))


# ==========================================
# BOOK BUTTON
# ==========================================

class BookButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Book an Event", style=discord.ButtonStyle.primary, emoji="📅")
    async def open_modal(self, interaction: discord.Interaction, _button):
        await interaction.response.send_modal(BookModal())


# ==========================================
# CALENDAR COG
# ==========================================

class Calendar(commands.Cog):
    """Book events via a popup form — restricted to authorized users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        raw = os.getenv("CALENDAR_USERS", "")
        self._allowed: set[int] = {
            int(uid.strip()) for uid in raw.split(",") if uid.strip()
        }

        # webhook — same pattern as ai_roleplay
        self._webhooks: dict[int, discord.Webhook] = {}
        self._wh_avatar_set: set[int] = set()

        avatar_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "calendar_asset", "webhook_agent_calendar_pfp.jpg",
        )
        self._avatar_bytes: bytes | None = None
        if os.path.exists(avatar_path):
            with open(avatar_path, "rb") as f:
                self._avatar_bytes = f.read()
        else:
            log.warning("Calendar webhook avatar not found at %s", avatar_path)

    def _check(self, user_id: int) -> bool:
        return user_id in self._allowed

    # ------------------------------------------------------------------
    # webhook plumbing
    # ------------------------------------------------------------------

    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        """Return (or create) the cached koharu webhook for *channel*."""
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

    async def _send_text(self, channel: discord.TextChannel, content: str) -> bool:
        """Post *content* through koharu. Returns True on success."""
        wh = await self._get_webhook(channel)
        if wh is None:
            return False

        for _ in range(2):
            try:
                if channel.id not in self._wh_avatar_set and self._avatar_bytes:
                    await wh.edit(avatar=self._avatar_bytes)
                    self._wh_avatar_set.add(channel.id)
                await wh.send(content=content, username=WH_NAME)
                return True
            except discord.NotFound:
                self._webhooks.pop(channel.id, None)
                self._wh_avatar_set.discard(channel.id)
                wh = await self._get_webhook(channel)
                if wh is None:
                    return False
        return False

    async def _send_view(self, ctx: commands.Context, content: str, view: discord.ui.View) -> None:
        """Send a message with a view through koharu; fall back to ctx.reply."""
        wh = await self._get_webhook(ctx.channel)
        if wh:
            try:
                await wh.send(content=content, view=view, username=WH_NAME)
                return
            except discord.NotFound:
                self._webhooks.pop(ctx.channel.id, None)
                self._wh_avatar_set.discard(ctx.channel.id)
        await ctx.reply(content, view=view)

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    @commands.command(name="book")
    async def book_cmd(self, ctx: commands.Context) -> None:
        """Send a button that opens the booking form."""
        if not self._check(ctx.author.id):
            return await ctx.reply("You are not a maomao member.", delete_after=5)
        await self._send_view(ctx, "Click below to book an event:", BookButton())

    @commands.command(name="unbook")
    async def unbook_cmd(self, ctx: commands.Context) -> None:
        """Open a dropdown to pick and remove an upcoming event."""
        if not self._check(ctx.author.id):
            return await ctx.reply("You don't have access to the calendar.", delete_after=5)
        await self._unbook_dropdown(ctx)

    async def _unbook_dropdown(self, ctx: commands.Context) -> None:
        """Fetch future events and present a selection dropdown."""
        try:
            db = get_db()
            all_docs = await db.collection(COLLECTION).order_by("date").get()
        except Exception:
            log.exception("Failed to fetch calendar events")
            return await ctx.reply("Could not fetch events.")

        now = datetime.now(timezone.utc)
        events: list[dict] = []

        for doc in all_docs:
            data = doc.to_dict()
            try:
                ev_dt = datetime.fromisoformat(data.get("utc", ""))
            except (ValueError, TypeError):
                continue
            if ev_dt <= now:
                continue
            y, m, d = data["date"].split("-")
            events.append({
                "doc_id":       doc.id,
                "title":        data.get("title", ""),
                "date_display": f"{d}-{m}-{y}",
                "time":         data.get("time", ""),
                "country":      data.get("country", ""),
                "createdBy":    data.get("createdBy", ""),
            })

        if not events:
            msg = "📭 No upcoming events."
            if not await self._send_text(ctx.channel, msg):
                await ctx.reply(msg)
            return

        await self._send_view(ctx, "Pick an event to remove:", UnbookView(events))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Calendar(bot))
