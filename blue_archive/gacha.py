#this is the gacha cog for blue archive, handles all recruitment commands##
"""Blue Archive Gacha Simulator — in-game-accurate recruitment with live banners."""
######################################################################
import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from .data import (
    db,
    fetch_banners,
    format_banner_embed,
    roll_rarity,
    get_rates_for_banner,
    PULL10_RATES,
    DEFAULT_RATES,
)
from .gacha_renderer import render_pull

log = logging.getLogger(__name__)

STATE_FILE = Path("data/ba_gacha_state.json")
SPARK_TARGET = 200
_state_lock = asyncio.Lock()


def _load_state() -> dict:
    """Load per-user gacha state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    """Persist per-user gacha state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


async def _get_user_state(user_id: int) -> dict:
    async with _state_lock:
        state = _load_state()
    key = str(user_id)
    if key not in state:
        state[key] = {"banner_id": None, "spark": 0, "history": []}
    return state[key]


async def _set_user_state(user_id: int, user_state: dict) -> None:
    async with _state_lock:
        state = _load_state()
        state[str(user_id)] = user_state
        _save_state(state)


class BlueArchiveGacha(commands.Cog):
    """Blue Archive recruitment simulator with live banners and in-game-style renders."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._banner_cache: dict = {"current": [], "upcoming": [], "ended": []}
        self._cache_ready = asyncio.Event()

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def cog_load(self) -> None:
        """Load student DB and fetch banners on startup."""
        db.load()
        await self._refresh_banners()
        # Background refresh every 6 hours
        self._refresh_task = self.bot.loop.create_task(self._periodic_refresh())

    async def cog_unload(self) -> None:
        self._refresh_task.cancel()

    async def _refresh_banners(self) -> None:
        try:
            self._banner_cache = await fetch_banners()
        except Exception:
            log.exception("Banner refresh failed")
        finally:
            self._cache_ready.set()  # always unblock waiters, even on failure

    async def _periodic_refresh(self) -> None:
        while True:
            await asyncio.sleep(6 * 3600)  # 6 hours
            await self._refresh_banners()

    def _all_banners(self) -> list[dict]:
        """Return a flat list of all available (current + upcoming) banners for picking."""
        return self._banner_cache.get("current", []) + self._banner_cache.get("upcoming", [])

    # ── Commands ────────────────────────────────────────────────────────

    @commands.group(name="gacha", aliases=["banners", "g"], invoke_without_command=True)
    async def gacha(self, ctx: commands.Context) -> None:
        """Show live recruitment banners. Use subcommands: pick, info."""
        try:
            await asyncio.wait_for(self._cache_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass  # proceed with whatever we have
        banners = self._all_banners()

        if not banners:
            embed = discord.Embed(
                title="Recruitment",
                description="No active banners available right now.\n"
                            "Try the permanent **Regular Recruitment** banner instead:\n"
                            "`!gacha pick regular`",
                color=0x5BA0D0,
            )
            await ctx.reply(embed=embed)
            return

        # Build embed
        embed = discord.Embed(
            title="Recruitment — Available Banners",
            description="Use `!gacha pick <number>` to select a banner, then `!pull` to recruit!\n"
                        "Or `!gacha pick regular` for the permanent banner.\n"
                        "`!gacha info` to see your status.",
            color=0x5BA0D0,
        )

        for i, banner in enumerate(banners[:8]):  # Discord embed field limit
            embed.add_field(
                name="",
                value=format_banner_embed(banner, i),
                inline=False,
            )

        embed.set_footer(text="Banner data from BlueArchiveAPI • Updates every 6 hours")
        await ctx.reply(embed=embed)

    @gacha.command(name="pick")
    async def gacha_pick(self, ctx: commands.Context, *, choice: str = "") -> None:
        """Select your active recruitment banner.

        Usage: !gacha pick <number>  or  !gacha pick regular
        """
        choice = choice.strip().lower()
        if not choice:
            await ctx.reply("Usage: `!gacha pick <number>` or `!gacha pick regular`")
            return

        if choice == "regular":
            user_state = await _get_user_state(ctx.author.id)
            user_state["banner_id"] = "regular"
            await _set_user_state(ctx.author.id, user_state)
            await ctx.reply(
                "Active banner set to **Regular Recruitment** (permanent pool, no rate-up).\n"
                "Use `!pull` to recruit!"
            )
            return

        # Try numeric pick from banner list
        try:
            idx = int(choice) - 1
        except ValueError:
            await ctx.reply("Usage: `!gacha pick <number>` or `!gacha pick regular`")
            return

        try:
            await asyncio.wait_for(self._cache_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        banners = self._all_banners()

        if idx < 0 or idx >= len(banners):
            await ctx.reply(f"Invalid banner number. There are {len(banners)} banners available (1–{len(banners)}).")
            return

        banner = banners[idx]
        rateups = ", ".join(banner.get("rateups", [])) or "Standard Pool"
        gtype = banner.get("gachaType", "PickupGacha")

        user_state = await _get_user_state(ctx.author.id)
        user_state["banner_id"] = banner["id"]
        user_state["spark"] = 0  # reset spark on banner switch
        await _set_user_state(ctx.author.id, user_state)

        await ctx.reply(
            f"Active banner: **{gtype}**\n"
            f"Rate-up: {rateups}\n"
            f"Spark count reset to 0. Use `!pull` to recruit!"
        )

    @commands.command(name="pull")
    async def pull_gacha(self, ctx: commands.Context, *, mode: str = "10") -> None:
        """Perform a gacha pull. Default is 10-pull.

        Usage: !pull        (10-pull)
               !pull single (1-pull)
        """
        mode = mode.strip().lower()
        if mode in ("single", "1", "one"):
            count = 1
        else:
            count = 10

        user_state = await _get_user_state(ctx.author.id)
        banner_id = user_state.get("banner_id")

        # Determine banner info
        try:
            await asyncio.wait_for(self._cache_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        banner = None
        banner_name = "Regular Recruitment"
        rates = DEFAULT_RATES

        if banner_id and banner_id != "regular":
            for b in self._all_banners():
                if b["id"] == banner_id:
                    banner = b
                    break
            if banner:
                rateups = ", ".join(banner.get("rateups", [])) or "Standard Pool"
                gtype = banner.get("gachaType", "PickupGacha")
                banner_name = f"{gtype} — {rateups}"
                rates = get_rates_for_banner(banner)

        # "Opening envelope" teaser
        teaser = await ctx.reply("Opening recruitment envelope" + ("s..." if count > 1 else "..."))

        # Determine pool scope: regular banner excludes limiteds
        is_regular = (banner_id is None or banner_id == "regular")

        # Perform pulls
        pulls = []
        for i in range(count):
            # 10th pull guarantee
            r = rates
            if count == 10 and i == 9:
                r = PULL10_RATES

            rarity = roll_rarity(r)
            student = db.random_by_rarity(rarity, exclude_limited=is_regular)

            # Check for rate-up weighting (simplified: 30% chance to hit rate-up if 3★)
            if banner and rarity == 3 and banner.get("rateups"):
                rateup_names = [n.lower() for n in banner["rateups"]]
                if random.random() < 0.30:
                    # Try to find the rate-up student in our DB
                    for rn in rateup_names:
                        s = db.get_by_name(rn)
                        if s and s["StarGrade"] == 3:
                            student = s
                            break

            pulls.append(student)

        # Update spark
        user_state["spark"] = user_state.get("spark", 0) + count
        # Trim history to last 50
        history = user_state.get("history", [])
        history.extend(p["Id"] for p in pulls)
        user_state["history"] = history[-50:]
        await _set_user_state(ctx.author.id, user_state)

        # Render result image
        spark = user_state["spark"]
        try:
            async with ctx.typing():
                img_bytes = await render_pull(pulls, banner_name, spark)
        except Exception as e:
            log.exception("Render failed")
            try:
                await teaser.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            await ctx.reply(f"Render failed: {e}")
            return

        # Send image only
        try:
            file = discord.File(img_bytes, filename="gacha_result.png")
            try:
                await teaser.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            await ctx.reply(file=file)
        except Exception as e:
            log.exception("Failed to send result image")
            await ctx.reply(f"Failed to send result: {e}")

    @commands.command(name="spark")
    async def spark(self, ctx: commands.Context, *, character_name: str = "") -> None:
        """Claim a rate-up student from your active banner (costs 200 recruitment points).

        Usage: !spark <character name>
        """
        if not character_name:
            await ctx.reply("Usage: `!spark <character name>` — e.g., `!spark Aru`")
            return

        user_state = await _get_user_state(ctx.author.id)
        spark = user_state.get("spark", 0)

        if spark < SPARK_TARGET:
            await ctx.reply(
                f"You need {SPARK_TARGET} recruitment points to spark. You have {spark}."
            )
            return

        # Check if character exists
        student = db.get_by_name(character_name)
        if not student:
            await ctx.reply(f"Character **{character_name}** not found in the database.")
            return

        # Deduct spark
        user_state["spark"] = spark - SPARK_TARGET
        await _set_user_state(ctx.author.id, user_state)

        await ctx.reply(
            f"**Spark!** You claimed **{student['Name']}** ({student['StarGrade']}*, {student.get('School', 'Unknown')})!\n"
            f"Remaining recruitment points: {user_state['spark']}"
        )

    @gacha.command(name="info")
    async def gacha_info(self, ctx: commands.Context) -> None:
        """Show your current gacha state — active banner, spark count, and recent pulls."""
        user_state = await _get_user_state(ctx.author.id)
        banner_id = user_state.get("banner_id", "regular")
        spark = user_state.get("spark", 0)
        history = user_state.get("history", [])

        # Banner name
        banner_name = "Regular Recruitment"
        if banner_id and banner_id != "regular":
            for b in self._all_banners():
                if b["id"] == banner_id:
                    rateups = ", ".join(b.get("rateups", [])) or "Standard Pool"
                    banner_name = f"{b.get('gachaType', 'PickupGacha')} — {rateups}"
                    break

        # Recent pulls
        recent = []
        for sid in reversed(history[-5:]):
            s = db.get(sid)
            if s:
                recent.append(f"{'*' * s['StarGrade']} {s['Name']}")

        embed = discord.Embed(
            title="Your Gacha Status",
            color=0x5BA0D0,
        )
        embed.add_field(name="Active Banner", value=banner_name, inline=False)
        embed.add_field(
            name="Recruitment Points", value=f"{spark}/{SPARK_TARGET}", inline=True
        )
        embed.add_field(
            name="Total Pulls", value=str(len(history)), inline=True
        )
        if recent:
            embed.add_field(
                name="Recent Pulls", value="\n".join(recent) or "None", inline=False
            )

        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlueArchiveGacha(bot))
