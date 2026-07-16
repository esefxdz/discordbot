import os
import aiohttp
import feedparser
from discord.ext import commands

REQUEST_TIMEOUT = 15


class TwitterLeaks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def leaks(self, ctx):
        primary = os.getenv('TWITTER_RSS_URL', '')
        fallbacks_raw = os.getenv('TWITTER_RSS_FALLBACKS', '')

        urls = [primary] if primary else []
        if fallbacks_raw:
            urls.extend(u.strip() for u in fallbacks_raw.split(',') if u.strip())

        if not urls:
            return await ctx.reply('rss url not configured.')

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            entries = None
            last_error = None

            for url in urls:
                try:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        text = await resp.text()
                    feed = feedparser.parse(text)
                    if feed.entries:
                        entries = feed.entries
                        break
                except Exception as e:
                    last_error = e
                    continue

        if not entries:
            msg = 'no tweets found.'
            if last_error:
                msg += f' (last error: {last_error})'
            return await ctx.reply(msg[:2000])

        response = "**Latest 5 Leaks:**\n"
        for entry in entries[:5]:
            link = entry.get('link', '')
            if 'nitter.net' in link:
                link = link.replace('nitter.net', 'twitter.com').replace('#m', '')
            response += f'\n{link}\n'

        await ctx.reply(response[:2000])


async def setup(bot):
    await bot.add_cog(TwitterLeaks(bot))
