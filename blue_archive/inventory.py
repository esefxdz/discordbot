#this is the inventory cog — interactive collection browser with buttons##
"""Interactive collection browser for the Blue Archive gacha system."""
######################################################################
import logging

import discord
from discord.ext import commands

from . import db as gacha_db
from .data import db
from .constants import (
    PAGE_SIZE, POOL_TOTALS, FILTER_LABELS,
    RARITY_SYMBOL, RARITY_LABEL, RARITY_EMBED_COLOR,
    ELIGMA_YIELD,
)

log = logging.getLogger(__name__)

# Local UI labels
PAGE_LEFT = "◀"
PAGE_RIGHT = "▶"
FILTER_ALL = "All"


def _bar(current: int, total: int, width: int = 8) -> str:
    """Unicode progress bar: ██████░░ 75%"""
    if total == 0:
        return "░" * width + " 0%"
    pct = min(current / total, 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled) + f" {int(pct * 100)}%"


class InventoryView(discord.ui.View):
    """Interactive view with buttons for collection browsing."""

    def __init__(self, uid: int, owner_id: int):
        super().__init__(timeout=120)
        self.uid = uid
        self.owner_id = owner_id
        self.page = 0
        self.filter_rarity: int | None = None
        self._update_buttons()

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_rows(self) -> list[tuple[int, str, int, int]]:
        """Return [(student_id, name, count, rarity), ...] filtered and sorted."""
        rows = gacha_db.get_inventory(self.uid)
        result = []
        for sid, count in rows:
            s = db.get(sid)
            if s:
                r = s["StarGrade"]
                if self.filter_rarity and r != self.filter_rarity:
                    continue
                result.append((sid, s["Name"], count, r))
        result.sort(key=lambda x: (-x[3], x[1]))
        return result

    def _build_dashboard(self) -> discord.Embed:
        """Landing page with stats and progress bars."""
        stats = gacha_db.get_inventory_stats(self.uid)
        eligma = gacha_db.get_eligma(self.uid)

        # Count by rarity
        rows = gacha_db.get_inventory(self.uid)
        by_rarity = {1: 0, 2: 0, 3: 0}
        for sid, _ in rows:
            s = db.get(sid)
            if s:
                by_rarity[s["StarGrade"]] = by_rarity.get(s["StarGrade"], 0) + 1

        embed = discord.Embed(
            title="📋 Collection Dashboard",
            description=f"**{stats['unique']}** unique students from **{stats['total_pulls']}** total pulls",
            color=0x5BA0D0,
        )

        # Progress bars per rarity
        for r in (3, 2, 1):
            pct = _bar(by_rarity[r], POOL_TOTALS[r])
            embed.add_field(
                name=f"{RARITY_LABEL[r]}  {by_rarity[r]}/{POOL_TOTALS[r]}",
                value=f"`{pct}`",
                inline=True,
            )

        embed.add_field(
            name="Eligma",
            value=f"**{eligma}**",
            inline=True,
        )

        embed.set_footer(text="Use buttons below to browse")
        return embed

    def _build_collection_page(self) -> discord.Embed:
        """Paginated student list."""
        all_rows = self._get_rows()
        total_pages = max(1, (len(all_rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, total_pages - 1)

        start = self.page * PAGE_SIZE
        chunk = all_rows[start:start + PAGE_SIZE]

        rarity_label = RARITY_LABEL.get(self.filter_rarity, "All")
        embed = discord.Embed(
            title=f"Collection — {rarity_label} ({len(all_rows)} students)",
            color=0x5BA0D0,
        )

        if not chunk:
            embed.description = "No students in this category yet. Use `!pull` to recruit!"
            return embed

        lines = []
        for sid, name, count, rarity in chunk:
            symbol = RARITY_SYMBOL[rarity]
            dupes = f" ×{count}" if count > 1 else ""
            lines.append(f"{symbol} {name}{dupes}")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages}")
        return embed

    def _build_eligma_page(self) -> discord.Embed:
        amount = gacha_db.get_eligma(self.uid)
        return discord.Embed(
            title="Eligma",
            description=(
                f"**Balance: {amount}**\n\n"
                "Earned from duplicates:\n"
                f"{RARITY_SYMBOL[1]} 1★ dupe = {ELIGMA_YIELD[1]} Eligma\n"
                f"{RARITY_SYMBOL[2]} 2★ dupe = {ELIGMA_YIELD[2]} Eligma\n"
                f"{RARITY_SYMBOL[3]} 3★ dupe = {ELIGMA_YIELD[3]} Eligma"
            ),
            color=0x5BA0D0,
        )

    # ── buttons ───────────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        self.clear_items()

        # Navigation row: dashboard, collection, eligma
        dash = discord.ui.Button(label="Dashboard", style=discord.ButtonStyle.gray, row=0, custom_id="dash")
        dash.callback = self._on_dash
        self.add_item(dash)

        coll = discord.ui.Button(label="Collection", style=discord.ButtonStyle.blurple, row=0, custom_id="coll")
        coll.callback = self._on_coll
        self.add_item(coll)

        elig = discord.ui.Button(label="Eligma", style=discord.ButtonStyle.green, row=0, custom_id="elig")
        elig.callback = self._on_elig
        self.add_item(elig)

        # Filter row: All, 3★, 2★, 1★
        for label in (FILTER_ALL, "3★", "2★", "1★"):
            style = discord.ButtonStyle.gray
            if FILTER_LABELS[label] == self.filter_rarity:
                style = discord.ButtonStyle.primary
            elif label == FILTER_ALL and self.filter_rarity is None:
                style = discord.ButtonStyle.primary
            btn = discord.ui.Button(label=label, style=style, row=1, custom_id=f"f_{label}")
            btn.callback = self._on_filter
            self.add_item(btn)

        # Page row
        left = discord.ui.Button(label=PAGE_LEFT, style=discord.ButtonStyle.gray, row=2, custom_id="prev")
        left.callback = self._on_prev
        self.add_item(left)

        right = discord.ui.Button(label=PAGE_RIGHT, style=discord.ButtonStyle.gray, row=2, custom_id="next")
        right.callback = self._on_next
        self.add_item(right)

    async def _on_dash(self, interaction: discord.Interaction) -> None:
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_dashboard(), view=self)

    async def _on_coll(self, interaction: discord.Interaction) -> None:
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_collection_page(), view=self)

    async def _on_elig(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_eligma_page(), view=self)

    async def _on_filter(self, interaction: discord.Interaction) -> None:
        label = interaction.data["custom_id"][2:]  # strip "f_"
        self.filter_rarity = FILTER_LABELS.get(label)
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_collection_page(), view=self)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_collection_page(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        all_rows = self._get_rows()
        max_page = max(0, (len(all_rows) - 1) // PAGE_SIZE)
        self.page = min(max_page, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_collection_page(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use the buttons. Run `!inv` yourself!",
                ephemeral=True,
            )
            return False
        return True


class Inventory(commands.Cog):
    """Interactive collection browser for Blue Archive gacha."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.group(name="inv", aliases=["collection", "i"], invoke_without_command=True)
    async def inventory(self, ctx: commands.Context, *, target: str = "") -> None:
        """Open your interactive collection dashboard.

        Usage: !inv         — your collection with buttons
               !inv @user   — someone else's (read-only)
        """
        if target and target.startswith("<@"):
            try:
                uid = int(target.strip("<@!>"))
            except ValueError:
                uid = ctx.author.id
        else:
            uid = ctx.author.id

        rows = gacha_db.get_inventory(uid)
        if not rows:
            if uid == ctx.author.id:
                await ctx.reply("Your collection is empty. Use `!pull` to recruit students!")
            else:
                await ctx.reply("This user's collection is empty.")
            return

        view = InventoryView(uid, ctx.author.id)
        await ctx.reply(embed=view._build_dashboard(), view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Inventory(bot))
