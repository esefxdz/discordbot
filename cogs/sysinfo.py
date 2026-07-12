import asyncio
import logging
import re
from discord.ext import commands
import psutil
import aiohttp

log = logging.getLogger(__name__)


class SysInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\[[0-9]+[A-Z]', '', text)

    @staticmethod
    async def _run_command(*args: str, timeout: float = 8) -> str:
        """Run a shell command asynchronously, return stdout or '' on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode().strip()
        except Exception:
            log.debug("Command failed: %s", args, exc_info=True)
            return ""

    # ── commands ────────────────────────────────────────────────────────

    @commands.command()
    async def sysinfo(self, ctx):
        """List available sysinfo commands."""
        cmds = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(cmds))

    @commands.command(name="fetch")
    async def fetch_cmd(self, ctx):
        """Show system info via fastfetch."""
        out = await self._run_command("fastfetch", "--pipe", "--logo", "none")
        if not out:
            return await ctx.reply("fastfetch not available")
        clean = self._strip_ansi(out)
        await ctx.reply(f'```\n{clean}\n```')

    @commands.command()
    async def uptime(self, ctx):
        out = await self._run_command("uptime", "-p")
        await ctx.reply(f'⏱️ {out}' if out else "uptime not available")

    @commands.command()
    async def temps(self, ctx):
        out = await self._run_command("sensors")
        await ctx.reply(f'```\n{out}\n```' if out else "sensors not available")

    @commands.command()
    async def gpu(self, ctx):
        out = await self._run_command("envycontrol", "--query")
        await ctx.reply(f'I am on {out} mode esef!' if out else "envycontrol not available")

    @commands.command()
    async def top(self, ctx):
        out = await self._run_command("ps", "aux", "--sort=-%cpu")
        if not out:
            return await ctx.reply("ps not available")
        lines = out.splitlines()
        top5 = '\n'.join(lines[:6])
        await ctx.reply(f'```\n{top5}\n```')

    @commands.command()
    async def mem(self, ctx):
        mem = psutil.virtual_memory()
        await ctx.reply(f'💾 Memory Usage: {mem.percent}% ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)')

    @commands.command()
    async def cpu(self, ctx):
        cpu_percent = psutil.cpu_percent(interval=1)
        await ctx.reply(f'⚡ CPU Usage: {cpu_percent}%')

    @commands.command()
    async def load(self, ctx):
        load1, load5, load15 = psutil.getloadavg()
        await ctx.reply(f'📊 Load Average: {load1:.2f}, {load5:.2f}, {load15:.2f}')

    @commands.command()
    async def net(self, ctx):
        net = psutil.net_io_counters()
        await ctx.reply(f'📡 Network I/O: {net.bytes_sent} sent, {net.bytes_recv} received')

    @commands.command()
    async def battery(self, ctx):
        battery = psutil.sensors_battery()
        if battery:
            await ctx.reply(f'Battery: {battery.percent}% {"Charging" if battery.power_plugged else "Not Charging"}')

    @commands.command()
    async def processes(self, ctx):
        processes = psutil.pids()
        await ctx.reply(f'Processes: {len(processes)} running processes')

    @commands.command()
    async def stats(self, ctx):
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        reply = f'CPU: {cpu}% | RAM: {ram}% | Disk: {disk}%'
        await ctx.reply(reply)

    @commands.command()
    async def strinova(self, ctx):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=1282270') as r:
                data = await r.json()
                count = data['response']['player_count']
                await ctx.reply(f'🎮 Strinova — {count:,} players online right now')


async def setup(bot):
    await bot.add_cog(SysInfo(bot))
