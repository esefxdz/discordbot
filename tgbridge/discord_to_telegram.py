#discord-to-telegram forwarding##
"""Discord -> Telegram forwarding.

Sends as HTML, not Markdown: telegram 400s on any unpaired * _ ` or [, which
silently dropped most real messages. Only html can escape arbitrary user text.
"""
######################################################################
import html
import io
import logging

import discord
from discord.ext import commands
from telegram import (Bot, InputMediaAudio, InputMediaDocument,
                      InputMediaPhoto, InputMediaVideo, ReplyParameters)
from telegram.error import TelegramError

from . import entities, store

log = logging.getLogger(__name__)

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
MEDIA_GROUP_MAX = 10


class DiscordToTelegramForwarder(commands.Cog):
    """Mirrors configured Discord channels into their paired Telegram chats."""

    def __init__(self, bot: commands.Bot, tg_token: str, routes=None):
        self.bot = bot
        self._tg_bot = Bot(token=tg_token)
        # discord_channel_id -> telegram_chat_id
        self.routes: dict[int, int] = {}
        for route in routes or []:
            if route.dc_to_tg and route.tg_chat_id:
                self.routes[route.dc_channel_id] = route.tg_chat_id
        self.forwarded = 0
        self.last_error: str | None = None

    def add_route(self, discord_channel_id: int, telegram_chat_id: int):
        self.routes[int(discord_channel_id)] = int(telegram_chat_id)

    async def cog_unload(self):
        try:
            await self._tg_bot.shutdown()
        except Exception:
            pass

    # ── helpers ─────────────────────────────────────────────────────────────

    def _header(self, message: discord.Message) -> str:
        return f"<b>{html.escape(message.author.display_name)}</b>"

    def _body(self, message: discord.Message) -> str:
        content = entities.resolve_discord_references(message, message.content or "")
        return entities.to_telegram_html(content)

    async def _reply_params(self, message: discord.Message):
        """Point the telegram message at whatever this replies to."""
        ref = getattr(message, "reference", None)
        if not ref or not ref.message_id:
            return None
        try:
            mapped = await store.tg_for_dc(message.channel.id, ref.message_id)
            if not mapped:
                return None
            return ReplyParameters(message_id=mapped[1],
                                   allow_sending_without_reply=True)
        except Exception as exc:
            log.debug("reply lookup failed: %s", exc)
            return None

    @staticmethod
    def _split(text: str, limit: int) -> list[str]:
        if not text:
            return []
        if len(text) <= limit:
            return [text]
        chunks, rest = [], text
        while len(rest) > limit:
            window = rest[:limit]
            cut = window.rfind("\n")
            if cut < limit // 2:
                cut = window.rfind(" ")
            if cut < limit // 2:
                cut = limit
            chunks.append(rest[:cut])
            rest = rest[cut:].lstrip()
        if rest:
            chunks.append(rest)
        return chunks

    async def _send_text(self, chat_id: int, text: str, reply=None):
        sent = []
        for chunk in self._split(text, TEXT_LIMIT):
            try:
                msg = await self._tg_bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode="HTML",
                    reply_parameters=reply)
            except TelegramError as exc:
                # last resort: drop formatting rather than lose the message
                log.warning("HTML send failed (%s) — retrying as plain text", exc)
                self.last_error = str(exc)
                try:
                    msg = await self._tg_bot.send_message(
                        chat_id=chat_id, text=_strip_tags(chunk),
                        reply_parameters=reply)
                except TelegramError as exc2:
                    log.error("plain-text send also failed: %s", exc2)
                    self.last_error = str(exc2)
                    continue
            sent.append(msg)
            reply = None
        return sent

    # ── attachments ─────────────────────────────────────────────────────────

    @staticmethod
    def _kind(attachment: discord.Attachment) -> str:
        mime = (attachment.content_type or "").lower()
        name = (attachment.filename or "").lower()
        if mime.startswith("image/gif") or name.endswith(".gif"):
            return "animation"
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        return "document"

    async def _buffer(self, attachment: discord.Attachment):
        buf = io.BytesIO(await attachment.read())
        buf.name = attachment.filename
        return buf

    async def _send_attachments(self, chat_id, attachments, caption, reply):
        """Send attachments, batching into media groups where allowed."""
        # photos and videos can share a group; documents and audio cannot
        visual, documents, audio, animations = [], [], [], []
        for attachment in attachments:
            kind = self._kind(attachment)
            (visual if kind in ("photo", "video")
             else audio if kind == "audio"
             else animations if kind == "animation"
             else documents).append((kind, attachment))

        sent = []
        caption_left = caption

        async def flush_group(items, factory):
            nonlocal caption_left, reply
            for batch in _chunks(items, MEDIA_GROUP_MAX):
                # read each attachment once and reuse the buffer
                prepared = []
                for index, (kind, attachment) in enumerate(batch):
                    buf = await self._buffer(attachment)
                    caption = None
                    if index == 0 and caption_left:
                        caption = caption_left[:CAPTION_LIMIT]
                        caption_left = None
                    prepared.append((kind, buf, caption))
                try:
                    if len(prepared) == 1:
                        kind, buf, caption = prepared[0]
                        sent.extend(await self._send_single(
                            chat_id, kind, buf, caption, reply))
                    else:
                        media = []
                        for kind, buf, caption in prepared:
                            kwargs = {"media": buf}
                            if caption:
                                kwargs["caption"] = caption
                                kwargs["parse_mode"] = "HTML"
                            media.append(factory(kind, kwargs))
                        sent.extend(await self._tg_bot.send_media_group(
                            chat_id=chat_id, media=media, reply_parameters=reply))
                    reply = None
                except TelegramError as exc:
                    log.error("attachment send failed: %s", exc)
                    self.last_error = str(exc)

        await flush_group(visual, lambda kind, kw:
                          InputMediaPhoto(**kw) if kind == "photo"
                          else InputMediaVideo(**kw))
        await flush_group(animations, lambda kind, kw: InputMediaDocument(**kw))
        await flush_group(documents, lambda kind, kw: InputMediaDocument(**kw))
        await flush_group(audio, lambda kind, kw: InputMediaAudio(**kw))

        if caption_left:
            sent.extend(await self._send_text(chat_id, caption_left, reply))
        return sent

    async def _send_single(self, chat_id, kind, buf, caption, reply):
        """One attachment on its own — use the richest endpoint for its type."""
        common = {"chat_id": chat_id, "caption": caption,
                  "parse_mode": "HTML" if caption else None,
                  "reply_parameters": reply}
        if kind == "photo":
            return [await self._tg_bot.send_photo(photo=buf, **common)]
        if kind == "video":
            return [await self._tg_bot.send_video(video=buf, **common)]
        if kind == "audio":
            return [await self._tg_bot.send_audio(audio=buf, **common)]
        if kind == "animation":
            return [await self._tg_bot.send_animation(animation=buf, **common)]
        return [await self._tg_bot.send_document(document=buf, **common)]

    async def _send_stickers(self, chat_id, stickers, reply):
        """Discord stickers have no telegram equivalent, send what we can."""
        sent = []
        for sticker in stickers:
            try:
                fmt = getattr(sticker, "format", None)
                name = getattr(fmt, "name", "") or ""
                if name.lower() == "lottie":
                    sent.extend(await self._send_text(
                        chat_id, f"<i>(sticker: {html.escape(sticker.name)})</i>",
                        reply))
                else:
                    data = await sticker.read()
                    buf = io.BytesIO(data)
                    buf.name = f"{sticker.name}.{'gif' if name.lower() == 'gif' else 'png'}"
                    sent.append(await self._tg_bot.send_document(
                        chat_id=chat_id, document=buf, reply_parameters=reply))
                reply = None
            except Exception as exc:
                log.warning("sticker forward failed: %s", exc)
        return sent

    # ── events ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignoring bots and webhooks is what breaks the echo loop
        if message.author.bot or message.webhook_id:
            return
        chat_id = self.routes.get(message.channel.id)
        if chat_id is None:
            return

        try:
            reply = await self._reply_params(message)
            header = self._header(message)
            body = self._body(message)
            caption = f"{header}\n{body}".strip() if body else header

            sent = []
            if message.attachments:
                sent += await self._send_attachments(
                    chat_id, message.attachments, caption, reply)
            elif getattr(message, "stickers", None):
                sent += await self._send_text(chat_id, caption, reply)
                sent += await self._send_stickers(chat_id, message.stickers, None)
            else:
                sent += await self._send_text(chat_id, caption, reply)

            if sent:
                await store.link(chat_id, [m.message_id for m in sent],
                                 message.channel.id, message.id, "dc")
            self.forwarded += 1
            log.info("[D->TG] %s (channel %s) -> chat %s",
                     message.author, message.channel.id, chat_id)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("[D->TG] forward failed")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        # raw, not on_message_edit: the cached one never fires for messages
        # sent before the last restart
        chat_id = self.routes.get(payload.channel_id)
        if chat_id is None:
            return
        data = payload.data or {}
        if data.get("author", {}).get("bot") or data.get("webhook_id"):
            return
        content = data.get("content")
        if content is None:
            return                      # embed-only update, not an edit

        try:
            mapped = await store.tg_for_dc(payload.channel_id, payload.message_id)
            if not mapped:
                return
            _, tg_msg_id = mapped

            message = payload.cached_message
            author = (message.author.display_name if message
                      else data.get("author", {}).get("global_name")
                      or data.get("author", {}).get("username") or "unknown")
            resolved = entities.resolve_discord_references(message, content) \
                if message else content
            text = (f"<b>{html.escape(str(author))}</b>\n"
                    f"{entities.to_telegram_html(resolved)}\n"
                    f"<i>(edited)</i>")[:TEXT_LIMIT]
            try:
                await self._tg_bot.edit_message_text(
                    chat_id=chat_id, message_id=tg_msg_id, text=text,
                    parse_mode="HTML")
            except TelegramError:
                # the original was media, so the text is in the caption
                await self._tg_bot.edit_message_caption(
                    chat_id=chat_id, message_id=tg_msg_id,
                    caption=text[:CAPTION_LIMIT], parse_mode="HTML")
        except TelegramError as exc:
            log.debug("[D->TG] edit sync failed: %s", exc)
        except Exception:
            log.exception("[D->TG] edit sync error")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        await self._delete(payload.channel_id, [payload.message_id])

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
            self, payload: discord.RawBulkMessageDeleteEvent):
        await self._delete(payload.channel_id, list(payload.message_ids))

    async def _delete(self, channel_id: int, message_ids: list[int]):
        chat_id = self.routes.get(channel_id)
        if chat_id is None:
            return
        for message_id in message_ids:
            try:
                mapped = await store.tg_for_dc(channel_id, message_id)
                if not mapped:
                    continue
                await self._tg_bot.delete_message(chat_id=chat_id,
                                                  message_id=mapped[1])
            except TelegramError as exc:
                # telegram refuses deletes older than 48h, which is normal
                log.debug("[D->TG] delete sync skipped: %s", exc)
            except Exception:
                log.exception("[D->TG] delete sync error")


def _chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _strip_tags(text: str) -> str:
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", text))
