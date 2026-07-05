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

    # Flips a coin, returning Heads or Tails
    @commands.command()
    async def coin(self, ctx):
        result = random.choice(['🪙 Heads', '🪙 Tails'])
        await ctx.reply(f'{result}')

    # Rolls a standard 6-sided die
    @commands.command()
    async def dice(self, ctx):
        result = random.randint(1, 6)
        await ctx.reply(f'🎲 {result}')

    # Rolls a 4-sided die
    @commands.command()
    async def d4(self, ctx):
        result = random.randint(1, 4)
        await ctx.reply(f'🎲 {result}')

    # Rolls an 8-sided die
    @commands.command()
    async def d8(self, ctx):
        result = random.randint(1, 8)
        await ctx.reply(f'🎲 {result}')

    # Rolls a 10-sided die
    @commands.command()
    async def d10(self, ctx):
        result = random.randint(1, 10)
        await ctx.reply(f'🎲 {result}')

    # Rolls a 12-sided die
    @commands.command()
    async def d12(self, ctx):
        result = random.randint(1, 12)
        await ctx.reply(f'🎲 {result}')

    # Rolls a 20-sided die
    @commands.command()
    async def d20(self, ctx):
        result = random.randint(1, 20)
        await ctx.reply(f'🎲 {result}')

    # Rolls a 100-sided die
    @commands.command()
    async def d100(self, ctx):
        result = random.randint(1, 100)
        await ctx.reply(f'🎲 {result}')

    # Rolls custom NdN formatted dice (e.g., 2d6 or 20)
    @commands.command()
    async def roll(self, ctx, dice_format: str):
        """Rolls a dice in NdN format."""
        dice_format = dice_format.lower()
        try:
            if 'd' not in dice_format:
                rolls = 1
                limit = int(dice_format)
            else:
                parts = dice_format.split('d')
                rolls = int(parts[0]) if parts[0] else 1
                limit = int(parts[1])
        except Exception:
            await ctx.reply('Format has to be in NdN (e.g., 2d6), dN (e.g., d20), or just N (e.g., 20)!')
            return

        if rolls > 100:
            await ctx.reply('Too many dice! Max 100.')
            return
            
        if limit > 1000:
            await ctx.reply('Dice too big! Max d1000.')
            return

        result = ', '.join(str(random.randint(1, limit)) for _ in range(rolls))
        await ctx.reply(f'🎲 {result}')

    # Measures a user's pp size
    @commands.command()
    async def pp(self, ctx):
        length = random.randint(1, 15)
        pp = '8' + '=' * length + 'D'
        await ctx.reply(f"{ctx.author.mention}'s pp size: {pp}")
    
    # Assigns a random funny rank to the user
    @commands.command()
    async def rank(self, ctx):
        ranks = ['Substance', 'Molecule', 'Atom', 'Proton', 'Neutron', 'Electron', 'Quark', 'Superstring', 'Singularity', 'giorgaras200']
        rank = random.choice(ranks)
        await ctx.reply(f"{ctx.author.mention}'s rank: {rank}")
    
    # Calculates the user's aura score
    @commands.command()
    async def aura(self, ctx):
        aura = random.randint(-1000000000, +1000000000)
        await ctx.reply(f"{ctx.author.mention}'s aura: {aura:,}")
        
    # Play a game of russian roulette
    @commands.command()
    async def roulette(self, ctx):
        if random.randint(1, 6) == 1:
            await ctx.reply(f'{ctx.author.mention} died.')
        else:
            await ctx.reply(f'{ctx.author.mention} survived.')

    # Magic 8-Ball
    @commands.command(name="8ball")
    async def _8ball(self, ctx, *, question: str):
        responses = ["It is certain.", "It is decidedly so.", "Without a doubt.", "Yes - definitely.", "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.", "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
        await ctx.reply(f'🎱 {random.choice(responses)}')

    @commands.command()
    async def slots(self, ctx):
        emojis = ['🍎', '🍒', '🍇', '💎', '🔔', '🍋']
        a, b, c = random.choice(emojis), random.choice(emojis), random.choice(emojis)
        await ctx.reply(f'[ {a} | {b} | {c} ]')

    # Rock, Paper, Scissors
    @commands.command()
    async def rps(self, ctx, choice: str):
        choices = ['rock', 'paper', 'scissors']
        choice = choice.lower()
        if choice not in choices:
            await ctx.reply("Please choose rock, paper, or scissors!")
            return
            
        bot_choice = random.choice(choices)
        
        if choice == bot_choice:
            result = "It's a tie!"
        elif (choice == 'rock' and bot_choice == 'scissors') or \
             (choice == 'paper' and bot_choice == 'rock') or \
             (choice == 'scissors' and bot_choice == 'paper'):
            result = "You win!"
        else:
            result = "I win!"
            
        await ctx.reply(f"I chose {bot_choice}. {result}")

    @commands.command()
    async def ship(self, ctx, user1: str, user2: str = None):
        if user2 is None:
            user2 = user1
            user1 = ctx.author.mention
            
        percentage = random.randint(0, 100)
        await ctx.reply(f"🛳️ **Ship:** {user1} x {user2}\n**Compatibility:** {percentage}%")

    # 10 Pull Gacha (5% Purple, 19% Yellow, 76% Blue)
    @commands.command()
    async def pull(self, ctx):
        results = []
        for _ in range(10):
            roll = random.randint(1, 100)
            if roll <= 5:
                results.append('🟪')
            elif roll <= 24:
                results.append('🟨')
            else:
                results.append('🟦')
                
        if '🟨' not in results and '🟪' not in results:
            if '🟦' in results:
                results[results.index('🟦')] = '🟨'
                
        # Using En-Space (\u2002) so Discord doesn't collapse it
        row1 = "\u2002".join(results[0:5])
        row2 = "\u2002".join(results[5:10])
        
        await ctx.reply(f"{row1}\n\n{row2}")


async def setup(bot):
    await bot.add_cog(Fun(bot))