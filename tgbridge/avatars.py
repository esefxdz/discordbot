#telegram profile photos re-hosted as discord webhook avatars##
"""Profile photos for forwarded messages.

Telegram's file url embeds the bot token, so it can never go in a webhook
payload. The photo is uploaded once to a private discord channel and that link
is used as avatar_url — discord re-hosts it at post time, so the link only has
to be alive during the POST.

Best-effort: every failure returns None and the message keeps the default avatar.
"""
######################################################################
import asyncio
import io
import logging
import time

import discord

from . import store

log = logging.getLogger(__name__)


class AvatarCache:
    def __init__(self, discord_bot, channel_id: int | None):
        self.bot = discord_bot
        self.channel_id = channel_id
        self._tg_bot = None
        self._locks: dict[int, "object"] = {}
        self.hits = 0
        self.uploads = 0

    def bind_telegram(self, tg_bot):
        self._tg_bot = tg_bot

    @property
    def enabled(self) -> bool:
        return bool(self.channel_id and self._tg_bot)

    def _lock(self, key: int):
        # stop two messages from one user racing to upload the same photo
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    # ── telegram side ──────────────────────────────────────

    async def _current_photo(self, user_id: int):
        """-> (file_id, file_unique_id) for a user's current photo, or None."""
        photos = await self._tg_bot.get_user_profile_photos(user_id, limit=1)
        # empty is normal: no photo set, or privacy hides it
        if not photos or not photos.photos or not photos.photos[0]:
            return None
        sizes = photos.photos[0]
        pick = next((s for s in sizes if s.width >= 160), sizes[-1])
        return pick.file_id, pick.file_unique_id

    async def _chat_photo(self, chat):
        photo = getattr(chat, "photo", None)
        if not photo:
            return None
        return photo.small_file_id, photo.small_file_unique_id

    async def _download(self, file_id: str) -> bytes | None:
        try:
            tg_file = await self._tg_bot.get_file(file_id)
            buf = io.BytesIO()
            await tg_file.download_to_memory(buf)
            return buf.getvalue()
        except Exception as exc:
            log.debug("avatar download failed: %s", exc)
            return None

    # ── discord side ───────────────────────────────────────

    async def _upload(self, key: int, image: bytes) -> str | None:
        """Re-host the image in the cache channel and return its url."""
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except Exception as exc:
                log.warning("avatar cache channel %s unreachable: %s",
                            self.channel_id, exc)
                return None
        try:
            file = discord.File(io.BytesIO(image), filename=f"{key}.jpg")
            message = await channel.send(file=file)
            self.uploads += 1
            return message.attachments[0].url
        except Exception as exc:
            log.warning("avatar upload failed: %s", exc)
            return None

    # ── public ────────────────────────────────────────────

    async def url_for_user(self, user) -> str | None:
        if not self.enabled or user is None:
            return None
        return await self._resolve(user.id, lambda: self._current_photo(user.id))

    async def url_for_chat(self, chat) -> str | None:
        if not self.enabled or chat is None:
            return None
        return await self._resolve(chat.id, lambda: self._chat_photo(chat))

    async def _resolve(self, key: int, fetch_photo) -> str | None:
        try:
            async with self._lock(key):
                return await self._resolve_inner(key, fetch_photo)
        except Exception as exc:
            log.debug("avatar resolution failed for %s: %s", key, exc)
            return None

    async def _resolve_inner(self, key: int, fetch_photo) -> str | None:
        now = int(time.time())
        cached = await store.get_avatar(key)

        # fresh link, checked recently — nothing to do
        if cached and cached["cdn_url"] and \
                now - cached["uploaded_at"] < store.AVATAR_URL_TTL and \
                now - cached["checked_at"] < store.AVATAR_RECHECK:
            self.hits += 1
            return cached["cdn_url"]

        # known to have no photo, not time to re-check yet
        if cached and not cached["file_unique_id"] and \
                now - cached["checked_at"] < store.AVATAR_RECHECK:
            return None

        photo = None
        if not cached or now - cached["checked_at"] >= store.AVATAR_RECHECK:
            try:
                photo = await fetch_photo()
            except Exception as exc:
                log.debug("profile photo lookup failed for %s: %s", key, exc)
                photo = None

            if photo is None:
                await store.put_avatar(key, None, None, None, 0, now)
                return None

        # same photo: refresh the link from stored bytes, no re-download
        if cached and photo and photo[1] == cached["file_unique_id"] \
                and cached["image"]:
            if cached["cdn_url"] and now - cached["uploaded_at"] < store.AVATAR_URL_TTL:
                await store.touch_avatar(key, now)
                self.hits += 1
                return cached["cdn_url"]
            url = await self._upload(key, cached["image"])
            if url:
                await store.put_avatar(key, photo[1], cached["image"], url, now, now)
            return url

        # new or changed photo: download, host, remember
        if photo is None:
            return cached["cdn_url"] if cached else None
        image = await self._download(photo[0])
        if not image:
            return cached["cdn_url"] if cached else None
        url = await self._upload(key, image)
        if url:
            await store.put_avatar(key, photo[1], image, url, now, now)
        return url
