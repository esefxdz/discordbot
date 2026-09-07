#discord webhook poster — shared session, retries, splitting##
"""Discord webhook client for the bridge.

One shared session, allowed_mentions on every payload, 429 backoff, splitting
instead of truncation, and ?wait=true so we learn the message id for edits.
"""
######################################################################
import asyncio
import json
import logging

import aiohttp

log = logging.getLogger(__name__)

CONTENT_LIMIT = 2000
MAX_RETRIES = 4
# discord never tells us the limit (it moves with boost tier), so react to the
# actual rejection instead of guessing a threshold
_TOO_LARGE_STATUSES = {413}

NO_MENTIONS = {"parse": []}


class WebhookClient:
    """Shared-session Discord webhook poster."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=120)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def split_content(text: str, limit: int = CONTENT_LIMIT) -> list[str]:
        """Break text into discord-sized chunks on the nicest boundary."""
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        chunks, remaining = [], text
        while len(remaining) > limit:
            window = remaining[:limit]
            cut = window.rfind("\n")
            if cut < limit // 2:
                cut = window.rfind(" ")
            if cut < limit // 2:
                cut = limit                       # no sane boundary, hard cut
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        return [c for c in chunks if c]

    async def _request(self, method: str, url: str, *, build_body=None,
                       json_body=None):
        """Request with retries. build_body is a callable because a FormData
        cannot be replayed once consumed, so each attempt rebuilds it."""
        session = await self.session()
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            kwargs = {}
            if build_body is not None:
                kwargs["data"] = build_body()
            elif json_body is not None:
                kwargs["json"] = json_body
            try:
                async with session.request(method, url, **kwargs) as resp:
                    if resp.status in (200, 204):
                        self.sent += 1
                        if resp.status == 204:
                            return None
                        try:
                            return await resp.json()
                        except Exception:
                            return None

                    body = (await resp.text())[:400]

                    if resp.status == 429:
                        retry_after = delay
                        try:
                            retry_after = float((await resp.json()).get(
                                "retry_after", delay))
                        except Exception:
                            pass
                        log.warning("rate limited, waiting %.2fs", retry_after)
                        await asyncio.sleep(min(retry_after + 0.25, 30))
                        continue

                    if resp.status in _TOO_LARGE_STATUSES:
                        raise PayloadTooLarge(body)

                    if 500 <= resp.status < 600:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue

                    # 4xx won't improve on retry
                    self.failed += 1
                    self.last_error = f"{resp.status} {body}"
                    log.error("webhook %s %s -> %s %s", method, url, resp.status, body)
                    return None

            except PayloadTooLarge:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("webhook request failed (attempt %d): %s",
                            attempt + 1, exc)
                self.last_error = str(exc)
                await asyncio.sleep(delay)
                delay *= 2

        self.failed += 1
        self.last_error = self.last_error or "exhausted retries"
        return None

    # ── public api ───────────────────────────────────────────────────────

    async def send(self, webhook_url: str, username: str, content: str = "",
                   files: list | None = None, avatar_url: str | None = None):
        """Post to a webhook, splitting long text and attaching files.

        files is a list of (filename, bytes). Returns the first message, which
        is what replies and edits anchor to.
        """
        chunks = self.split_content(content) or ([""] if files else [])
        if not chunks:
            return None

        first = None
        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            attach = files if (is_last and files) else None
            payload = {
                "username": username[:80] or "unknown",
                "allowed_mentions": NO_MENTIONS,
            }
            if chunk:
                payload["content"] = chunk
            if avatar_url:
                payload["avatar_url"] = avatar_url

            result = await self._post_one(webhook_url, payload, attach)
            if index == 0:
                first = result
        return first

    async def _post_one(self, webhook_url: str, payload: dict, files):
        url = webhook_url + "?wait=true"
        if not files:
            return await self._request("POST", url, json_body=payload)

        def build():
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(payload),
                           content_type="application/json")
            for index, (filename, data) in enumerate(files):
                form.add_field(f"files[{index}]", data, filename=filename)
            return form

        try:
            return await self._request("POST", url, build_body=build)
        except PayloadTooLarge:
            names = ", ".join(name for name, _ in files)
            log.info("upload rejected as too large: %s", names)
            notice = payload.get("content", "")
            payload = dict(payload)
            payload["content"] = (
                f"{notice}\n*(attachment too large for this server: "
                f"{names})*").strip()[:CONTENT_LIMIT]
            return await self._request("POST", url, json_body=payload)

    async def edit(self, webhook_url: str, message_id: int, content: str):
        """Mirror an edit onto an already-forwarded message."""
        url = f"{webhook_url}/messages/{message_id}"
        body = {"content": content[:CONTENT_LIMIT],
                "allowed_mentions": NO_MENTIONS}
        return await self._request("PATCH", url, json_body=body)

    async def delete(self, webhook_url: str, message_id: int):
        url = f"{webhook_url}/messages/{message_id}"
        return await self._request("DELETE", url)


class PayloadTooLarge(Exception):
    """Discord rejected the upload for size."""
