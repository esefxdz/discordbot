#this is where telegram-to-discord forwarding lives##
"""Telegram -> Discord forwarding."""
######################################################################
import asyncio
import io
import logging
import time

from telegram.ext import Application, MessageHandler, filters

from . import config, entities, media, store
from .avatars import AvatarCache
from .webhook import WebhookClient

log = logging.getLogger(__name__)

# an album arrives as several updates; wait this long after the last one
ALBUM_DEBOUNCE = 1.5
ALBUM_MAX_FILES = 10
DOWNLOAD_TIMEOUT = 90.0
REPLY_SNIPPET = 120


class TelegramForwarder:
    def __init__(self, token, discord_bot=None, avatar_channel_id=None,
                 routes=None, drop_pending=True):
        self.token = token
        self.bot = discord_bot
        self.drop_pending = drop_pending
        # chat_id -> Route
        self.routes: dict[int, config.Route] = {}
        for route in routes or []:
            if route.tg_to_dc:
                self.routes[route.tg_chat_id] = route

        self.webhook = WebhookClient()
        self.avatars = AvatarCache(discord_bot, avatar_channel_id)
        self._app = None
        self._running = False
        self._albums: dict[str, dict] = {}
        self.started_at = None
        self.forwarded = 0
        self.last_error = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def add_route(self, group_id, webhook_url):
        """Back-compatible single-route registration."""
        existing = self.routes.get(int(group_id))
        if existing:
            existing.webhook_url = webhook_url
        else:
            self.routes[int(group_id)] = config.Route(
                name=str(group_id), tg_chat_id=int(group_id),
                webhook_url=webhook_url)

    async def start(self):
        # on_ready fires again on every reconnect; a second poller on the same
        # token means telegram 409s and duplicated posts
        if self._running:
            log.debug("telegram forwarder already running — start() ignored")
            return
        self._running = True
        try:
            await store.init_db()
            removed = await store.prune()
            if removed:
                log.info("pruned %d expired message mappings", removed)

            self._app = Application.builder().token(self.token).build()
            self._app.add_handler(MessageHandler(filters.ALL, self._on_update))
            self._app.add_error_handler(self._on_error)

            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=self.drop_pending)

            self.avatars.bind_telegram(self._app.bot)
            self.started_at = time.time()
            log.info("telegram forwarder started — watching %d chat(s), "
                     "avatars %s", len(self.routes),
                     "on" if self.avatars.enabled else "off")
        except Exception:
            self._running = False
            raise

    async def stop(self):
        self._running = False
        for pending in list(self._albums.values()):
            task = pending.get("task")
            if task:
                task.cancel()
        self._albums.clear()
        if self._app:
            try:
                if self._app.updater.running:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as exc:
                log.warning("error during telegram shutdown: %s", exc)
            self._app = None
        await self.webhook.close()
        log.info("telegram forwarder stopped")

    async def _on_error(self, update, context):
        self.last_error = str(context.error)
        log.error("telegram update failed: %s", context.error)

    # ── naming ──────────────────────────────────────────────────────────────

    def _sender_name(self, message) -> str:
        user = getattr(message, "from_user", None)
        if user:
            name = (user.first_name or "").strip()
            if user.last_name:
                name = f"{name} {user.last_name}".strip()
            return name or user.username or "unknown"
        chat = getattr(message, "sender_chat", None) or getattr(message, "chat", None)
        if chat and getattr(chat, "title", None):
            return chat.title
        return "unknown"

    # ── downloads ───────────────────────────────────────────────────────────

    async def _download(self, file_id: str):
        """-> (bytes, filename). Raises on failure."""
        tg_file = await asyncio.wait_for(
            self._app.bot.get_file(file_id), timeout=30.0)
        buf = io.BytesIO()
        await asyncio.wait_for(
            tg_file.download_to_memory(buf), timeout=DOWNLOAD_TIMEOUT)
        path = tg_file.file_path or ""
        return buf.getvalue(), (path.split("/")[-1] or "file")

    # ── context lines ───────────────────────────────────────────────────────

    async def _reply_line(self, message, route) -> str:
        """Render a quote of whatever this message is replying to."""
        replied = getattr(message, "reply_to_message", None)
        if not replied:
            return ""
        try:
            who = self._sender_name(replied)
            snippet = (replied.text or replied.caption or "").strip()
            if not snippet:
                snippet = _describe_media(replied)
            snippet = " ".join(snippet.split())
            if len(snippet) > REPLY_SNIPPET:
                snippet = snippet[:REPLY_SNIPPET].rstrip() + "…"
            snippet = entities.escape_discord(snippet)
            who = entities.escape_discord(who)

            link = await self._jump_link(route, replied.message_id)
            arrow = f"[↩ reply to **{who}**]({link})" if link else f"↩ reply to **{who}**"
            return f"> {arrow}: {snippet}\n" if snippet else f"> {arrow}\n"
        except Exception as exc:
            log.debug("could not render reply context: %s", exc)
            return ""

    async def _jump_link(self, route, tg_msg_id: int):
        """Link to the Discord copy of a Telegram message, if we forwarded it."""
        if not self.bot:
            return None
        try:
            mapped = await store.dc_for_tg(route.tg_chat_id, tg_msg_id)
            if not mapped:
                return None
            channel_id, message_id = mapped
            channel = self.bot.get_channel(channel_id)
            guild_id = channel.guild.id if channel and channel.guild else None
            if not guild_id:
                return None
            return (f"https://discord.com/channels/"
                    f"{guild_id}/{channel_id}/{message_id}")
        except Exception:
            return None

    def _forward_line(self, message) -> str:
        """Note where a forwarded message originally came from."""
        try:
            origin = getattr(message, "forward_origin", None)
            source = None
            if origin is not None:
                otype = getattr(origin, "type", "")
                if otype == "user" and getattr(origin, "sender_user", None):
                    user = origin.sender_user
                    source = (user.first_name or "") + (
                        f" {user.last_name}" if user.last_name else "")
                elif otype == "hidden_user":
                    source = getattr(origin, "sender_user_name", None)
                elif otype == "chat" and getattr(origin, "sender_chat", None):
                    source = origin.sender_chat.title
                elif otype == "channel" and getattr(origin, "chat", None):
                    source = origin.chat.title
            if not source:
                return ""
            return f"-# ↪ forwarded from {entities.escape_discord(source.strip())}\n"
        except Exception:
            return ""

    # ── rendering ───────────────────────────────────────────────────────────

    async def _render_text(self, message) -> str:
        body = message.text or message.caption or ""
        ents = message.entities if message.text else message.caption_entities
        return entities.to_discord_markdown(body, ents)

    async def _render_sticker(self, message):
        """-> (files, extra_text), per sticker format."""
        sticker = message.sticker
        emoji = sticker.emoji or ""
        cached = await store.get_media(sticker.file_unique_id)
        if cached:
            return [cached], emoji

        data, filename = await self._download(sticker.file_id)
        stem = filename.rsplit(".", 1)[0] or "sticker"

        if sticker.is_video:
            # .webm vp9 with alpha -> animated webp keeps the transparency
            converted = await media.to_animated_webp(data, ".webm")
            if converted:
                out = (f"{stem}.webp", converted)
                await store.put_media(sticker.file_unique_id, *out)
                return [out], emoji
            return [(filename, data)], emoji

        if sticker.is_animated:
            # .tgs is gzipped lottie, ffmpeg cannot read it
            rendered = await media.render_tgs(data)
            if rendered:
                out = (f"{stem}.gif", rendered)
                await store.put_media(sticker.file_unique_id, *out)
                return [out], emoji
            thumb = getattr(sticker, "thumbnail", None)
            if thumb:
                try:
                    tdata, tname = await self._download(thumb.file_id)
                    return [(tname, tdata)], emoji
                except Exception as exc:
                    log.debug("sticker thumbnail failed: %s", exc)
            return [], f"{emoji} *(animated sticker)*".strip()

        # static .webp renders natively with full alpha; converting to gif
        # only destroyed the antialiased edges
        return [(filename, data)], emoji

    async def _render_media(self, message):
        """-> (files, extra_text) for whatever media this message carries."""
        if message.sticker:
            return await self._render_sticker(message)

        if message.photo:
            data, filename = await self._download(message.photo[-1].file_id)
            return [(filename, data)], ""

        animation = getattr(message, "animation", None)
        document = getattr(message, "document", None)
        # mime_type is nullable
        doc_mime = (getattr(document, "mime_type", None) or "") if document else ""

        if animation or message.video or doc_mime.startswith("video"):
            if animation:
                file_id, is_animation = animation.file_id, True
            elif message.video:
                file_id, is_animation = message.video.file_id, False
            else:
                file_id, is_animation = document.file_id, False

            data, filename = await self._download(file_id)
            suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".mp4"

            # animations never have audio; elsewhere a silent clip is a gif in
            # spirit and should loop, one with sound is a real video
            convert = is_animation or not await media.has_audio_track(data, suffix)
            if convert:
                gif = await media.to_gif(data, suffix)
                if gif:
                    return [(filename.rsplit(".", 1)[0] + ".gif", gif)], ""
            return [(filename, data)], ""

        if document:
            data, filename = await self._download(document.file_id)
            return [(filename, data)], ""

        if message.voice:
            data, filename = await self._download(message.voice.file_id)
            return [(filename, data)], f"-# 🎤 voice message ({_duration(message.voice)})"

        if message.video_note:
            data, filename = await self._download(message.video_note.file_id)
            return [(filename, data)], f"-# 📹 video note ({_duration(message.video_note)})"

        if message.audio:
            audio = message.audio
            data, filename = await self._download(audio.file_id)
            label = " — ".join(x for x in (audio.performer, audio.title) if x)
            return [(filename, data)], f"-# 🎵 {label}" if label else ""

        return [], _describe_nonmedia(message)

    # ── dispatch ────────────────────────────────────────────────────────────

    async def _on_update(self, update, context):
        message = update.effective_message
        if not message:
            return
        chat = update.effective_chat
        route = self.routes.get(chat.id) if chat else None
        if route is None:
            return

        # echo guard: never re-forward what the bridge itself posted
        me = getattr(self._app.bot, "id", None)
        sender = getattr(message, "from_user", None)
        if me and sender and sender.id == me:
            return

        is_edit = bool(update.edited_message or update.edited_channel_post)
        try:
            if is_edit:
                await self._handle_edit(route, message)
            elif message.media_group_id:
                self._buffer_album(route, message)
            else:
                await self._forward(route, [message])
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("failed to forward message from %s",
                          self._sender_name(message))

    def _buffer_album(self, route, message):
        key = f"{route.tg_chat_id}:{message.media_group_id}"
        pending = self._albums.get(key)
        if pending is None:
            pending = self._albums[key] = {"messages": [], "task": None,
                                           "route": route}
        pending["messages"].append(message)
        if pending["task"]:
            pending["task"].cancel()
        pending["task"] = asyncio.create_task(self._flush_album(key))

    async def _flush_album(self, key):
        try:
            await asyncio.sleep(ALBUM_DEBOUNCE)
        except asyncio.CancelledError:
            return
        pending = self._albums.pop(key, None)
        if not pending or not pending["messages"]:
            return
        messages = sorted(pending["messages"], key=lambda m: m.message_id)
        try:
            await self._forward(pending["route"], messages)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("failed to forward album")

    async def _forward(self, route, messages):
        """Post one or more Telegram messages as a single Discord message."""
        lead = messages[0]
        sender = self._sender_name(lead)

        files, notes = [], []
        for message in messages:
            try:
                got, note = await self._render_media(message)
            except Exception as exc:
                log.warning("media render failed: %s", exc)
                got, note = [], "*(attachment could not be fetched)*"
            files.extend(got)
            if note:
                notes.append(note)

        body_parts = []
        for message in messages:
            rendered = await self._render_text(message)
            if rendered:
                body_parts.append(rendered)

        prefix = self._forward_line(lead) + await self._reply_line(lead, route)
        content = prefix + "\n".join(body_parts + notes).strip()

        if not content.strip() and not files:
            return

        avatar_url = None
        try:
            if getattr(lead, "from_user", None):
                avatar_url = await self.avatars.url_for_user(lead.from_user)
            else:
                avatar_url = await self.avatars.url_for_chat(
                    getattr(lead, "sender_chat", None) or lead.chat)
        except Exception as exc:
            log.debug("avatar lookup failed: %s", exc)

        result = await self.webhook.send(
            route.webhook_url, sender, content,
            files=files[:ALBUM_MAX_FILES] or None, avatar_url=avatar_url)

        self.forwarded += 1
        if result and result.get("id"):
            # every telegram id in an album maps to the one discord post, so
            # a reply to any photo in it resolves
            await store.link(route.tg_chat_id,
                             [m.message_id for m in messages],
                             int(result["channel_id"]), int(result["id"]), "tg")

    async def _handle_edit(self, route, message):
        mapped = await store.dc_for_tg(route.tg_chat_id, message.message_id)
        if not mapped:
            log.debug("edit for unmapped message %s — ignoring", message.message_id)
            return
        _, dc_msg_id = mapped
        content = await self._render_text(message)
        if not content:
            return
        await self.webhook.edit(route.webhook_url, dc_msg_id,
                                content + "\n-# *(edited)*")


# ── small helpers ───────────────────────────────────────────────────────────

def _duration(obj) -> str:
    seconds = getattr(obj, "duration", 0) or 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def _describe_media(message) -> str:
    """Short label for a message quoted in a reply."""
    for attr, label in (("photo", "photo"), ("sticker", "sticker"),
                        ("animation", "GIF"), ("video", "video"),
                        ("voice", "voice message"), ("video_note", "video note"),
                        ("audio", "audio"), ("document", "file")):
        if getattr(message, attr, None):
            return f"({label})"
    return ""


def _describe_nonmedia(message) -> str:
    """The message types that carry no file at all."""
    poll = getattr(message, "poll", None)
    if poll:
        options = "\n".join(f"• {entities.escape_discord(o.text)}"
                            for o in poll.options)
        return f"📊 **{entities.escape_discord(poll.question)}**\n{options}"

    location = getattr(message, "location", None)
    venue = getattr(message, "venue", None)
    if venue:
        return (f"📍 **{entities.escape_discord(venue.title)}**\n"
                f"{entities.escape_discord(venue.address)}\n"
                f"<https://www.google.com/maps?q="
                f"{venue.location.latitude},{venue.location.longitude}>")
    if location:
        return (f"📍 location: <https://www.google.com/maps?q="
                f"{location.latitude},{location.longitude}>")

    contact = getattr(message, "contact", None)
    if contact:
        name = " ".join(x for x in (contact.first_name, contact.last_name) if x)
        return (f"👤 **{entities.escape_discord(name)}**\n"
                f"{entities.escape_discord(contact.phone_number or '')}")

    dice = getattr(message, "dice", None)
    if dice:
        return f"{dice.emoji} rolled **{dice.value}**"

    if getattr(message, "new_chat_members", None):
        names = ", ".join(entities.escape_discord(
            u.first_name or u.username or "someone")
            for u in message.new_chat_members)
        return f"-# → {names} joined the group"

    left = getattr(message, "left_chat_member", None)
    if left:
        name = entities.escape_discord(left.first_name or left.username or "someone")
        return f"-# ← {name} left the group"

    if getattr(message, "pinned_message", None):
        return "-# 📌 pinned a message"

    return ""
