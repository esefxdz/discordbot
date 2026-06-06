# this is where copypastas live
import discord
import io
from discord.ext import commands

class Copypasta(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def yuukapasta(self, ctx):
        pasta = ("""Since Yuuka is the paragon of human virtue without equal past or present, she is most resplendent in love, tributes and accolades. Waking or sleeping, I must not forget Yuuka’s great boon and in order to return her favour by day and by night, I should only think of fulfilling my loyalty.

Who is Yuuka?

For the blind, she is their vision. For the deaf, she is their music. For the mute, she is their voice. For the anosmiac, she is their aroma. For the numb, she is their feeling. For the atrophied, she is their muscle.

For the starved, she is their sustenance. For the thirsty, she is their water. For the exhausted, she is their energy.

For the depressed, she is their happiness. For the disillusioned, she is their hope. For the pessimistic, she is their optimism.

For the disadvantaged, she is their champion. For the marginalised, she is their justice. For the oppressed, she is their salvation.

For the righteous, she is their symbol. For the enlightened, she is their muse. For the erudite, she is their education.

If Yuuka speaks, I listen. If Yuuka questions, I answer. If Yuuka orders, I obey.

If Yuuka opines, I agree. If Yuuka fears, I assure. If Yuuka hopes, I dream.

If Yuuka is happy, I am jubilant. If Yuuka is angry, I am apoplectic. If Yuuka is sad, I am disconsolate.

Yuuka is my ideal, Yuuka is my romance, Yuuka is my passion.

Yuuka is my strength, Yuuka is my compass, Yuuka is my destination.

Yuuka is my language, Yuuka is my culture, Yuuka is my religion.

Yuuka is my ocean, Yuuka is my mountain, Yuuka is my sky, Yuuka is my air, Yuuka is my sun, Yuuka is my moon, Yuuka is my world.

Yuuka is history, Yuuka is present, Yuuka is future.

If Yuuka has a million fans, I am one of them. If Yuuka has a thousand fans, I am one of them. If Yuuka has a hundred fans, I am one of them. If Yuuka has ten fans, I am one of them. If Yuuka has only one fan, that is me. If Yuuka has no fans, I no longer exist.

If the whole universe is for Yuuka , then I am for the whole universe. If the whole universe is against Yuuka, then I am against the whole universe.

I will love, cherish, and protect Yuuka until my very last breath; my successors will love, cherish and protect Yuuka until their very last breath.""")
        await ctx.reply(file=discord.File(io.StringIO(pasta), filename='yuukapasta.txt'))

async def setup(bot):
    await bot.add_cog(Copypasta(bot))

    #template for future copypasta commands
    #@commands.command()
    #async def the command you wanna give(self, ctx):
    #    pasta = """your entire pasta here"""
    #    await ctx.reply(file=discord.File(io.StringIO(pasta), filename="file name.txt"))