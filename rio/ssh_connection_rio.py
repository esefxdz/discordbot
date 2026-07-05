import os
import subprocess
import discord
from discord.ext import commands

class Rio(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def connectrio(self, ctx):
        target = os.getenv("RIO_CONNECTION")

        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", target, "echo ok"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and "ok" in result.stdout:
                await ctx.reply("ok")
            else:
                await ctx.reply("failed")

        except Exception as e:
            await ctx.reply(f"failed: {e}")


async def setup(bot):
    await bot.add_cog(Rio(bot))

    #the basics are done, i will complete this function once i switch to arch 