import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio

from twitterbridge.rss import TwitterRSSForwarder

load_dotenv('credentials.env')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# The Telegram bridge discovers its own routes from credentials.env and
# starts polling when the extension loads — see tgbridge/config.py.

async def alert_owner(text: str):
    owner = await bot.fetch_user(int(os.getenv('OWNER_ID', '0')))
    await owner.send(text)

# one poller for the @CalabiyauLeaks feed, posting to both servers
twitter = TwitterRSSForwarder(
    webhooks={
        'main': os.getenv('TWITTER_DISCORD_WEBHOOK'),
        'mao': os.getenv('TWITTER_DISCORD_WEBHOOK_MAO'),
    },
    alert=alert_owner,
)

@bot.event
async def on_ready():
    print(f'[+] {bot.user} is online!')
    # on_ready fires again after reconnects; don't start a duplicate poller
    if not twitter._running:
        asyncio.create_task(twitter.start())
    # Clear stale global slash commands
    try:
        await bot.tree.sync()
    except Exception:
        pass

async def main():
    async with bot:
        await bot.load_extension('tgbridge')
        await bot.load_extension('cogs.general')
        await bot.load_extension('cogs.help')
        await bot.load_extension('cogs.chance')
        await bot.load_extension('cogs.gifs')
        await bot.load_extension('cogs.copypasta')
        await bot.load_extension('cogs.sysinfo')
        await bot.load_extension('music.music_wrapper')
        await bot.load_extension('cogs.statchannel')
        await bot.load_extension('cogs.welcome')
        await bot.load_extension('cogs.ffmpeg_things')
        await bot.load_extension('cogs.roles')
        await bot.load_extension('cogs.gitpull')
        await bot.load_extension('cogs.currency_converter')
        await bot.load_extension('ai.ai_roleplay')
        await bot.load_extension('twitterbridge.leaks_cmd')
        await bot.load_extension('blue_archive.gacha')
        await bot.load_extension('blue_archive.inventory')
        await bot.load_extension('firebase_website')
        await bot.load_extension('cogs.calendar')
        await bot.load_extension('cogs.bucketlist')
        await bot.load_extension('cogs.timestamp')
        await bot.load_extension('cogs.timestamp_friends')

        try:
            await bot.start(os.getenv('DISCORD_TOKEN'))
        finally:
            # bot.close() unloads extensions, so tgbridge.teardown stops the
            # Telegram poller and closes its aiohttp session for us
            twitter.stop()
            await twitter.close()

logging.basicConfig(level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)
asyncio.run(main())
