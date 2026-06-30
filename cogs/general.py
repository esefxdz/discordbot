from datetime import timedelta
from aiohttp import client_exceptions
import discord
from discord.ext import commands
import os
import aiohttp
import feedparser
import urllib.parse

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def yuuka(self, ctx):
        await ctx.send('kanpeki!')

    @commands.command()
    async def weather(self, ctx, *, city: str = "kayseri"):
        try:
            city_encoded = urllib.parse.quote(city)
            url = f'https://wttr.in/{city_encoded}?0T'
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        weather_data = await resp.text()
                        await ctx.reply(f'```text\n{weather_data.strip()}\n```')
                    else:
                        await ctx.reply(f'could not fetch weather for {city}')
        except Exception as e:
            await ctx.reply(f'weather fetch failed: {e}')
        
    
    @commands.command()
    async def purge(self, ctx, amount: int):
        if ctx.author.id != int(os.getenv('OWNER_ID')):
            return
        await ctx.channel.purge(limit=amount + 1)

    #member specific command, priviliged
    @commands.command()
    async def ferox(self, ctx, duration: str):
        if ctx.author.id != int(os.getenv("MELLO")):
            return await ctx.reply("you are not her bro")
        ferox = ctx.guild.get_member(int(os.getenv("FEROX")))
        
        if duration.lower() in ("off", "unmute"):
            await ferox.timeout(None)
            await ctx.reply("oh, you want him free? fine.")
            return

        units = {
            "m": timedelta(minutes=1),
            "h": timedelta(hours=1),
            "d": timedelta(days=1),
            "w": timedelta(weeks=1),
            }

        amount = int(duration[:-1])
        delta = amount * units[duration[-1].lower()]

        await ferox.timeout(discord.utils.utcnow() + delta)
        await ctx.reply(f"muted ferox for {duration}, she knows the best")

    @commands.command()
    async def ban(self, ctx, *, reason=None):
        if ctx.author.id != int(os.getenv('OWNER_ID')):
            return

        if not ctx.guild:
            return

        msg = ctx.message
        to_ban = set()

        # Collect targets from mentions
        if msg.mention_everyone:
            for member in ctx.guild.members:
                to_ban.add(member)

        for role in msg.role_mentions:
            for member in role.members:
                to_ban.add(member)

        for user in msg.mentions:
            if isinstance(user, discord.Member):
                to_ban.add(user)

        # Safety checks: never ban the bot itself or the owner
        if ctx.me in to_ban:
            to_ban.remove(ctx.me)
        if ctx.author in to_ban:
            to_ban.remove(ctx.author)

        if not to_ban:
            return await ctx.reply("i couldnt ban, im broken fix me esef :(")

        # Confirm before banning
        await ctx.reply(f"Are you sure you want to ban {len(to_ban)} members? Type `yes` or `y` to confirm. (You have 30 seconds to confirm or i wont ban anyone)")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            import asyncio
            response_msg = await self.bot.wait_for('message', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.reply("Ban wave cancelled (timed out).")

        if response_msg.content.lower() not in ('yes', 'y'):
            return await ctx.reply("You didnt say yes so... im not banning until im sure.")
            
        await ctx.reply(f"Okay... banning {len(to_ban)} members, hope you wont regret...")
        
        banned_count = 0
        for member in to_ban:
            try:
                await member.ban(reason=reason or f"Mass ban by {ctx.author}")
                banned_count += 1
            except discord.Forbidden:
                pass # Can't ban this user (higher role/admin)
            except discord.HTTPException:
                pass # Random discord API error

        await ctx.reply(f"i banned {banned_count} users.")


    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return  # ignore typos silently
        print(f'Error: {error}')

async def setup(bot):
    await bot.add_cog(General(bot))
