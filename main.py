import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio

load_dotenv('credentials.env')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} is online!')

async def main():
    async with bot:
        await bot.load_extension('cogs.general')
        await bot.load_extension('cogs.chance')
        await bot.load_extension('cogs.gifs')
        await bot.load_extension('cogs.copypasta')
        await bot.load_extension('cogs.sysinfo')
        await bot.load_extension('music.music')
        await bot.start(os.getenv('DISCORD_TOKEN'))

logging.basicConfig(level=logging.INFO)
asyncio.run(main())