# this is where telegram-to-discord forwarding lives
import logging
import io
import json
import aiohttp
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logger = logging.getLogger('telegram.forwarder')


class TelegramForwarder:
    """Watches a Telegram group/channel and forwards messages to a Discord webhook."""

    def __init__(self, token: str, group_id: int, webhook_url: str):
        self.token = token
        self.group_id = group_id
        self.webhook_url = webhook_url
        self._app: Application | None = None

    async def start(self):
        """Build the telegram application and start polling."""
        self._app = (
            Application.builder()
            .token(self.token)
            .build()
        )
        self._app.add_handler(MessageHandler(filters.ALL, self._on_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info('✅ Telegram forwarder started — watching group %s', self.group_id)

    async def stop(self):
        """Graceful shutdown."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info('Telegram forwarder stopped')

    # ── internal helpers ─────────────────────────────────────────────────────

    def _sender_name(self, update: Update) -> str:
        """Extract a display name from the message sender."""
        user = update.effective_user
        if user:
            name = user.first_name or ''
            if user.last_name:
                name += f' {user.last_name}'
            return name.strip() or user.username or 'Unknown'
        # channel posts have no user — use chat title
        chat = update.effective_chat
        if chat and chat.title:
            return chat.title
        return 'Unknown'

    def _forward_prefix(self, message) -> str:
        """Build a prefix for forwarded messages."""
        if message.forward_origin:
            origin = message.forward_origin
            # user forward
            if hasattr(origin, 'sender_user') and origin.sender_user:
                u = origin.sender_user
                name = u.first_name or ''
                if u.last_name:
                    name += f' {u.last_name}'
                return f'↩️ Forwarded from **{name.strip() or u.username or "Unknown"}**:\n'
            # channel forward
            if hasattr(origin, 'chat') and origin.chat:
                return f'↩️ Forwarded from **{origin.chat.title or "Unknown channel"}**:\n'
            # hidden user
            if hasattr(origin, 'sender_user_name') and origin.sender_user_name:
                return f'↩️ Forwarded from **{origin.sender_user_name}**:\n'
            return '↩️ Forwarded:\n'
        return ''

    async def _download_file(self, file_id: str) -> tuple[bytes, str]:
        """Download a file from Telegram, return (bytes, filename)."""
        tg_file = await self._app.bot.get_file(file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        filename = tg_file.file_path.split('/')[-1] if tg_file.file_path else 'file'
        return buf.read(), filename

    async def _send_to_webhook(self, username: str, content: str = '',
                                file_data: bytes | None = None,
                                filename: str | None = None):
        """POST a message (and optional file) to the Discord webhook."""
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            payload = {'username': username}
            if content:
                payload['content'] = content[:2000]  # discord limit

            if file_data and filename:
                data.add_field('payload_json', json.dumps(payload),
                               content_type='application/json')
                data.add_field('file', file_data, filename=filename)
            else:
                # simple json post
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        logger.error('Webhook POST failed: %s %s', resp.status, await resp.text())
                    return

            async with session.post(self.webhook_url, data=data) as resp:
                if resp.status not in (200, 204):
                    logger.error('Webhook POST failed: %s %s', resp.status, await resp.text())

    # ── main message handler ─────────────────────────────────────────────────

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle every incoming message from the watched group."""
        message = update.effective_message
        if not message:
            return

        # only process messages from the configured group
        if update.effective_chat and update.effective_chat.id != self.group_id:
            return

        sender = self._sender_name(update)
        prefix = self._forward_prefix(message)
        caption = message.caption or ''

        try:
            # ── photo ────────────────────────────────────────────────────
            if message.photo:
                # largest resolution is last in the array
                photo = message.photo[-1]
                file_data, filename = await self._download_file(photo.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            # ── video ────────────────────────────────────────────────────
            elif message.video:
                file_data, filename = await self._download_file(message.video.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            # ── document (files, gifs sent as docs, etc) ─────────────────
            elif message.document:
                file_data, filename = await self._download_file(message.document.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            # ── animation (GIF) ──────────────────────────────────────────
            elif message.animation:
                file_data, filename = await self._download_file(message.animation.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            # ── sticker ──────────────────────────────────────────────────
            elif message.sticker:
                # stickers have no caption — send emoji or name
                sticker_text = message.sticker.emoji or '(sticker)'
                text = f'{prefix}{sticker_text}'
                # try to download the sticker image
                try:
                    file_data, filename = await self._download_file(message.sticker.file_id)
                    await self._send_to_webhook(sender, text, file_data, filename)
                except Exception:
                    await self._send_to_webhook(sender, text)

            # ── text ─────────────────────────────────────────────────────
            elif message.text:
                text = f'{prefix}{message.text}'
                await self._send_to_webhook(sender, text)

            else:
                # voice, audio, contact, location, etc — just note it
                text = f'{prefix}*(unsupported message type)*'
                await self._send_to_webhook(sender, text)

        except Exception:
            logger.exception('Failed to forward message from %s', sender)
