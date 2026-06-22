# this is where telegram-to-discord forwarding lives
import io
import json
import aiohttp
from telegram.ext import Application, MessageHandler, filters

class TelegramForwarder:
    def __init__(self, token, group_id, webhook_url):
        self.token = token
        self.group_id = group_id
        self.webhook_url = webhook_url
        self._app = None

    async def start(self):
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(MessageHandler(filters.ALL, self._on_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        print(f'✅ telegram forwarder started — watching group {self.group_id}')

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            print('telegram forwarder stopped')

    # internal helpers
    def _sender_name(self, update):
        user = update.effective_user
        if user:
            name = user.first_name or ''
            if user.last_name:
                name += f' {user.last_name}'
            return name.strip() or user.username or 'unknown'
        
        # channel posts have no user — use chat title
        chat = update.effective_chat
        if chat and chat.title:
            return chat.title
        return 'unknown'

    def _forward_prefix(self, message):
        if message.forward_origin:
            origin = message.forward_origin
            
            # user forward
            if hasattr(origin, 'sender_user') and origin.sender_user:
                u = origin.sender_user
                name = u.first_name or ''
                if u.last_name:
                    name += f' {u.last_name}'
                return f'↩️ Forwarded from **{name.strip() or u.username or "unknown"}**:\n'
            
            # channel forward
            if hasattr(origin, 'chat') and origin.chat:
                return f'↩️ Forwarded from **{origin.chat.title or "unknown channel"}**:\n'
            
            # hidden user
            if hasattr(origin, 'sender_user_name') and origin.sender_user_name:
                return f'↩️ Forwarded from **{origin.sender_user_name}**:\n'
            
            return '↩️ Forwarded:\n'
        return ''

    async def _download_file(self, file_id):
        tg_file = await self._app.bot.get_file(file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        filename = tg_file.file_path.split('/')[-1] if tg_file.file_path else 'file'
        return buf.read(), filename

    async def _send_to_webhook(self, username, content='', file_data=None, filename=None):
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            payload = {'username': username}
            if content:
                payload['content'] = content[:2000]

            if file_data and filename:
                data.add_field('payload_json', json.dumps(payload), content_type='application/json')
                data.add_field('file', file_data, filename=filename)
            else:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        print(f'webhook post failed: {resp.status} {await resp.text()}')
                    return

            async with session.post(self.webhook_url, data=data) as resp:
                if resp.status not in (200, 204):
                    print(f'webhook post failed: {resp.status} {await resp.text()}')

    # main message handler
    async def _on_message(self, update, context):
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
            if message.photo:
                photo = message.photo[-1]
                file_data, filename = await self._download_file(photo.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            elif message.video:
                file_data, filename = await self._download_file(message.video.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            elif message.document:
                file_data, filename = await self._download_file(message.document.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            elif message.animation:
                file_data, filename = await self._download_file(message.animation.file_id)
                text = f'{prefix}{caption}'.strip()
                await self._send_to_webhook(sender, text, file_data, filename)

            elif message.sticker:
                sticker_text = message.sticker.emoji or '(sticker)'
                text = f'{prefix}{sticker_text}'
                try:
                    file_data, filename = await self._download_file(message.sticker.file_id)
                    await self._send_to_webhook(sender, text, file_data, filename)
                except Exception:
                    await self._send_to_webhook(sender, text)

            elif message.text:
                text = f'{prefix}{message.text}'
                await self._send_to_webhook(sender, text)

            else:
                text = f'{prefix}*(unsupported message type)*'
                await self._send_to_webhook(sender, text)

        except Exception as e:
            print(f'failed to forward message from {sender}: {e}')
