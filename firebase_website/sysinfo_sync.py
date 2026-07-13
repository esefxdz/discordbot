"""Push system stats (CPU, RAM, disk, uptime) to Firestore via Admin SDK."""
######################################################################
import asyncio
import logging
import re
import time

import psutil

from . import get_db
from .config import SYSINFO_INTERVAL

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

    # ── public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        log.info("SysInfo → Firestore sync started (every %ds)", SYSINFO_INTERVAL)

    def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None
        log.info("SysInfo → Firestore sync stopped")

    # ── internals ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            try:
                await self._push()
            except asyncio.CancelledError:
                return
            except Exception:
                log.debug("SysInfo Firestore push failed", exc_info=True)
            await asyncio.sleep(SYSINFO_INTERVAL)

    async def _push(self) -> None:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_seconds = int(time.time() - psutil.boot_time())

        days, r = divmod(uptime_seconds, 86400)
        hours, r = divmod(r, 3600)
        minutes, _ = divmod(r, 60)
        uptime_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"

        load1, load5, load15 = psutil.getloadavg()
        net = psutil.net_io_counters()
        swap = psutil.swap_memory()

        payload = {
            "online":    True,
            "cpu":       round(cpu, 1),
            "ram":       round(mem.percent, 1),
            "ramUsed":   f"{mem.used // (1024 ** 2)}MB",
            "ramTotal":  f"{mem.total // (1024 ** 2)}MB",
            "disk":      round(disk.percent, 1),
            "diskUsed":  f"{disk.used // (1024 ** 3)}GB",
            "diskTotal": f"{disk.total // (1024 ** 3)}GB",
            "swap":      round(swap.percent, 1),
            "swapUsed":  f"{swap.used // (1024 ** 2)}MB",
            "swapTotal": f"{swap.total // (1024 ** 2)}MB",
            "uptime":    uptime_str,
            "load1":     round(load1, 1),
            "load5":     round(load5, 1),
            "load15":    round(load15, 1),
            "netSent":   _fmt_bytes(net.bytes_sent),
            "netRecv":   _fmt_bytes(net.bytes_recv),
            "processes": len(psutil.pids()),
            "fetch":     await self._get_fastfetch(),
        }

        db = get_db()
        await db.collection("sysinfo").document("server").set(payload, merge=True)

    @staticmethod
    async def _get_fastfetch() -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "fastfetch", "--pipe", "--logo", "none",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\[[0-9]+[A-Z]", "", stdout.decode())
            lines = [line for line in clean.strip().splitlines() if line.strip()]
            return "\n".join(lines[:15])
        except Exception:
            return ""
