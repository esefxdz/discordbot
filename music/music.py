import wavelink
import discord
from discord.ext import commands


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        node = music.Node(uri='http://127.0.0.1:2333', password='yuukabot')
        await music.Pool.connect(nodes=[node], client=self.bot)
        print('✅ Wavelink connected to Lavalink')

    @commands.command()
    async def play(self, ctx, *, query: str):
        if not ctx.author.voice:
            return await ctx.reply('you need to be in a voice channel!')
        
        if not ctx.voice_client:
            vc: music.Player = await ctx.author.voice.channel.connect(cls=music.Player)
        else:
            vc: music.Player = ctx.voice_client

        tracks = await music.Playable.search(query)
        if not tracks:
            return await ctx.reply('nothing found!')

        track = tracks[0]
        await vc.play(track)
        await ctx.reply(f'▶️ now playing: {track.title}')

    @commands.command()
    async def stop(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.reply('⏹️ stopped!')

    @commands.command()
    async def skip(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.skip()
            await ctx.reply('⏭️ skipped!')

    @commands.command()
    async def pause(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.pause(not ctx.voice_client.paused)
            await ctx.reply('⏸️ toggled pause!')


async def setup(bot):
    await bot.add_cog(Music(bot))