#this is the gacha cog for blue archive, handles all recruitment commands##
"""Blue Archive Gacha Simulator — in-game-accurate recruitment with live banners."""
######################################################################
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from . import db as gacha_db
from .data import (
    db,
    fetch_banners,
    format_banner_embed,
    roll_rarity,
    get_rates_for_banner,
)
from .gacha_renderer import render_pull
from .constants import SPARK_TARGET, BANNER_FILE, DEFAULT_RATES, PULL10_RATES, GACHA_ANIM_PATH, GACHA_ANIM_DURATION

log = logging.getLogger(__name__)
_banner_lock = asyncio.Lock()


def _read_banner_file() -> dict:
    """Read banner state from disk (runs in thread)."""
    if BANNER_FILE.exists():
        with open(BANNER_FILE, "r") as f:
            return json.load(f)
    return {}


def _write_banner_file(state: dict) -> None:
    """Write banner state to disk (runs in thread)."""
    BANNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BANNER_FILE, "w") as f:
        json.dump(state, f, indent=2)


async def _load_banner_state() -> dict:
    """Which banner each user has picked."""
    async with _banner_lock:
        return await asyncio.to_thread(_read_banner_file)


async def _save_banner_state(state: dict) -> None:
    async with _banner_lock:
        await asyncio.to_thread(_write_banner_file, state)


async def _get_banner_id(user_id: int) -> Optional[str]:
    state = await _load_banner_state()
    return state.get(str(user_id))


async def _set_banner_id(user_id: int, banner_id: Optional[str]) -> None:
    state = await _load_banner_state()
    state[str(user_id)] = banner_id
    await _save_banner_state(state)


class BlueArchiveGacha(commands.Cog):
    """Blue Archive recruitment simulator with live banners and in-game-style renders."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._banner_cache: dict = {"current": [], "upcoming": [], "ended": []}
        self._cache_ready = asyncio.Event()

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def cog_load(self) -> None:
        """Load student DB, init SQLite, and fetch banners on startup."""
        await asyncio.to_thread(db.load)
        await gacha_db.init_db()
        await self._refresh_banners()
        await db.merge_ennead(self._banner_cache)
        self._refresh_task = asyncio.create_task(self._periodic_refresh())

    async def cog_unload(self) -> None:
        if hasattr(self, '_refresh_task'):
            self._refresh_task.cancel()

    async def _refresh_banners(self) -> None:
        try:
            self._banner_cache = await fetch_banners()
            self._cache_ready.set()
        except Exception:
            log.exception("Banner refresh failed")

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
        """Show available banners and commands.

        Without a subcommand, lists current banners.
        Use !gacha help for full command reference.
        """
        try:
            await asyncio.wait_for(self._cache_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        banners = self._all_banners()

        if not banners:
            embed = discord.Embed(
                title="Recruitment",
                description="No active banners available right now.\n"
                            "Try the permanent **Regular Recruitment** banner instead:\n"
                            "`!gacha pick regular`",
                color=0x5BA0D0,
            )
            embed.set_footer(text="!gacha help — see all commands")
            await ctx.reply(embed=embed)
            return

        embed = discord.Embed(
            title="Recruitment — Available Banners",
            description=(
                "**Commands:** `!gacha pick <n>` • `!gacha info` • "
                "`!pull` • `!pull single` • `!spark <name>` • "
                "`!inv` • `!eligma`\n"
                "Use `!gacha help` for details.\n"
            ),
            color=0x5BA0D0,
        )

        for i, banner in enumerate(banners[:8]):
            embed.add_field(
                name="",
                value=format_banner_embed(banner, i),
                inline=False,
            )

        embed.set_footer(text="Banner data from BlueArchiveAPI • Updates every 6 hours")
        await ctx.reply(embed=embed)

    @gacha.command(name="help")
    async def gacha_help(self, ctx: commands.Context) -> None:
        """Show all gacha-related commands."""
        embed = discord.Embed(
            title="Gacha Commands",
            color=0x5BA0D0,
        )
        embed.add_field(
            name="Banners",
            value=(
                "`!gacha` — list current banners\n"
                "`!gacha pick <n>` — select banner #n\n"
                "`!gacha pick regular` — permanent pool\n"
                "`!gacha info` — your status & spark"
            ),
            inline=False,
        )
        embed.add_field(
            name="Pulling",
            value=(
                "`!pull` — 10-pull (guaranteed 2★+ on 10th)\n"
                "`!pull single` — single pull\n"
                "`!spark <name>` — claim rate-up at 200 pts"
            ),
            inline=False,
        )
        embed.add_field(
            name="Collection",
            value=(
                "`!inv` — interactive collection browser\n"
                "`!inv @user` — view someone else's\n"
                "`!eligma` — check your eligma balance"
            ),
            inline=False,
        )
        await ctx.reply(embed=embed)

    @commands.cooldown(1, 3, commands.BucketType.user)
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
            await _set_banner_id(ctx.author.id, "regular")
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

        await _set_banner_id(ctx.author.id, str(banner["id"]))

        # Show current spark for this banner
        spark = await gacha_db.get_spark(ctx.author.id, str(banner["id"]))

        await ctx.reply(
            f"Active banner: **{gtype}**\n"
            f"Rate-up: {rateups}\n"
            f"Recruitment Points: {spark}/{SPARK_TARGET}. Use `!pull` to recruit!"
        )

    @commands.cooldown(1, 6, commands.BucketType.user)
    @commands.command(name="pull")
    async def pull_gacha(self, ctx: commands.Context, *, mode: str = "10") -> None:
        """Pull on your active recruitment banner.

        Usage: !pull         (10-pull with guaranteed 2★+ on 10th)
               !pull single  (1-pull)
        """
        mode = mode.strip().lower()
        if mode in ("single", "1", "one"):
            count = 1
        else:
            count = 10

        user_id = ctx.author.id
        banner_id = await _get_banner_id(user_id)

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
                if str(b.get("id")) == banner_id:
                    banner = b
                    break
            if banner:
                rateups = ", ".join(banner.get("rateups", [])) or "Standard Pool"
                gtype = banner.get("gachaType", "PickupGacha")
                banner_name = f"{gtype} — {rateups}"
                rates = get_rates_for_banner(banner)

        # "Opening envelope" teaser — animation GIF, or text fallback
        if GACHA_ANIM_PATH.exists():
            teaser = await ctx.reply(file=discord.File(str(GACHA_ANIM_PATH)))
            anim_start = asyncio.get_running_loop().time()
            is_gif = True
        else:
            teaser = await ctx.reply("Opening recruitment envelope" + ("s..." if count > 1 else "..."))
            is_gif = False

        # Build the banner-specific character pool
        if banner_id and banner_id != "regular" and banner:
            pool = db.build_pool(banner)
            rateup_names = [n.lower() for n in banner.get("rateups", [])]
        else:
            pool = db.build_pool(None)  # regular recruitment
            rateup_names = []

        # Perform pulls
        pull_results = []
        for i in range(count):
            # 10th pull guarantee
            if count == 10 and i == 9:
                # Guaranteed 2★+ — use banner's 3★ rate, rest to 2★
                r3 = rates[0]
                r = (r3, 1.0 - r3, 0.0)
            else:
                r = rates

            rarity = roll_rarity(r)
            rarity_pool = pool.get(rarity, [])
            if rarity_pool:
                student = db.weighted_pick(rarity_pool, rateup_names, rarity)
            else:
                student = db.random_by_rarity(rarity)  # fallback

            pull_results.append(student)

        # Save pulls to inventory (eligma for dupes) and update spark — all async
        spark_banner = str(banner_id) if (banner_id and banner_id != "regular") else None
        spark = 0
        if spark_banner:
            spark = await gacha_db.add_spark(user_id, spark_banner, count)

        eligma_earned = 0
        for p in pull_results:
            try:
                e = await gacha_db.add_pull(ctx.author.id, p["Id"], p["StarGrade"])
                eligma_earned += e
            except Exception:
                log.exception(
                    "DB add_pull failed for user %d student %d",
                    ctx.author.id, p["Id"],
                )

        dupe_msg = f" | +{eligma_earned} Eligma from dupes" if eligma_earned else ""

        # Render result image
        try:
            async with ctx.typing():
                img_bytes = await render_pull(pull_results, banner_name, spark)
        except Exception as e:
            log.exception("Render failed")
            try:
                await teaser.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            await ctx.reply(f"Render failed: {e}")
            return

        # If we showed the GIF, wait for it to finish playing before replacing
        if is_gif:
            elapsed = asyncio.get_running_loop().time() - anim_start
            remaining = GACHA_ANIM_DURATION - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

        # Send image with spark+eligma caption
        try:
            file = discord.File(img_bytes, filename="gacha_result.png")
            try:
                await teaser.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            await ctx.reply(
                f"Recruitment Points: {spark}/{SPARK_TARGET}{dupe_msg}",
                file=file,
            )
        except Exception as e:
            log.exception("Failed to send result image")
            await ctx.reply(f"Failed to send result: {e}")

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(name="spark")
    async def spark(self, ctx: commands.Context, *, character_name: str = "") -> None:
        """Claim a rate-up student using 200 Recruitment Points from your active banner.

        Usage: !spark <character name>
        """
        if not character_name:
            await ctx.reply("Usage: `!spark <character name>` — e.g., `!spark Aru`")
            return

        user_id = ctx.author.id
        banner_id = str(await _get_banner_id(user_id) or "regular")
        spark = await gacha_db.get_spark(user_id, banner_id)

        # Regular recruitment has no spark system
        if banner_id == "regular":
            await ctx.reply("Regular Recruitment does not have a spark system. Pick a banner with `!gacha pick <n>` first.")
            return

        if spark < SPARK_TARGET:
            await ctx.reply(
                f"You need {SPARK_TARGET} Recruitment Points to spark. "
                f"You have {spark} on this banner."
            )
            return

        # Resolve character name
        student = db.get_by_name(character_name)
        if not student:
            await ctx.reply(f"Character **{character_name}** not found in the database.")
            return

        # Validate against active banner rate-ups
        banner = None
        for b in self._all_banners():
            if str(b.get("id")) == banner_id:
                banner = b
                break
        if banner:
            rateups = [n.lower() for n in banner.get("rateups", [])]
            matched = any(
                db.get_by_name(rn) is student for rn in rateups
            )
            if not matched:
                rateup_str = ", ".join(banner.get("rateups", []))
                await ctx.reply(
                    f"**{student['Name']}** is not a rate-up on this banner.\n"
                    f"Rate-ups: {rateup_str}"
                )
                return

        # Atomic claim: check points, check ownership, add student, deduct points
        success, msg = await gacha_db.spark_claim(user_id, banner_id, student["Id"], student["StarGrade"])
        if not success:
            if msg == "already_owned":
                await ctx.reply(f"You already own **{student['Name']}** — duplicates come from pulls.")
            else:
                await ctx.reply(msg)
            return
        remaining = await gacha_db.get_spark(user_id, banner_id)

        await ctx.reply(
            f"**Spark!** You claimed **{student['Name']}** "
            f"({student['StarGrade']}*, {student.get('School', 'Unknown')})!\n"
            f"Remaining Recruitment Points: {remaining}"
        )

    @gacha.command(name="info")
    async def gacha_info(self, ctx: commands.Context) -> None:
        """Show your active banner, spark count, and collection summary."""
        user_id = ctx.author.id
        banner_id = str(await _get_banner_id(user_id) or "regular")
        spark = await gacha_db.get_spark(user_id, banner_id)

        # Banner name
        banner_name = "Regular Recruitment"
        if banner_id and banner_id != "regular":
            for b in self._all_banners():
                if str(b.get("id")) == banner_id:
                    rateups = ", ".join(b.get("rateups", [])) or "Standard Pool"
                    banner_name = f"{b.get('gachaType', 'PickupGacha')} — {rateups}"
                    break

        embed = discord.Embed(
            title="Your Gacha Status",
            color=0x5BA0D0,
        )
        embed.add_field(name="Active Banner", value=banner_name, inline=False)
        embed.add_field(
            name="Recruitment Points", value=f"{spark}/{SPARK_TARGET}", inline=True
        )
        embed.add_field(
            name="Eligma", value=str(await gacha_db.get_eligma(user_id)), inline=True
        )
        stats = await gacha_db.get_inventory_stats(user_id)
        embed.add_field(
            name="Collection",
            value=f"{stats['unique']} unique students from {stats['total_pulls']} total pulls",
            inline=False,
        )

        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlueArchiveGacha(bot))
