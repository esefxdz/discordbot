from discord.ext import commands

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def yuuka(self, ctx):
        await ctx.send('kanpeki!')

async def setup(bot):
    await bot.add_cog(General(bot))

@commands.Cog.listener()
async def on_command_error(self, ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # ignore typos silently
    print(f'Error: {error}')