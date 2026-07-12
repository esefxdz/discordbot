"""ICY (SHOUTcast/Icecast) metadata poller — reads live track titles from radio streams."""
import asyncio
import logging
import re
import ssl
from typing import Awaitable, Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"StreamTitle='([^']*)';")

# Some servers send a "StreamUrl='...'" which we can ignore
_CHUNK = 8192


class IcyMetadataPoller:
    """Polls an Icecast/SHOUTcast stream for ICY metadata and fires a callback on title change.

    Usage:
        poller = IcyMetadataPoller("http://...", callback)
        task = asyncio.create_task(poller.run())
        ...
        poller.stop()
        task.cancel()
    """

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
        while not self._stop.is_set():
            try:
                await self._poll_loop()
            except asyncio.CancelledError:
                return
            except Exception:
                log.debug(f"ICY poller disconnected, reconnecting in 10s: {self._url}")
            # Back off before reconnecting
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10.0)
                return  # stop() was called
            except asyncio.TimeoutError:
                pass

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

        # TLS for HTTPS
        ssl_ctx = ssl.create_default_context() if parsed.scheme == "https" else None

        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
        try:
            # Send HTTP GET with Icy-MetaData header
            request = (
                f"GET {path} HTTP/1.0\r\n"
                f"Host: {host}\r\n"
                f"Icy-MetaData: 1\r\n"
                f"User-Agent: yuuka-radio/1.0\r\n"
                f"\r\n"
            )
            writer.write(request.encode())
            await writer.drain()

            # Read status line + headers
            headers = await self._read_headers(reader)

            metaint = int(headers.get("icy-metaint", 0))
            if metaint <= 0:
                log.debug(f"No ICY metadata on {self._url}")
                return  # stream has no metadata support

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
                        try:
                            await self._on_title(title)
                        except Exception:
                            log.debug("ICY callback error", exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
        """Read HTTP response headers into a lowercase-keyed dict."""
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                break  # empty line = end of headers
            if ":" in text:
                key, _, value = text.partition(":")
                headers[key.strip().lower()] = value.strip()
        return headers

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
