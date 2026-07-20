"""Bucketlist cog — modal-based bucket list, Firestore Admin SDK."""
######################################################################
import hashlib
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands

from firebase_website import get_db

log = logging.getLogger(__name__)

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ==========================================
# BUCKET MODAL
# ==========================================

class BucketModal(discord.ui.Modal, title="Add to Bucketlist"):
    """Popup form for adding a bucketlist item."""

    item_title = discord.ui.TextInput(
        label="Title",
        placeholder="Visit Japan",
        max_length=80,
    )
    description = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="Explore Tokyo, eat real ramen...",
        max_length=200,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.item_title.value.strip()
        description = self.description.value.strip()

        now = datetime.now(timezone.utc)
        utc_ts = now.isoformat()
        month_name = MONTHS[now.month - 1]
        date_display = f"Added {month_name} {now.year}"

        doc = {
            "title":       title,
            "description": description,
            "date":        date_display,
            "utc":         utc_ts,
            "done":        False,
            "createdBy":   str(interaction.user),
        }

        cog = interaction.client.get_cog("Bucketlist")
        if cog is None:
            return await interaction.response.send_message(
                "Cog not ready. Try again.", ephemeral=True
            )

        try:
            db = get_db()
            await cog._items_ref(db).add(doc)
        except Exception:
            log.exception("Failed to add bucketlist item")
            return await interaction.response.send_message(
                "Failed to save. Try again.", ephemeral=True
            )

        desc = f" — {description}" if description else ""
        await interaction.response.send_message(
            f"Added **{title}**{desc}\nhttps://esefos.netlify.app/bucketlist",
            ephemeral=True,
        )


# ==========================================
# UNBUCKET DROPDOWN
# ==========================================

class UnbucketSelect(discord.ui.Select):
    """Dropdown of bucketlist items — pick one to delete."""

    def __init__(self, items: list[dict], cog) -> None:
        self._items = {it["doc_id"]: it for it in items}
        self._cog = cog
        super().__init__(
            placeholder="Pick an item to remove…",
            options=[
                discord.SelectOption(
                    label=f"{it['title']}"[:100],
                    description=f"{it['date']} — {it['createdBy']}"[:100],
                    value=it["doc_id"],
                )
                for it in items[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        doc_id = self.values[0]
        it = self._items[doc_id]

        try:
            db = get_db()
            await self._cog._items_ref(db).document(doc_id).delete()
        except Exception:
            log.exception("Failed to delete bucketlist item")
            return await interaction.response.send_message(
                "Failed to remove. Try again.", ephemeral=True
            )

        await interaction.response.send_message(
            f"Removed **{it['title']}** from the bucketlist.", ephemeral=True
        )


class UnbucketView(discord.ui.View):
    def __init__(self, items: list[dict], cog) -> None:
        super().__init__(timeout=60)
        self.add_item(UnbucketSelect(items, cog))


# ==========================================
# BUCKET BUTTON
# ==========================================

class BucketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Add Item", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_modal(self, interaction: discord.Interaction, _button):
        await interaction.response.send_modal(BucketModal())


# ==========================================
# BUCKETLIST COG
# ==========================================

class Bucketlist(commands.Cog):
    """Add/remove bucketlist items via a popup form — restricted to authorized users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # build the subcollection path key from credentials
        bl_user = os.getenv("BUCKETLIST_USER", "")
        bl_pass = os.getenv("BUCKETLIST_PASS", "")
        combined = f"{bl_user}:{bl_pass}"
        self._hash = hashlib.sha256(combined.encode()).hexdigest()

    def _items_ref(self, db):
        """Return the Firestore subcollection ref gated behind the credential hash."""
        return db.collection("bucketlist_data").document(self._hash).collection("items")

    #this command lists all the bucketlist commands, not an actual bucketlist command##
    @commands.command()
    async def bucketlist(self, ctx):
        bucketlist_commands = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(bucketlist_commands))
    ######################################################################

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    @commands.command(name="bucket")
    async def bucket_cmd(self, ctx: commands.Context) -> None:
        """Send a button that opens the add-item form."""
        await ctx.reply("Click below to add an item:", view=BucketButton())

    @commands.command(name="unbucket")
    async def unbucket_cmd(self, ctx: commands.Context) -> None:
        """Open a dropdown to pick and remove a bucketlist item."""
        await self._unbucket_dropdown(ctx)

    async def _unbucket_dropdown(self, ctx: commands.Context) -> None:
        """Fetch all bucketlist items and present a selection dropdown."""
        try:
            db = get_db()
            all_docs = await self._items_ref(db).order_by("utc").get()
        except Exception:
            log.exception("Failed to fetch bucketlist items")
            return await ctx.reply("Could not fetch items.")

        items: list[dict] = []

        for doc in all_docs:
            data = doc.to_dict()
            items.append({
                "doc_id":    doc.id,
                "title":     data.get("title", ""),
                "date":      data.get("date", ""),
                "createdBy": data.get("createdBy", ""),
            })

        if not items:
            return await ctx.reply("📭 Nothing in the bucketlist yet.")

        await ctx.reply("Pick an item to remove:", view=UnbucketView(items, self))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Bucketlist(bot))
