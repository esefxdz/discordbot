#this is the inventory cog for viewing collections and eligma balance##
"""Collection and eligma commands for the Blue Archive gacha system."""
######################################################################
import logging

import discord
from discord.ext import commands

from . import db as gacha_db
from .data import db

log = logging.getLogger(__name__)

ELIGMA_YIELD = {1: 1, 2: 10, 3: 50}


class Inventory(commands.Cog):
    """View your student collection and eligma from gacha pulls."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.group(name="inv", aliases=["collection", "i"], invoke_without_command=True)
    async def inventory(self, ctx: commands.Context, *, target: str = "") -> None:
        """View your student collection grouped by rarity.

        Usage:
            !inv              — your collection
            !inv @user        — someone else's
            !inv 3star        — filter to 3★ only
            !inv stats        — summary stats
        """
        # Determine whose collection to show
        if target and target.startswith("<@"):
            try:
                uid = int(target.strip("<@!>"))
            except ValueError:
                uid = ctx.author.id
        else:
            uid = ctx.author.id

        filter_rarity = None
        if target in ("3star", "3", "3*", "3star"):
            filter_rarity = 3
        elif target in ("2star", "2", "2*", "2star"):
            filter_rarity = 2
        elif target in ("1star", "1", "1*", "1star"):
            filter_rarity = 1

        rows = gacha_db.get_inventory(uid)
        if not rows:
            if uid == ctx.author.id:
                await ctx.reply("Your collection is empty. Use `!pull` to recruit students!")
            else:
                await ctx.reply("This user's collection is empty.")
            return

        # Group by rarity
        by_rarity: dict[int, list[tuple[int, str, int]]] = {3: [], 2: [], 1: []}
        for sid, count in rows:
            s = db.get(sid)
            if s:
                r = s["StarGrade"]
                by_rarity.get(r, []).append((sid, s["Name"], count))

        # Build embed
        title = f"Collection of {self.bot.get_user(uid).display_name if self.bot.get_user(uid) else 'User'}"
        embed = discord.Embed(title=title, color=0x5BA0D0)

        for rarity, label, em_color in [
            (3, "3-star", 0xF0C0FF),
            (2, "2-star", 0xFFF588),
            (1, "1-star", 0xAADCFF),
        ]:
            if filter_rarity and rarity != filter_rarity:
                continue
            chars = sorted(by_rarity[rarity], key=lambda x: -x[2])
            if not chars:
                continue
            total_unique = len(chars)
            total_dupes = sum(c[2] - 1 for c in chars)
            header = f"{label} — {total_unique} unique"
            if total_dupes:
                header += f" (+{total_dupes} dupes)"
            
            # Format: "Aru x3, Hina x1, ..."
            lines = []
            for _, name, count in chars:
                lines.append(f"{name}" + (f" x{count}" if count > 1 else ""))
            
            # Split into chunks of ~10 per embed field line
            text = ", ".join(lines)
            if len(text) > 1024:
                text = text[:1021] + "..."
            embed.add_field(name=header, value=text, inline=False)

        # Footer stats
        stats = gacha_db.get_inventory_stats(uid)
        eligma = gacha_db.get_eligma(uid)
        embed.set_footer(
            text=f"{stats['unique']} unique · {stats['total_pulls']} total pulls · {eligma} Eligma"
        )

        await ctx.reply(embed=embed)

    @inventory.command(name="stats")
    async def inv_stats(self, ctx: commands.Context) -> None:
        """Show your collection summary stats."""
        uid = ctx.author.id
        stats = gacha_db.get_inventory_stats(uid)
        eligma = gacha_db.get_eligma(uid)

        # Count by rarity
        rows = gacha_db.get_inventory(uid)
        by_rarity = {1: 0, 2: 0, 3: 0}
        for sid, _ in rows:
            s = db.get(sid)
            if s:
                by_rarity[s["StarGrade"]] = by_rarity.get(s["StarGrade"], 0) + 1

        embed = discord.Embed(title="Collection Stats", color=0x5BA0D0)
        embed.add_field(name="Total Pulls", value=str(stats["total_pulls"]), inline=True)
        embed.add_field(name="Unique Students", value=str(stats["unique"]), inline=True)
        embed.add_field(name="Eligma", value=str(eligma), inline=True)
        embed.add_field(
            name="By Rarity",
            value=f"3★: {by_rarity[3]} · 2★: {by_rarity[2]} · 1★: {by_rarity[1]}",
            inline=False,
        )

        await ctx.reply(embed=embed)

    @commands.command(name="eligma", aliases=["elig"])
    async def eligma(self, ctx: commands.Context) -> None:
        """Show your eligma balance and explain how it works."""
        uid = ctx.author.id
        amount = gacha_db.get_eligma(uid)

        embed = discord.Embed(
            title="Eligma",
            description=(
                f"**Balance: {amount}**\n\n"
                "Eligma is earned from duplicate pulls:\n"
                f"1★ dupe = {ELIGMA_YIELD[1]} Eligma\n"
                f"2★ dupe = {ELIGMA_YIELD[2]} Eligma\n"
                f"3★ dupe = {ELIGMA_YIELD[3]} Eligma\n\n"
                "In the real game, Eligma is used to buy student shards "
                "in the shop. (Shop coming soon.)"
            ),
            color=0x5BA0D0,
        )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Inventory(bot))
