# this is where gif commands live
import discord
import os
from discord.ext import commands

class Gifs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._load_gif_commands()

    def _load_gif_commands(self):
        if not os.path.exists('gifs'):
            return
        for filename in os.listdir('gifs'):
            if filename.endswith('.gif'):
                name = filename[:-4]  # strip .gif
                self._make_command(name)

    def _make_command(self, name: str):
        async def gif_command(self, ctx):
            await ctx.reply(file=discord.File(f'gifs/{name}.gif'))
        gif_command.__name__ = name
        cmd = commands.command(name=name)(gif_command)
        self.add_command(cmd)

    @commands.command()
    async def gifs(self, ctx):
        gif_commands = [f'!{cmd.name}' for cmd in self.get_commands() if cmd.name != 'gifs']
        await ctx.reply('\n'.join(sorted(gif_commands)))

async def setup(bot):
    await bot.add_cog(Gifs(bot))