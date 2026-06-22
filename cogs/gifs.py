# this is where gif commands live
import discord
import os
from discord.ext import commands

class Gifs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._gif_names = []
        self._load_gif_commands()

    def _load_gif_commands(self):
        if not os.path.exists('gifs'):
            return
        for filename in os.listdir('gifs'):
            if filename.endswith('.gif'):
                name = filename[:-4]
                self._gif_names.append(name)
                self._make_command(name)

    def _make_command(self, name: str):
        async def gif_command(ctx):
            await ctx.reply(file=discord.File(f'gifs/{name}.gif'))
        gif_command.__name__ = name
        cmd = commands.command(name=name)(gif_command)
        self.bot.add_command(cmd)

    @commands.command()
    async def gifs(self, ctx):
        gif_commands = [f'!{name}' for name in sorted(self._gif_names)]
        await ctx.reply('\n'.join(gif_commands))

async def setup(bot):
    await bot.add_cog(Gifs(bot))
