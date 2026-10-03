"""FFmpeg media conversion cog.

Slash commands that take an uploaded file: /togif, /caption <text>,
/reverse, /speed <x>, /tomp4, /tomp3, /toopus. All ffmpeg invocations use
argument lists (no shell) and captions are passed via a textfile to avoid
drawtext injection.
"""
import asyncio
import os
import shutil
import tempfile

import discord
from discord import app_commands
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

    async def _transcode(self, interaction: discord.Interaction, file: discord.Attachment,
                         args: list[str], ext: str, caption: str | None = None):
        if file.size > MAX_UPLOAD:
            await interaction.response.send_message(
                f"File too big ({file.size // (1024*1024)} MB). Max {MAX_UPLOAD // (1024*1024)} MB.",
                ephemeral=True)
            return

        # ffmpeg easily takes longer than the 3s interaction window
        await interaction.response.defer(thinking=True)

        tmpdir = tempfile.mkdtemp(prefix="ffmpeg_")
        inp = os.path.join(tmpdir, "in" + os.path.splitext(file.filename)[1])
        out = os.path.join(tmpdir, "out" + ext)
        try:
            await file.save(inp)
            if caption is not None:
                caption_file = os.path.join(tmpdir, "caption.txt")
                with open(caption_file, "w", encoding="utf-8") as f:
                    f.write(caption)
                args = [a.replace("{caption_file}", caption_file) for a in args]
            await self._run_ffmpeg(["-i", inp, *args, out], tmpdir)
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                raise RuntimeError("ffmpeg produced no output")
            await interaction.followup.send(file=discord.File(out))
        except Exception as exc:
            await interaction.followup.send(f"ffmpeg failed: {exc}"[:2000])
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

    @app_commands.command()
    @app_commands.describe(file="Video to convert")
    async def togif(self, interaction: discord.Interaction, file: discord.Attachment):
        """Convert a video to a GIF."""
        await self._transcode(
            interaction, file,
            ["-vf", "fps=10,scale='min(480,iw)':-2:flags=lanczos"],
            ".gif",
        )

    @app_commands.command()
    @app_commands.describe(file="Video or audio to convert")
    async def tomp4(self, interaction: discord.Interaction, file: discord.Attachment):
        """Convert media to MP4 (h264/aac)."""
        await self._transcode(
            interaction, file,
            ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
            ".mp4",
        )

    @app_commands.command()
    @app_commands.describe(file="Video or audio to extract from")
    async def tomp3(self, interaction: discord.Interaction, file: discord.Attachment):
        """Extract audio to MP3."""
        await self._transcode(
            interaction, file,
            ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
            ".mp3",
        )

    @app_commands.command()
    @app_commands.describe(file="Video or audio to extract from")
    async def toopus(self, interaction: discord.Interaction, file: discord.Attachment):
        """Extract audio to Opus."""
        await self._transcode(
            interaction, file,
            ["-vn", "-c:a", "libopus", "-b:a", "128k"],
            ".opus",
        )

    @app_commands.command()
    @app_commands.describe(file="Video to reverse")
    async def reverse(self, interaction: discord.Interaction, file: discord.Attachment):
        """Reverse a video/audio clip."""
        await self._transcode(
            interaction, file,
            ["-vf", "reverse", "-af", "areverse"],
            ".mp4",
        )

    @app_commands.command()
    @app_commands.describe(file="Video to speed up or slow down",
                           factor="Playback speed, 0.5 to 2.0")
    async def speed(self, interaction: discord.Interaction, file: discord.Attachment,
                    factor: app_commands.Range[float, 0.5, 2.0] = 1.0):
        """Change playback speed (0.5 to 2.0)."""
        await self._transcode(
            interaction, file,
            ["-filter_complex",
             f"[0:v]setpts=PTS/{factor}[v];[0:a]atempo={factor}[a]",
             "-map", "[v]", "-map", "[a]"],
            ".mp4",
        )

    @app_commands.command()
    @app_commands.describe(file="Video to caption", text="Text to put on top")
    async def caption(self, interaction: discord.Interaction, file: discord.Attachment,
                      text: str):
        """Add a caption to the top of a video."""
        text = text.strip()
        if not text:
            await interaction.response.send_message("Provide a caption.", ephemeral=True)
            return

        font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        font_arg = f":fontfile={font}" if font else ""
        vf = (
            "drawtext=textfile={caption_file}:fontsize=40:fontcolor=white"
            f":box=1:boxcolor=black@0.5:boxborderw=10:x=(w-text_w)/2:y=20{font_arg}"
        )
        await self._transcode(
            interaction, file,
            ["-vf", vf, "-c:a", "copy"],
            ".mp4",
            caption=text,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FfmpegThings(bot))
