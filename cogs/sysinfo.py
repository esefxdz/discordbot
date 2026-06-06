import subprocess
import discord
from discord.ext import commands
import re

class SysInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def strip_ansi(self, text):
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\[[0-9]+[A-Z]', '', text)

    @commands.command()
    async def fetch(self, ctx):
        result = subprocess.run(['fastfetch', '--pipe'], capture_output=True, text=True)
        clean = self.strip_ansi(result.stdout)
        await ctx.reply(f'```\n{clean}\n```')

async def setup(bot):
    await bot.add_cog(SysInfo(bot))