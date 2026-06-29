import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio

from tgbridge.forwarder import TelegramForwarder
from tgbridge.discord_to_telegram import DiscordToTelegramForwarder

from twitterbridge.rss import TwitterRSSForwarder

load_dotenv('credentials.env')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

forwarder = TelegramForwarder(token=os.getenv('TELEGRAM_BOT_TOKEN'))
if os.getenv('TELEGRAM_GROUP_ID') and os.getenv('DISCORD_WEBHOOK_URL'):
    forwarder.add_route(int(os.getenv('TELEGRAM_GROUP_ID')), os.getenv('DISCORD_WEBHOOK_URL'))
if os.getenv('TELEGRAM_GROUP_ID_SHITPOST') and os.getenv('DISCORD_WEBHOOK_URL_SHITPOST'):
    forwarder.add_route(int(os.getenv('TELEGRAM_GROUP_ID_SHITPOST')), os.getenv('DISCORD_WEBHOOK_URL_SHITPOST'))

# Discord → Telegram (reverse bridge)
d_to_tg = DiscordToTelegramForwarder(bot=bot, tg_token=os.getenv('TELEGRAM_BOT_TOKEN'))
if os.getenv('DISCORD_CHANNEL_ID') and os.getenv('TELEGRAM_GROUP_ID'):
    d_to_tg.add_route(int(os.getenv('DISCORD_CHANNEL_ID')), int(os.getenv('TELEGRAM_GROUP_ID')))
if os.getenv('DISCORD_CHANNEL_ID_SHITPOST') and os.getenv('TELEGRAM_GROUP_ID_SHITPOST'):
    d_to_tg.add_route(int(os.getenv('DISCORD_CHANNEL_ID_SHITPOST')), int(os.getenv('TELEGRAM_GROUP_ID_SHITPOST')))

twitter = TwitterRSSForwarder(
    rss_url=os.getenv('TWITTER_RSS_URL'),
    webhook_url=os.getenv('TWITTER_DISCORD_WEBHOOK'),
)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} is online!')
    asyncio.create_task(forwarder.start())
    asyncio.create_task(twitter.start())

async def main():
    async with bot:
        await bot.add_cog(d_to_tg)
        await bot.load_extension('cogs.general')
        await bot.load_extension('cogs.chance')
        await bot.load_extension('cogs.gifs')
        await bot.load_extension('cogs.copypasta')
        await bot.load_extension('cogs.sysinfo')
        await bot.load_extension('music.music')
        await bot.load_extension('cogs.statchannel')
        await bot.load_extension('cogs.ffmpeg_things')
        await bot.load_extension('cogs.currency_converter')
        await bot.load_extension('ai.ai_roleplay')

        try:
            await bot.start(os.getenv('DISCORD_TOKEN'))
        finally:
            await forwarder.stop()
            twitter.stop()

logging.basicConfig(level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)
asyncio.run(main())