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

# in-memory cache for rates: {base: (timestamp, rates_dict)}
_RATE_CACHE = {}
_CACHE_TTL = 3600  # seconds

class CurrencyConverter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _norm(self, cur: str) -> str:
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

    async def _get_rates(self, base: str) -> dict:
        """Fetch rates for *base* from cache or the Frankfurter API.
        Returns a dict mapping target ISO codes to float rates.
        """
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

    @app_commands.command(name='currency')
    @app_commands.rename(from_cur='from', to_cur='to')
    @app_commands.describe(from_cur='Currency to convert from, e.g. usd, euro, lira',
                           to_cur='Currency to convert to',
                           amount='How much to convert (default 1)')
    async def currency(self, interaction: discord.Interaction, from_cur: str, to_cur: str, amount: float = 1.0):
        """Convert money between currencies."""
        try:
            from_code = self._norm(from_cur)
            to_code = self._norm(to_cur)
        except ValueError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        try:
            rates = await self._get_rates(from_code)
        except Exception:
            return await interaction.response.send_message('❌ could not fetch conversion rates, try again later', ephemeral=True)
        rate = rates.get(to_code)
        if rate is None:
            return await interaction.response.send_message(f"❌ unsupported conversion: {from_code} → {to_code}", ephemeral=True)
        result = amount * rate
        embed = discord.Embed(title='Currency conversion', color=0x00ff00)
        embed.add_field(name='From', value=f"{amount:g} {from_code}", inline=True)
        embed.add_field(name='To', value=f"{result:g} {to_code}", inline=True)
        embed.add_field(name='Rate', value=f"1 {from_code} = {rate:g} {to_code}", inline=False)
        embed.set_footer(text='Data from frankfurter.app')
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(CurrencyConverter(bot))
