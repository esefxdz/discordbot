import os
import time
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# load aliases from json
with open(os.path.join(os.path.dirname(__file__), 'currency_aliases.json'), 'r', encoding='utf-8') as f:
    _ALIAS_MAP = json.load(f)

# unique ISO codes available for autocomplete (preserves order, deduplicates)
_ISO_CODES = sorted(set(_ALIAS_MAP.values()))

# in-memory cache for rates: {base: (timestamp, rates_dict)}
_RATE_CACHE = {}
_CACHE_TTL = 3600  # seconds


def _norm(cur: str) -> str:
    """Normalize a currency string to its ISO code.
    Returns upper-case ISO code or raises ValueError if unknown.
    """
    cur = cur.strip().lower()
    if cur in _ALIAS_MAP:
        return _ALIAS_MAP[cur]
    cur = cur.upper()
    if len(cur) == 3 and cur.isalpha():
        return cur
    raise ValueError(f"unknown currency '{cur}'")


async def _get_rates(base: str) -> dict:
    """Fetch rates for *base* from cache or the Frankfurter API."""
    now = time.time()
    cached = _RATE_CACHE.get(base)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    url = f"https://api.frankfurter.app/latest?base={base}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError('currency API error')
            data = await resp.json()
    rates = data.get('rates', {})
    _RATE_CACHE[base] = (now, rates)
    return rates


def _make_embed(amount: float, from_code: str, to_code: str, rate: float) -> discord.Embed:
    result = amount * rate
    embed = discord.Embed(title='Currency conversion', color=0x5865F2)
    embed.add_field(name='From', value=f"{amount:g} {from_code}", inline=True)
    embed.add_field(name='To', value=f"{result:g} {to_code}", inline=True)
    embed.add_field(name='Rate', value=f"1 {from_code} = {rate:g} {to_code}", inline=False)
    embed.set_footer(text='Data from frankfurter.app')
    return embed


async def _currency_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return up to 25 matching currency codes/aliases for autocomplete."""
    current_lower = current.strip().lower()
    results: list[app_commands.Choice[str]] = []

    # exact alias matches first
    for alias, code in _ALIAS_MAP.items():
        if current_lower in alias and len(results) < 25:
            results.append(app_commands.Choice(name=f"{code} ({alias})", value=code))

    # fill remaining slots with ISO code prefix matches
    for code in _ISO_CODES:
        if code.startswith(current.upper()) and len(results) < 25:
            if not any(c.value == code for c in results):
                results.append(app_commands.Choice(name=code, value=code))

    return results[:25]


class CurrencyConverter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── slash command ────────────────────────────────────────────────────────

    @app_commands.command(name='currency', description='Convert between currencies')
    @app_commands.describe(
        from_cur='Currency to convert from (e.g. USD, dollar, $)',
        to_cur='Currency to convert to (e.g. EUR, euro, €)',
        amount='Amount to convert (default: 1)',
    )
    @app_commands.autocomplete(from_cur=_currency_autocomplete, to_cur=_currency_autocomplete)
    async def currency_slash(
        self,
        interaction: discord.Interaction,
        from_cur: str,
        to_cur: str,
        amount: float = 1.0,
    ):
        await interaction.response.defer()
        try:
            from_code = _norm(from_cur)
            to_code = _norm(to_cur)
        except ValueError as e:
            return await interaction.followup.send(f"Unknown currency: {e}", ephemeral=True)
        try:
            rates = await _get_rates(from_code)
        except Exception:
            return await interaction.followup.send('Could not fetch conversion rates, try again later.', ephemeral=True)
        rate = rates.get(to_code)
        if rate is None:
            return await interaction.followup.send(f"Unsupported conversion: {from_code} to {to_code}", ephemeral=True)
        await interaction.followup.send(embed=_make_embed(amount, from_code, to_code, rate))

    # ── prefix command (kept for backwards compat) ───────────────────────────

    @commands.command(name='currency')
    async def currency_prefix(self, ctx, amount: float = 1, from_cur: str = None, to_cur: str = None):
        """Convert amount from from_cur to to_cur. Usage: !currency [amount] <from> <to>"""
        if from_cur is None and to_cur is None:
            to_cur = amount       # type: ignore
            from_cur = str(amount)  # type: ignore
            amount = 1.0
        try:
            from_code = _norm(str(from_cur))
            to_code = _norm(str(to_cur))
        except ValueError as e:
            return await ctx.reply(f"Unknown currency: {e}")
        try:
            rates = await _get_rates(from_code)
        except Exception:
            return await ctx.reply('Could not fetch conversion rates, try again later.')
        rate = rates.get(to_code)
        if rate is None:
            return await ctx.reply(f"Unsupported conversion: {from_code} to {to_code}")
        await ctx.reply(embed=_make_embed(amount, from_code, to_code, rate))


async def setup(bot: commands.Bot):
    await bot.add_cog(CurrencyConverter(bot))
