import asyncio
import os
import discord
from discord.ext import commands

# ── config ───────────────────────────────────────────────────────────────────
OWNER_ID      = int(os.getenv("OWNER_ID", "0"))
SUDO_PASSWORD = os.getenv("SUDO_PASSWORD", "")
BOT_DIR       = os.path.expanduser("~/discordbot")
SERVICE_NAME  = "yuuka"

GIT_TIMEOUT    = 30
SYSTEMD_TIMEOUT = 15

# ── colors (kept local so this cog has no dependency on music.py) ───────────
C_OK   = 0x50FA7B
C_ERR  = 0xFF5555
C_WARN = 0xFFB86C


# ── subprocess helpers ────────────────────────────────────────────────────────
async def run_git_pull() -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "pull",
        cwd=BOT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, f"git pull timed out after {GIT_TIMEOUT}s"

    raw = (stdout.decode() + stderr.decode()).strip()
    output = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("[sudo]")
    )
    return proc.returncode, output or "No output."


async def run_sudo_systemctl(action: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-S", "systemctl", action, SERVICE_NAME,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=f"{SUDO_PASSWORD}\n".encode()),
            timeout=SYSTEMD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, f"systemctl {action} timed out after {SYSTEMD_TIMEOUT}s"

    output = (stdout.decode() + stderr.decode()).strip()
    return proc.returncode, output or "No output."


def truncate(text: str, limit: int = 1900) -> str:
    if len(text) > limit:
        return text[:limit] + "\n... (truncated)"
    return text


# ── cog ──────────────────────────────────────────────────────────────────────
class GitPull(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_owner(self, ctx: commands.Context) -> bool:
        return ctx.author.id == OWNER_ID

    async def _do_restart(self, ctx: commands.Context):
        await ctx.reply(embed=discord.Embed(
            description="Restarting service...", color=C_WARN))
        try:
            await run_sudo_systemctl("restart")
        except Exception:
            # process is expected to die mid-call once the service restarts,
            # so failures here are not reported back
            pass

    @commands.command(name="gitpull")
    async def gitpull(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_git_pull()

        color = C_OK if code == 0 else C_ERR
        embed = discord.Embed(
            title="Git Pull",
            description=f"```\n{truncate(output)}\n```",
            color=color,
        )
        await ctx.reply(embed=embed)

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_sudo_systemctl("status")

        color = C_OK if code == 0 else C_ERR
        embed = discord.Embed(
            title=f"Service Status: {SERVICE_NAME}",
            description=f"```\n{truncate(output)}\n```",
            color=color,
        )
        await ctx.reply(embed=embed)

    @commands.command(name="restart")
    async def restart(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return
        await self._do_restart(ctx)

    @commands.command(name="update")
    async def update(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_git_pull()

        if code != 0:
            embed = discord.Embed(
                title="Update Failed",
                description=f"git pull failed, restart skipped.\n```\n{truncate(output)}\n```",
                color=C_ERR,
            )
            return await ctx.reply(embed=embed)

        if "Already up to date" in output:
            embed = discord.Embed(
                title="Update",
                description="No changes to pull. Restart skipped.",
                color=C_WARN,
            )
            return await ctx.reply(embed=embed)

        embed = discord.Embed(
            title="Update",
            description=f"Pulled changes:\n```\n{truncate(output)}\n```",
            color=C_OK,
        )
        await ctx.reply(embed=embed)
        await self._do_restart(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(GitPull(bot))
