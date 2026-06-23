import os
import asyncio
import tempfile
import discord
from discord.ext import commands

class FfmpegThings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    #this command lists all the ffmpeg commands, not an actualy ffmpeg command##
    @commands.command()
    async def ffmpeg(self, ctx):
        ffmpeg_commands = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(ffmpeg_commands))
    ######################################################################

    async def _download_attachment(self, attachment):
        suffix = os.path.splitext(attachment.filename)[1] or '.dat'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            await attachment.save(tmp.name)
            return tmp.name, attachment.filename

    async def _run_ffmpeg(self, args):
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg',
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return proc.returncode, stderr.decode()

    async def _process_and_send(self, ctx, ffmpeg_args, temp_in, temp_out):
        ret, err = await self._run_ffmpeg(ffmpeg_args)
        if ret != 0:
            await ctx.reply(f'ffmpeg failed:\n```\n{err}\n```')
            if os.path.exists(temp_in):
                os.remove(temp_in)
            return
            
        if os.path.getsize(temp_out) > 8 * 1024 * 1024:
            await ctx.reply('resulting gif is larger than 8mb.')
            os.remove(temp_in)
            os.remove(temp_out)
            return
            
        await ctx.send(file=discord.File(temp_out))
        os.remove(temp_in)
        os.remove(temp_out)

    @commands.command()
    async def togif(self, ctx):
        if not ctx.message.attachments:
            return await ctx.reply('you need to attach a video file.')
            
        temp_in, _ = await self._download_attachment(ctx.message.attachments[0])
        temp_out = temp_in + '.gif'
        
        ffmpeg_args = [
            '-y',
            '-i', temp_in,
            '-filter_complex',
            'fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
            '-loop', '0',
            temp_out,
        ]
        await self._process_and_send(ctx, ffmpeg_args, temp_in, temp_out)

    @commands.command()
    async def caption(self, ctx, *, text: str):
        if not ctx.message.attachments:
            return await ctx.reply('attach an image or gif to caption.')
            
        temp_in, _ = await self._download_attachment(ctx.message.attachments[0])
        temp_out = temp_in + '_cap.gif'
        
        drawtext = (
            f"drawtext=font=Impact:text='{text}':fontcolor=white:fontsize=48:"
            "box=1:boxcolor=black@0.6:boxborderw=5:x=(w-text_w)/2:y=10"
        )
        
        ffmpeg_args = [
            '-y',
            '-i', temp_in,
            '-filter_complex',
            f'{drawtext},fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
            '-loop', '0',
            temp_out,
        ]
        await self._process_and_send(ctx, ffmpeg_args, temp_in, temp_out)

    @commands.command()
    async def reverse(self, ctx):
        if not ctx.message.attachments:
            return await ctx.reply('attach a gif or video to reverse.')
            
        temp_in, _ = await self._download_attachment(ctx.message.attachments[0])
        temp_out = temp_in + '_rev.gif'
        
        ffmpeg_args = [
            '-y',
            '-i', temp_in,
            '-filter_complex',
            'reverse,fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
            '-loop', '0',
            temp_out,
        ]
        await self._process_and_send(ctx, ffmpeg_args, temp_in, temp_out)

    @commands.command()
    async def speed(self, ctx, multiplier: float):
        if not ctx.message.attachments:
            return await ctx.reply('attach a gif or video to adjust speed.')
            
        if multiplier <= 0:
            return await ctx.reply('multiplier must be positive.')
            
        temp_in, _ = await self._download_attachment(ctx.message.attachments[0])
        temp_out = temp_in + '_spd.gif'
        
        ffmpeg_args = [
            '-y',
            '-i', temp_in,
            '-filter_complex',
            f'setpts=PTS*{1/ multiplier},fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
            '-loop', '0',
            temp_out,
        ]
        await self._process_and_send(ctx, ffmpeg_args, temp_in, temp_out)

async def setup(bot):
    await bot.add_cog(FfmpegThings(bot))
