"""Push system stats (CPU, RAM, disk, uptime) to Firestore on a loop."""
######################################################################
import asyncio
import logging
import re
import time

import aiohttp
import psutil

from .config import DOC_SYSINFO, SYSINFO_INTERVAL

log = logging.getLogger(__name__)


def _fmt_bytes(b: int) -> str:
    """Format a byte count as a human-readable string (e.g. '1.2 GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


class SysInfoSync:
    """Background task that collects system stats and pushes them to Firestore."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._tick = 0

    # ── public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the background sync loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("SysInfo → Firestore sync started (every %ds)", SYSINFO_INTERVAL)

    def stop(self) -> None:
        """Cancel the background sync loop."""
        if self._task is None:
            return
        self._task.cancel()
        self._task = None
        log.info("SysInfo → Firestore sync stopped")

    # ── internals ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main loop — collect stats, push, sleep, repeat."""
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._push(session)
                except asyncio.CancelledError:
                    return
                except Exception:
                    log.debug("SysInfo Firestore push failed", exc_info=True)
                await asyncio.sleep(SYSINFO_INTERVAL)

    async def _push(self, session: aiohttp.ClientSession) -> None:
        """Collect current stats and PATCH the Firestore document."""
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_seconds = int(time.time() - psutil.boot_time())

        # uptime → "3d 12h 5m" or "5h 23m"
        days, r = divmod(uptime_seconds, 86400)
        hours, r = divmod(r, 3600)
        minutes, _ = divmod(r, 60)
        if days > 0:
            uptime_str = f"{days}d {hours}h {minutes}m"
        else:
            uptime_str = f"{hours}h {minutes}m"

        load1, load5, load15 = psutil.getloadavg()
        net = psutil.net_io_counters()
        swap = psutil.swap_memory()

        payload = {
            "fields": {
                "online":    {"booleanValue": True},
                "cpu":       {"doubleValue": round(cpu, 1)},
                "ram":       {"doubleValue": round(mem.percent, 1)},
                "ramUsed":   {"stringValue": f"{mem.used // (1024 ** 2)}MB"},
                "ramTotal":  {"stringValue": f"{mem.total // (1024 ** 2)}MB"},
                "disk":      {"doubleValue": round(disk.percent, 1)},
                "diskUsed":  {"stringValue": f"{disk.used // (1024 ** 3)}GB"},
                "diskTotal": {"stringValue": f"{disk.total // (1024 ** 3)}GB"},
                "swap":      {"doubleValue": round(swap.percent, 1)},
                "swapUsed":  {"stringValue": f"{swap.used // (1024 ** 2)}MB"},
                "swapTotal": {"stringValue": f"{swap.total // (1024 ** 2)}MB"},
                "uptime":    {"stringValue": uptime_str},
                "load1":     {"doubleValue": round(load1, 1)},
                "load5":     {"doubleValue": round(load5, 1)},
                "load15":    {"doubleValue": round(load15, 1)},
                "netSent":   {"stringValue": _fmt_bytes(net.bytes_sent)},
                "netRecv":   {"stringValue": _fmt_bytes(net.bytes_recv)},
                "processes": {"integerValue": len(psutil.pids())},
                "fetch":     {"stringValue": await self._get_fastfetch()},
            }
        }

        async with session.patch(DOC_SYSINFO, json=payload) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                log.debug("Firestore PATCH %d: %s", resp.status, body[:200])

    @staticmethod
    async def _get_fastfetch() -> str:
        """Run fastfetch --pipe --logo none, strip ANSI, return first 15 lines."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "fastfetch", "--pipe", "--logo", "none",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\[[0-9]+[A-Z]", "", stdout.decode())
            lines = [l for l in clean.strip().splitlines() if l.strip()]
            return "\n".join(lines[:15])
        except Exception:
            return ""
