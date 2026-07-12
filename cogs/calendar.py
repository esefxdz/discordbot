"""Calendar cog — book events via Discord slash commands, displayed on the website."""
######################################################################
import logging
import re
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

FIRESTORE_CALENDAR = (
    "https://firestore.googleapis.com/v1/projects/esef-514bf"
    "/databases/(default)/documents/calendar_events"
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class Calendar(commands.Cog):
    """Book and manage calendar events from Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Sync slash commands when the cog loads."""
        try:
            await self.bot.tree.sync()
            log.info("Calendar slash commands synced")
        except Exception:
            log.warning("Failed to sync slash commands", exc_info=True)

    # ── /book ────────────────────────────────────────────────────────────

    @app_commands.command(name="book", description="Add an event to the calendar")
    @app_commands.describe(
        date="Date in YYYY-MM-DD format (e.g. 2026-07-20)",
        time="Time in HH:MM 24-hour format (optional, e.g. 14:30)",
        title="Event title",
        description="Extra notes (optional)",
    )
    async def book_cmd(
        self,
        interaction: discord.Interaction,
        date: str,
        title: str,
        time: str = "",
        description: str = "",
    ) -> None:
        """Slash command: /book <date> <title> [time] [description]"""
        await interaction.response.defer()

        if not DATE_RE.match(date):
            return await interaction.followup.send(
                "❌ Invalid date. Use `YYYY-MM-DD`, e.g. `2026-07-20`."
            )
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return await interaction.followup.send("❌ That date doesn't exist.")

        if time and not TIME_RE.match(time):
            return await interaction.followup.send("❌ Time must be `HH:MM` (24-hour), e.g. `14:30`.")
        if time:
            try:
                datetime.strptime(time, "%H:%M")
            except ValueError:
                return await interaction.followup.send("❌ Invalid time.")

        payload = {
            "fields": {
                "title":       {"stringValue": title.strip()},
                "date":        {"stringValue": date},
                "time":        {"stringValue": time},
                "description": {"stringValue": description.strip()},
                "createdBy":   {"stringValue": str(interaction.user)},
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(FIRESTORE_CALENDAR, json=payload) as resp:
                if resp.status in (200, 201):
                    when = date
                    if time:
                        when += f" at {time}"
                    desc = f" — {description}" if description else ""
                    await interaction.followup.send(
                        f"📅 Booked **{when}**: **{title}**{desc}"
                    )
                else:
                    body = await resp.text()
                    log.warning("Firestore POST failed %d: %s", resp.status, body[:200])
                    await interaction.followup.send("❌ Failed to save event.")

    # ── /unbook ──────────────────────────────────────────────────────────

    @app_commands.command(name="unbook", description="Remove an event from the calendar")
    @app_commands.describe(
        date="Date of the event (YYYY-MM-DD)",
        title="Exact title of the event to remove",
    )
    async def unbook_cmd(
        self,
        interaction: discord.Interaction,
        date: str,
        title: str,
    ) -> None:
        """Slash command: /unbook <date> <title>"""
        await interaction.response.defer()

        if not DATE_RE.match(date) or not title.strip():
            return await interaction.followup.send(
                "❌ Usage: `/unbook YYYY-MM-DD Title`"
            )

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
                if d == date and t.lower() == title.strip().lower():
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
