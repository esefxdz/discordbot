import discord
from discord.ext import commands
from .constants import C_ERR


def fmt_duration(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def progress_bar(position_ms: int, duration_ms: int, width: int = 14) -> str:
    if duration_ms == 0:
        return "▬" * width
    pct    = min(position_ms / duration_ms, 1.0)
    filled = int(pct * width)
    return "▬" * filled + "🔘" + "▬" * (width - filled)


def is_dj():
    """Soft DJ check — allows anyone to use info/play, restricts control commands."""
    async def predicate(ctx: commands.Context):
        if any(r.name.lower() == "dj" for r in ctx.author.roles):
            return True
        await ctx.reply(
            embed=discord.Embed(
                description="🎧 You need the **DJ** role to use this command.",
                color=C_ERR,
            )
        )
        return False
    return commands.check(predicate)
