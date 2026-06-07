#this is where coinflip and dice lives
from discord.ext import commands
import random

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # this command lists all the fun commands, not an actualy fun command##
    @commands.command()
    async def fun(self, ctx):
        fun_commands = [f'!{cmd.name}' for cmd in self.get_commands() if cmd.name != 'fun']
        await ctx.reply('\n'.join(sorted(fun_commands)))
    ######################################################################

    @commands.command()
    async def coin(self, ctx):
        result = random.choice(['🪙 Heads', '🪙 Tails'])
        await ctx.reply(f'{result}')

    @commands.command()
    async def dice(self, ctx):
        result = random.randint(1, 6)
        await ctx.reply(f'🎲 {result}')

    @commands.command()
    async def pp(self, ctx):
        length = random.randint(1, 15)
        pp = '8' + '=' * length + 'D'
        await ctx.reply(f"{ctx.author.mention}'s pp size: {pp}")
    
    @commands.command()
    async def rank(self, ctx):
        ranks = ['Substance', 'Molecule', 'Atom', 'Proton', 'Neutron', 'Electron', 'Quark', 'Superstring', 'Singularity', 'giorgaras200']
        rank = random.choice(ranks)
        await ctx.reply(f"{ctx.author.mention}'s rank: {rank}")
    
    @commands.command()
    async def aura(self, ctx):
        aura = random.randint(-1000000000, +1000000000)
        await ctx.reply(f"{ctx.author.mention}'s aura: {aura:,}")
    

async def setup(bot):
    await bot.add_cog(Fun(bot))