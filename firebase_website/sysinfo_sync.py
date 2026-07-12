"""Push system stats (CPU, RAM, disk, uptime) to Firestore on a loop."""
######################################################################
import asyncio
import logging
import time

import aiohttp
import psutil

from .config import DOC_SYSINFO, SYSINFO_INTERVAL

log = logging.getLogger(__name__)


class SysInfoSync:
    """Background task that collects system stats and pushes them to Firestore."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

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
                "uptime":    {"stringValue": uptime_str},
            }
        }

        async with session.patch(DOC_SYSINFO, json=payload) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                log.debug("Firestore PATCH %d: %s", resp.status, body[:200])
