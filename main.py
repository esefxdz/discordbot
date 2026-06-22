import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio

from tgbridge.forwarder import TelegramForwarder

load_dotenv('credentials.env')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} is online!')

async def main():
    # ── telegram forwarder ───────────────────────────────────────────────
    forwarder = TelegramForwarder(
        token=os.getenv('TELEGRAM_BOT_TOKEN'),
        group_id=int(os.getenv('TELEGRAM_GROUP_ID')),
        webhook_url=os.getenv('DISCORD_WEBHOOK_URL'),
    )

    async with bot:
        await bot.load_extension('cogs.general')
        await bot.load_extension('cogs.chance')
        await bot.load_extension('cogs.gifs')
        await bot.load_extension('cogs.copypasta')
        await bot.load_extension('cogs.sysinfo')
        await bot.load_extension('music.music')

        await forwarder.start()

        try:
            await bot.start(os.getenv('DISCORD_TOKEN'))
        finally:
            await forwarder.stop()

logging.basicConfig(level=logging.INFO)
asyncio.run(main())