import asyncio
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands

# ── config ───────────────────────────────────────────────────────────────────
OWNER_ID      = int(os.getenv("OWNER_ID", "0"))
SUDO_PASSWORD = os.getenv("SUDO_PASSWORD", "")
BOT_DIR       = os.path.expanduser("~/discordbot")
SERVICE_NAME  = "yuuka"

GIT_TIMEOUT     = 30
SYSTEMD_TIMEOUT = 15

# ── colors ───────────────────────────────────────────────────────────────────
C_OK   = 0x50FA7B
C_ERR  = 0xFF5555
C_WARN = 0xFFB86C
C_INFO = 0x8BE9FD


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
    return await _run(
        "git", "log", f"--oneline", f"-{n}",
        cwd=BOT_DIR, timeout=GIT_TIMEOUT,
    )


async def run_sudo_systemctl(action: str) -> tuple[int, str]:
    return await _run(
        "sudo", "-S", "systemctl", action, SERVICE_NAME,
        input_data=f"{SUDO_PASSWORD}\n".encode() if SUDO_PASSWORD else None,
        timeout=SYSTEMD_TIMEOUT,
    )


async def run_journalctl(n: int = 50) -> tuple[int, str]:
    return await _run(
        "sudo", "-S", "journalctl", "-u", SERVICE_NAME,
        "-n", str(n), "--no-pager",
        input_data=f"{SUDO_PASSWORD}\n".encode() if SUDO_PASSWORD else None,
        timeout=SYSTEMD_TIMEOUT,
    )


def truncate(text: str, limit: int = 1900) -> str:
    if len(text) > limit:
        return text[:limit] + "\n… (truncated)"
    return text


def _embed(title: str, description: str, color: int) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


# ── cog ──────────────────────────────────────────────────────────────────────
class GitPull(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_owner(self, ctx: commands.Context) -> bool:
        return ctx.author.id == OWNER_ID

    async def _do_restart(self, ctx: commands.Context):
        await ctx.reply(embed=_embed(
            "⟳  Restarting",
            f"Restarting `{SERVICE_NAME}` — see you on the other side.",
            C_WARN,
        ))
        try:
            await run_sudo_systemctl("restart")
        except Exception:
            pass  # process dies mid-call, that's expected

    # ── !gitpull ──────────────────────────────────────────────────────────────

    @commands.command(name="gitpull")
    async def gitpull(self, ctx: commands.Context):
        """Pull latest changes from git without restarting."""
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_git_pull()

        if code != 0:
            return await ctx.reply(embed=_embed(
                "✗  Pull Failed",
                f"```\n{truncate(output)}\n```",
                C_ERR,
            ))

        if "Already up to date" in output:
            return await ctx.reply(embed=_embed(
                "✓  Already Up to Date",
                "Nothing new to pull.",
                C_WARN,
            ))

        await ctx.reply(embed=_embed(
            "✓  Pulled",
            f"```\n{truncate(output)}\n```",
            C_OK,
        ))

    # ── !gitlog ───────────────────────────────────────────────────────────────

    @commands.command(name="gitlog")
    async def gitlog(self, ctx: commands.Context, n: int = 5):
        """Show the last N commits. Default: 5."""
        if not self._is_owner(ctx):
            return
        n = max(1, min(n, 20))

        async with ctx.typing():
            code, output = await run_git_log(n)

        color = C_OK if code == 0 else C_ERR
        await ctx.reply(embed=_embed(
            f"📋  Last {n} Commits",
            f"```\n{truncate(output)}\n```",
            color,
        ))

    # ── !logs ─────────────────────────────────────────────────────────────────

    @commands.command(name="logs")
    async def logs(self, ctx: commands.Context, n: int = 40):
        """Show the last N lines of the service journal. Default: 40."""
        if not self._is_owner(ctx):
            return
        n = max(1, min(n, 100))

        async with ctx.typing():
            code, output = await run_journalctl(n)

        color = C_OK if code == 0 else C_ERR
        await ctx.reply(embed=_embed(
            f"📄  Logs — {SERVICE_NAME} (last {n} lines)",
            f"```\n{truncate(output)}\n```",
            color,
        ))

    # ── !status ───────────────────────────────────────────────────────────────

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        """Show systemd service status."""
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_sudo_systemctl("status")

        color = C_OK if code == 0 else C_ERR
        await ctx.reply(embed=_embed(
            f"{'✓' if code == 0 else '✗'}  Status — {SERVICE_NAME}",
            f"```\n{truncate(output)}\n```",
            color,
        ))

    # ── !restart ──────────────────────────────────────────────────────────────

    @commands.command(name="restart")
    async def restart(self, ctx: commands.Context):
        """Restart the bot service."""
        if not self._is_owner(ctx):
            return
        await self._do_restart(ctx)

    # ── !update ───────────────────────────────────────────────────────────────

    @commands.command(name="update")
    async def update(self, ctx: commands.Context):
        """Pull latest changes and restart if anything changed."""
        if not self._is_owner(ctx):
            return

        async with ctx.typing():
            code, output = await run_git_pull()

        if code != 0:
            return await ctx.reply(embed=_embed(
                "✗  Update Failed",
                f"Pull failed — restart skipped.\n```\n{truncate(output)}\n```",
                C_ERR,
            ))

        if "Already up to date" in output:
            return await ctx.reply(embed=_embed(
                "✓  Already Up to Date",
                "Nothing new to pull. Restart skipped.",
                C_WARN,
            ))

        await ctx.reply(embed=_embed(
            "✓  Updated",
            f"```\n{truncate(output)}\n```",
            C_OK,
        ))
        await self._do_restart(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(GitPull(bot))
