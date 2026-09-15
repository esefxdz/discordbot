"""FFmpeg media conversion cog.

Commands operate on a message attachment, or the attachment of a replied-to
message: !togif, !caption <text>, !reverse, !speed <x>, !tomp4, !tomp3,
!toopus. All ffmpeg invocations use argument lists (no shell) and captions
are passed via a textfile to avoid drawtext injection.
"""
import asyncio
import os
import shutil
import tempfile

import discord
from discord.ext import commands

MAX_UPLOAD = 50 * 1024 * 1024          # 50 MB input cap
MAX_DURATION = 600                      # 10 min output cap
TIMEOUT = 120                           # ffmpeg wall-clock cap (seconds)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


class FfmpegThings(commands.Cog):
    """FFmpeg media utilities."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_attachment(self, message: discord.Message):
        if message.attachments:
            return message.attachments[0]
        ref = message.reference
        if ref is not None:
            resolved = ref.resolved
            if isinstance(resolved, discord.Message) and resolved.attachments:
                return resolved.attachments[0]
        return None

    async def _transcode(self, ctx: commands.Context, args: list[str], ext: str):
        attachment = await self._get_attachment(ctx.message)
        if attachment is None:
            await ctx.reply("Attach a file, or reply to a message that has one.")
            return
        if attachment.size > MAX_UPLOAD:
            await ctx.reply(f"File too big ({attachment.size // (1024*1024)} MB). Max {MAX_UPLOAD // (1024*1024)} MB.")
            return

        tmpdir = tempfile.mkdtemp(prefix="ffmpeg_")
        inp = os.path.join(tmpdir, "in" + os.path.splitext(attachment.filename)[1])
        out = os.path.join(tmpdir, "out" + ext)
        try:
            await attachment.save(inp)
            await self._run_ffmpeg(["-i", inp, *args, out], tmpdir)
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                raise RuntimeError("ffmpeg produced no output")
            await ctx.reply(file=discord.File(out))
        except Exception as exc:
            await ctx.reply(f"ffmpeg failed: {exc}"[:2000])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _run_ffmpeg(self, args: list[str], tmpdir: str):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-t", str(MAX_DURATION), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("ffmpeg timed out")
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[:500])

    @commands.command()
    async def togif(self, ctx: commands.Context):
        """Convert a video to a GIF."""
        await self._transcode(
            ctx,
            ["-vf", "fps=10,scale='min(480,iw)':-2:flags=lanczos"],
            ".gif",
        )

    @commands.command()
    async def tomp4(self, ctx: commands.Context):
        """Convert media to MP4 (h264/aac)."""
        await self._transcode(
            ctx,
            ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
            ".mp4",
        )

    @commands.command()
    async def tomp3(self, ctx: commands.Context):
        """Extract audio to MP3."""
        await self._transcode(
            ctx,
            ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
            ".mp3",
        )

    @commands.command()
    async def toopus(self, ctx: commands.Context):
        """Extract audio to Opus."""
        await self._transcode(
            ctx,
            ["-vn", "-c:a", "libopus", "-b:a", "128k"],
            ".opus",
        )

    @commands.command()
    async def reverse(self, ctx: commands.Context):
        """Reverse a video/audio clip."""
        await self._transcode(
            ctx,
            ["-vf", "reverse", "-af", "areverse"],
            ".mp4",
        )

    @commands.command()
    async def speed(self, ctx: commands.Context, factor: float = 1.0):
        """Change playback speed (0.5 to 2.0)."""
        if not 0.5 <= factor <= 2.0:
            await ctx.reply("Speed must be between 0.5 and 2.0.")
            return
        await self._transcode(
            ctx,
            ["-filter_complex",
             f"[0:v]setpts=PTS/{factor}[v];[0:a]atempo={factor}[a]",
             "-map", "[v]", "-map", "[a]"],
            ".mp4",
        )

    @commands.command()
    async def caption(self, ctx: commands.Context, *, text: str):
        """Add a caption to the top of a video."""
        text = text.strip()
        if not text:
            await ctx.reply("Provide a caption, e.g. `!caption hello world`.")
            return

        attachment = await self._get_attachment(ctx.message)
        if attachment is None:
            await ctx.reply("Attach a file, or reply to a message that has one.")
            return
        if attachment.size > MAX_UPLOAD:
            await ctx.reply(f"File too big ({attachment.size // (1024*1024)} MB).")
            return

        font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        tmpdir = tempfile.mkdtemp(prefix="ffmpeg_cap_")
        inp = os.path.join(tmpdir, "in" + os.path.splitext(attachment.filename)[1])
        caption_file = os.path.join(tmpdir, "caption.txt")
        out = os.path.join(tmpdir, "out.mp4")
        try:
            await attachment.save(inp)
            with open(caption_file, "w", encoding="utf-8") as f:
                f.write(text)
            font_arg = f":fontfile={font}" if font else ""
            vf = (
                f"drawtext=textfile={caption_file}:fontsize=40:fontcolor=white"
                f":box=1:boxcolor=black@0.5:boxborderw=10:x=(w-text_w)/2:y=20{font_arg}"
            )
            await self._run_ffmpeg(
                ["-i", inp, "-vf", vf, "-c:a", "copy", out],
                tmpdir,
            )
            await ctx.reply(file=discord.File(out))
        except Exception as exc:
            await ctx.reply(f"ffmpeg failed: {exc}"[:2000])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FfmpegThings(bot))
