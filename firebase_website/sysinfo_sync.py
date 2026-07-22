"""Push system stats (CPU, RAM, disk, uptime) to Firestore via Admin SDK."""
######################################################################
import asyncio
import logging
import re
import time

import psutil

from .config import get_db, SYSINFO_INTERVAL

log = logging.getLogger(__name__)

FASTFETCH_CACHE_SECS = 300  # refresh fastfetch every 5 minutes


def _fmt_size(b: int) -> str:
    """Bytes → human-readable string (auto-scales B → TB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


class SysInfoSync:
    """Background task: collect system stats and push to Firestore."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._fastfetch: str = ""
        self._fastfetch_ts: float = 0

    # ── public ──────────────────────────────────────────────────────────

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

    # ── loop ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            try:
                await self._push()
            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("SysInfo push failed", exc_info=True)
            await asyncio.sleep(SYSINFO_INTERVAL)

    # ── push ────────────────────────────────────────────────────────────

    async def _push(self) -> None:
        stats = await self._collect_stats()
        fetch = await self._get_fastfetch()

        payload = {
            "online":    True,
            "cpu":       stats["cpu"],
            "ram":       stats["ram"],
            "ramUsed":   stats["ram_used"],
            "ramTotal":  stats["ram_total"],
            "disk":      stats["disk"],
            "diskUsed":  stats["disk_used"],
            "diskTotal": stats["disk_total"],
            "swap":      stats["swap"],
            "swapUsed":  stats["swap_used"],
            "swapTotal": stats["swap_total"],
            "uptime":    stats["uptime"],
            "load1":     stats["load1"],
            "load5":     stats["load5"],
            "load15":    stats["load15"],
            "netSent":   stats["net_sent"],
            "netRecv":   stats["net_recv"],
            "fetch":     fetch,
        }

        db = get_db()
        await db.collection("sysinfo").document("server").set(payload, merge=True)

    # ── collectors ──────────────────────────────────────────────────────

    async def _collect_stats(self) -> dict:
        """Gather all psutil stats; offloads blocking calls to a thread."""
        cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        swap = psutil.swap_memory()
        load1, load5, load15 = psutil.getloadavg()
        net = psutil.net_io_counters()
        uptime_seconds = int(time.time() - psutil.boot_time())

        days, r = divmod(uptime_seconds, 86400)
        hours, r = divmod(r, 3600)
        minutes, _ = divmod(r, 60)
        uptime_str = f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"

        return {
            "cpu":       round(cpu, 1),
            "ram":       round(mem.percent, 1),
            "ram_used":  _fmt_size(mem.used),
            "ram_total": _fmt_size(mem.total),
            "disk":      round(disk.percent, 1),
            "disk_used": _fmt_size(disk.used),
            "disk_total": _fmt_size(disk.total),
            "swap":      round(swap.percent, 1),
            "swap_used": _fmt_size(swap.used),
            "swap_total": _fmt_size(swap.total),
            "uptime":    uptime_str,
            "load1":     round(load1, 1),
            "load5":     round(load5, 1),
            "load15":    round(load15, 1),
            "net_sent":  _fmt_size(net.bytes_sent),
            "net_recv":  _fmt_size(net.bytes_recv),
        }

    async def _get_fastfetch(self) -> str:
        """Return cached fastfetch output; refreshed every 5 minutes."""
        now = time.monotonic()
        if self._fastfetch and (now - self._fastfetch_ts) < FASTFETCH_CACHE_SECS:
            return self._fastfetch

        try:
            proc = await asyncio.create_subprocess_exec(
                "fastfetch", "--pipe", "--logo", "none",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", stdout.decode())
            lines = [line for line in clean.strip().splitlines() if line.strip()]
            self._fastfetch = "\n".join(lines[:15])
            self._fastfetch_ts = now
        except Exception:
            if not self._fastfetch:
                self._fastfetch = ""
        return self._fastfetch
