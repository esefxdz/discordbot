# this is where gif commands live
import discord
from discord.ext import commands

class Gifs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    #this command lists all the gif commands, not an actualy gif command##
    @commands.command()
    async def gifs(self, ctx):
        gif_commands = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(gif_commands))
    ######################################################################

    @commands.command()
    async def czech(self, ctx):
        await ctx.reply(file=discord.File('gifs/czech.gif'))

    @commands.command()
    async def dogshit(self, ctx):
        await ctx.reply(file=discord.File('gifs/dogshit.gif'))

    @commands.command()
    async def how(self, ctx):
        await ctx.reply(file=discord.File('gifs/how.gif'))

    @commands.command()
    async def peak(self, ctx):
        await ctx.reply(file=discord.File('gifs/peak.gif'))

    @commands.command()
    async def please(self, ctx):
        await ctx.reply(file=discord.File('gifs/please.gif'))

    @commands.command()
    async def shoo(self, ctx):
        await ctx.reply(file=discord.File('gifs/shoo.gif'))

    @commands.command()
    async def spell(self, ctx):
        await ctx.reply(file=discord.File('gifs/spell.gif'))

    @commands.command()
    async def tired(self, ctx):
        await ctx.reply(file=discord.File('gifs/tired.gif'))

    @commands.command()
    async def touch(self, ctx):
        await ctx.reply(file=discord.File('gifs/touch.gif'))

async def setup(bot):
    await bot.add_cog(Gifs(bot))