import aiohttp
from discord.ext import commands, tasks
import os

class StatChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_id = int(os.getenv('STRINOVA_STAT_CHANNEL_ID'))
        self.channel_id_mao = int(os.getenv('STRINOVA_STAT_CHANNEL_ID_MAO'))
        self.update_stat.start()
        self.update_stat_mao.start()

    def cog_unload(self):
        self.update_stat.cancel()
        self.update_stat_mao.cancel()

    async def _fetch_and_update(self, channel_id):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=1282270') as r:
                    data = await r.json()
                    count = data['response']['player_count']
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.edit(name=f'Strinova Players From Steam: {count:,}')
        except Exception as e:
            print(f'stat channel update failed (channel {channel_id}): {e}')

    @tasks.loop(minutes=10)
    async def update_stat(self):
        await self._fetch_and_update(self.channel_id)

    @tasks.loop(minutes=10)
    async def update_stat_mao(self):
        await self._fetch_and_update(self.channel_id_mao)

    @update_stat.before_loop
    async def before_update_stat(self):
        await self.bot.wait_until_ready()

    @update_stat_mao.before_loop
    async def before_update_stat_mao(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(StatChannel(bot))
