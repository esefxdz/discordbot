# will fix soon tm
"""FFmpeg media conversion cog — temporarily disabled.

Original commands: !togif, !caption, !reverse, !speed, !tomp4, !tomp3, !toopus.
Re-enabled once drawtext injection and resource-exhaustion concerns are addressed.
"""

from discord.ext import commands


class FfmpegThings(commands.Cog):
    """FFmpeg media utilities (disabled)."""

    def __init__(self, bot):
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FfmpegThings(bot))
