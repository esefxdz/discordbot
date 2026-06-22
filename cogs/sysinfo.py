import subprocess
from discord.ext import commands
import re
import psutil
import aiohttp

class SysInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def strip_ansi(self, text):
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\[[0-9]+[A-Z]', '', text)
    
    #this command lists all the sysinfo commands, not an actualy sysinfo command##
    @commands.command()
    async def sysinfo(self, ctx):
        sys_commands = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(sys_commands))
    ######################################################################

    @commands.command()
    async def fetch(self, ctx):
        result = subprocess.run(['fastfetch', '--pipe', '--logo', 'none'], capture_output=True, text=True)
        clean = self.strip_ansi(result.stdout)
        await ctx.reply(f'```\n{clean}\n```')

    @commands.command()
    async def uptime(self, ctx):
        result = subprocess.run(['uptime', '-p'], capture_output=True, text=True)
        await ctx.reply(f'⏱️ {result.stdout.strip()}')

    @commands.command()
    async def temps(self, ctx):
        result = subprocess.run(['sensors'], capture_output=True, text=True)
        await ctx.reply(f'```\n{result.stdout}\n```')

    @commands.command()
    async def gpu(self, ctx):
        result = subprocess.run(['envycontrol', '--query'], capture_output=True, text=True)
        await ctx.reply(f'I am on {result.stdout.strip()} mode esef!')

    @commands.command()
    async def top(self, ctx):
        result = subprocess.run(['ps', 'aux', '--sort=-%cpu'], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        top5 = '\n'.join(lines[:6])  # header + 5 processes
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
        await ctx.reply(f'Processes: {len(processes)} running processes ')
    
    @commands.command()
    async def stats(self, ctx):
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        reply = f'CPU: {cpu}% | RAM: {ram}% | Disk: {disk}%'
        await ctx.reply(reply)
    
    #this command fetches the current player count for Strinova from the Steam API
    #it's a game i play and want to show off to my friends, not an actual sysinfo command##
    #i know this is a bit of a weird place for it but i dont want to make a whole new cog just for one command
    #holy shit vscode autocomplete knew exactly what i was gonna say wtf;?
    @commands.command()
    async def strinova(self, ctx):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=1282270') as r:
                data = await r.json()
                count = data['response']['player_count']
                await ctx.reply(f'🎮 Strinova — {count:,} players online right now')


async def setup(bot):
    await bot.add_cog(SysInfo(bot))
