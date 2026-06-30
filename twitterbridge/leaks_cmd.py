import os
import aiohttp
import feedparser
from discord.ext import commands

class TwitterLeaks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def leaks(self, ctx):
        rss_url = os.getenv('TWITTER_RSS_URL')
        if not rss_url:
            return await ctx.reply('rss url not configured.')
            
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(rss_url) as resp:
                    text = await resp.text()
                    
            feed = feedparser.parse(text)
            if not feed.entries:
                return await ctx.reply('no tweets found.')
                
            response = "**Latest 5 Leaks:**\n"
            for entry in feed.entries[:5]:
                title = entry.get('title', '')
                link = entry.get('link', '')
                
                if 'nitter.net' in link:
                    link = link.replace('nitter.net', 'twitter.com').replace('#m', '')
                    
                response += f"\n{title}\n{link}\n"
                
            await ctx.reply(response[:2000])
        except Exception as e:
            await ctx.reply(f'failed to fetch leaks: {e}')

async def setup(bot):
    await bot.add_cog(TwitterLeaks(bot))
