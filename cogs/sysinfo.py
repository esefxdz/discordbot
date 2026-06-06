import subprocess
import discord
from discord.ext import commands

class SysInfo(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

    @commands.command()
    async def fetch(self, ctx):
        result = subprocess.run(['fastfetch'], capture_output=True, text=True)
        await ctx.reply(f'```\n{result.stdout}\n```')

    async def setup(bot):
    await bot.add_cog(SysInfo(bot))