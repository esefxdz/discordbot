"""ICY (SHOUTcast/Icecast) metadata poller — reads live track titles from radio streams."""
import asyncio
import logging
import re
import ssl
from typing import Awaitable, Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"StreamTitle='([^']*)';")
_CHUNK = 8192


class IcyMetadataPoller:
    """Polls an Icecast/SHOUTcast stream for ICY metadata and fires a callback on title change."""

    def __init__(
        self,
        url: str,
        on_title: Callable[[str], Awaitable[None]],
    ):
        self._url = url
        self._on_title = on_title
        self._stop = asyncio.Event()
        self._last_title: str | None = None

    # ── public API ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main loop — connect, poll, reconnect on failure."""
        log.info("ICY poller started for %s", self._url)
        while not self._stop.is_set():
            try:
                await self._poll_loop()
            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("ICY poller error for %s, retrying in 10s", self._url, exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10.0)
                return
            except asyncio.TimeoutError:
                pass
        log.info("ICY poller stopped for %s", self._url)

    def stop(self) -> None:
        """Signal the poller to stop (doesn't cancel the task)."""
        self._stop.set()

    # ── internals ───────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        parsed = urlparse(self._url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        ssl_ctx = ssl.create_default_context() if parsed.scheme == "https" else None
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
        try:
            request = (
                f"GET {path} HTTP/1.0\r\n"
                f"Host: {host}\r\n"
                f"Icy-MetaData: 1\r\n"
                f"User-Agent: yuuka-radio/1.0\r\n"
                f"\r\n"
            )
            writer.write(request.encode())
            await writer.drain()

            # Read status line
            status_line = await reader.readline()
            status_text = status_line.decode("utf-8", errors="replace").strip()
            log.info("ICY %s → %s", self._url, status_text)

            # Follow redirect (301/302)
            redirect_count = 0
            while "30" in status_text.split(" ")[:2] and redirect_count < 5:
                headers, _ = await self._read_headers(reader)
                location = headers.get("location", "")
                writer.close()
                if not location:
                    log.warning("ICY redirect without Location header: %s", self._url)
                    return
                log.info("ICY redirect %s → %s", self._url, location)
                parsed = urlparse(location)
                host = parsed.hostname or host
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                ssl_ctx = ssl.create_default_context() if parsed.scheme == "https" else None
                reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
                # Re-send request
                request2 = (
                    f"GET {path} HTTP/1.0\r\n"
                    f"Host: {host}\r\n"
                    f"Icy-MetaData: 1\r\n"
                    f"User-Agent: yuuka-radio/1.0\r\n"
                    f"\r\n"
                )
                writer.write(request2.encode())
                await writer.drain()
                status_line = await reader.readline()
                status_text = status_line.decode("utf-8", errors="replace").strip()
                redirect_count += 1

            # Read remaining headers
            headers, _ = await self._read_headers(reader)
            metaint = int(headers.get("icy-metaint", 0))
            if metaint <= 0:
                log.warning("ICY no metaint for %s (status: %s, got %d headers)",
                            self._url, status_text, len(headers))
                return

            log.info("ICY polling %s — metaint=%d", self._url, metaint)

            # Poll: skip audio → read metadata length → parse title
            while not self._stop.is_set():
                await self._skip(reader, metaint)
                length_byte = await reader.readexactly(1)
                length = length_byte[0] * 16
                if length > 0:
                    raw = await reader.readexactly(length)
                    title = self._extract_title(raw.decode("utf-8", errors="replace"))
                    if title and title != self._last_title:
                        self._last_title = title
                        log.info("ICY now playing: %s", title)
                        try:
                            await self._on_title(title)
                        except Exception:
                            log.warning("ICY callback error", exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> tuple[dict[str, str], str]:
        """Read headers into a lowercase-keyed dict. Returns (headers, raw_text)."""
        headers: dict[str, str] = {}
        raw = ""
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            raw += text + "\n"
            if not text:
                break
            if ":" in text:
                key, _, value = text.partition(":")
                headers[key.strip().lower()] = value.strip()
        return headers, raw

    @staticmethod
    async def _skip(reader: asyncio.StreamReader, n: int) -> None:
        """Read and discard exactly *n* bytes from the stream."""
        remaining = n
        while remaining > 0:
            chunk = await reader.read(min(remaining, _CHUNK))
            if not chunk:
                raise ConnectionError("ICY stream ended unexpectedly")
            remaining -= len(chunk)

    @staticmethod
    def _extract_title(metadata: str) -> str | None:
        """Parse ``StreamTitle='Artist - Title';`` from an ICY metadata chunk."""
        m = _TITLE_RE.search(metadata)
        if not m:
            return None
        title = m.group(1).strip()
        return title if title else None
