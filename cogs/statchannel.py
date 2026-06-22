import aiohttp
from discord.ext import commands, tasks
import os

class StatChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_id = int(os.getenv('STRINOVA_STAT_CHANNEL_ID'))
        self.update_stat.start()

    def cog_unload(self):
        self.update_stat.cancel()

    @tasks.loop(minutes=10)
    async def update_stat(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=1282270') as r:
                    data = await r.json()
                    count = data['response']['player_count']
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                await channel.edit(name=f'Strinova Players: {count:,}')
        except Exception as e:
            print(f'stat channel update failed: {e}')

    @update_stat.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(StatChannel(bot))