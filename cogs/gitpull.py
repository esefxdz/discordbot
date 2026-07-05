import asyncio
import os

import discord
from discord.ext import commands

# ── config ───────────────────────────────────────────────────────────────────
OWNER_ID      = int(os.getenv("OWNER_ID", "0"))
SUDO_PASSWORD = os.getenv("SUDO_PASSWORD", "")
BOT_DIR       = os.path.expanduser("~/discordbot")
SERVICE_NAME  = "yuuka"

GIT_TIMEOUT     = 30
SYSTEMD_TIMEOUT = 15

C_OK   = 0x50FA7B
C_ERR  = 0xFF5555
C_WARN = 0xFFB86C


# ── subprocess helpers ────────────────────────────────────────────────────────
async def _run(*args, cwd=None, input_data=None, timeout=30) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if input_data else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=input_data),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, f"timed out after {timeout}s"

    raw = (stdout.decode() + stderr.decode()).strip()
    output = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("[sudo]")
    )
    return proc.returncode, output or "no output"


async def run_git_pull() -> tuple[int, str]:
    return await _run("git", "pull", cwd=BOT_DIR, timeout=GIT_TIMEOUT)


async def run_git_log(n: int = 5) -> tuple[int, str]:
    return await _run("git", "log", "--oneline", f"-{n}", cwd=BOT_DIR, timeout=GIT_TIMEOUT)


async def run_sudo_systemctl(action: str) -> tuple[int, str]:
    return await _run(
        "sudo", "-S", "systemctl", action, SERVICE_NAME,
        input_data=f"{SUDO_PASSWORD}\n".encode() if SUDO_PASSWORD else None,
        timeout=SYSTEMD_TIMEOUT,
    )


async def run_journalctl(n: int = 50) -> tuple[int, str]:
    return await _run(
        "sudo", "-S", "journalctl", "-u", SERVICE_NAME, "-n", str(n), "--no-pager",
        input_data=f"{SUDO_PASSWORD}\n".encode() if SUDO_PASSWORD else None,
        timeout=SYSTEMD_TIMEOUT,
    )


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
        await ctx.reply(embed=discord.Embed(description="Restarting service...", color=C_WARN))
        await asyncio.sleep(0.5)
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-S", "systemctl", "restart", SERVICE_NAME,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if SUDO_PASSWORD:
            try:
                proc.stdin.write(f"{SUDO_PASSWORD}\n".encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except Exception:
                pass

    @commands.command(name="gitpull")
    async def gitpull(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_git_pull()

        if code != 0:
            return await ctx.reply(embed=discord.Embed(
                title="Git Pull",
                description=f"```\n{truncate(output)}\n```",
                color=C_ERR,
            ))

        if "Already up to date" in output:
            return await ctx.reply(embed=discord.Embed(
                title="Git Pull",
                description="Already up to date.",
                color=C_WARN,
            ))

        await ctx.reply(embed=discord.Embed(
            title="Git Pull",
            description=f"```\n{truncate(output)}\n```",
            color=C_OK,
        ))

    @commands.command(name="gitlog")
    async def gitlog(self, ctx: commands.Context, n: int = 5):
        if not self._is_owner(ctx):
            return
        n = max(1, min(n, 20))

        async with ctx.typing():
            code, output = await run_git_log(n)

        await ctx.reply(embed=discord.Embed(
            title=f"Last {n} commits",
            description=f"```\n{truncate(output)}\n```",
            color=C_OK if code == 0 else C_ERR,
        ))

    @commands.command(name="logs")
    async def logs(self, ctx: commands.Context, n: int = 40):
        if not self._is_owner(ctx):
            return
        n = max(1, min(n, 100))

        async with ctx.typing():
            code, output = await run_journalctl(n)

        await ctx.reply(embed=discord.Embed(
            title=f"Logs ({n} lines)",
            description=f"```\n{truncate(output)}\n```",
            color=C_OK if code == 0 else C_ERR,
        ))

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_sudo_systemctl("status")

        await ctx.reply(embed=discord.Embed(
            title=f"Service Status: {SERVICE_NAME}",
            description=f"```\n{truncate(output)}\n```",
            color=C_OK if code == 0 else C_ERR,
        ))

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
            return await ctx.reply(embed=discord.Embed(
                title="Update Failed",
                description=f"git pull failed, restart skipped.\n```\n{truncate(output)}\n```",
                color=C_ERR,
            ))

        if "Already up to date" in output:
            return await ctx.reply(embed=discord.Embed(
                title="Update",
                description="No changes to pull. Restart skipped.",
                color=C_WARN,
            ))

        await ctx.reply(embed=discord.Embed(
            title="Update",
            description=f"Pulled changes:\n```\n{truncate(output)}\n```",
            color=C_OK,
        ))
        await self._do_restart(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(GitPull(bot))
