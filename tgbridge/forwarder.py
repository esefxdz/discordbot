# this is where telegram-to-discord forwarding lives
import io
import json
import aiohttp
from telegram.ext import Application, MessageHandler, filters
import asyncio
import tempfile
import os
import shutil

class TelegramForwarder:
    def __init__(self, token):
        self.token = token
        self.routes = {}
        self._app = None

    def add_route(self, group_id, webhook_url):
        self.routes[group_id] = webhook_url

    async def start(self):
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(MessageHandler(filters.ALL, self._on_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        print(f'✅ telegram forwarder started — watching {len(self.routes)} groups')

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



    async def _download_file(self, file_id):
        tg_file = await self._app.bot.get_file(file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        filename = tg_file.file_path.split('/')[-1] if tg_file.file_path else 'file'
        return buf.read(), filename

    async def _send_to_webhook(self, webhook_url, username, content='', file_data=None, filename=None):
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            payload = {'username': username}
            if content:
                payload['content'] = content[:2000]

            if file_data and filename:
                data.add_field('payload_json', json.dumps(payload), content_type='application/json')
                data.add_field('file', file_data, filename=filename)
            else:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        print(f'webhook post failed: {resp.status} {await resp.text()}')
                    return

            async with session.post(webhook_url, data=data) as resp:
                if resp.status not in (200, 204):
                    print(f'webhook post failed: {resp.status} {await resp.text()}')

    # main message handler
    async def _on_message(self, update, context):
        message = update.effective_message
        if not message:
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id not in self.routes:
            return

        webhook_url = self.routes[chat_id]
        sender = self._sender_name(update)
        caption = message.caption or ''

        # Log incoming message media types for debugging
        print(f"[HANDLER] Types - photo:{bool(message.photo)} video:{bool(message.video)} animation:{bool(message.animation)} document:{bool(message.document)}", flush=True)
        try:
            if message.photo:
                photo = message.photo[-1]
                file_data, filename = await self._download_file(photo.file_id)
                text = caption.strip()
                await self._send_to_webhook(webhook_url, sender, text, file_data, filename)

            # video and document handling are now managed by the enhanced GIF block below

            # --- START OF ENHANCED GIF HANDLING BLOCK ---
            # Telegram may send a GIF as an `animation` (the normal case) *or*
            # as a generic `document`/`video` with MIME type `video/mp4`.  We want to
            # treat any of those as a potential GIF and try to convert it to a true
            # GIF for Discord auto‑looping.
            elif getattr(message, "animation", None) or (
                getattr(message, "document", None) and message.document.mime_type.startswith("video")
            ) or message.video:
                # Identify the file source
                if message.animation:
                    file_id = message.animation.file_id
                elif message.video:
                    file_id = message.video.file_id
                else:
                    file_id = message.document.file_id
                
                print(f"[GIF-HANDLER] Detected potential media (file_id={file_id})", flush=True)
                file_data, filename = await self._download_file(file_id)

                # Attempt conversion if it's a video/animation
                # >>> FFMPEG GIF CONVERSION START >>>
                try:

                    if not shutil.which('ffmpeg'):
                        raise RuntimeError("ffmpeg binary not found in PATH")

                    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_mp4:
                        temp_mp4.write(file_data)
                        temp_mp4_path = temp_mp4.name

                    temp_gif_path = temp_mp4_path + '.gif'

                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-y', '-i', temp_mp4_path,
                        '-filter_complex', 'fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
                        '-loop', '0', temp_gif_path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)

                    if process.returncode == 0 and os.path.exists(temp_gif_path):
                        gif_size = os.path.getsize(temp_gif_path)
                        if gif_size < 10 * 1024 * 1024:
                            with open(temp_gif_path, 'rb') as f:
                                file_data = f.read()
                            filename = filename.rsplit('.', 1)[0] + '.gif'
                    else:
                        print(f"[GIF-HANDLER] ffmpeg error: {stderr.decode()}", flush=True)
                    
                    if os.path.exists(temp_mp4_path): os.remove(temp_mp4_path)
                    if os.path.exists(temp_gif_path): os.remove(temp_gif_path)
                except Exception as e:
                    print(f'ffmpeg conversion skipped/failed: {e}', flush=True)
                # <<< FFMPEG GIF CONVERSION END <<<

                text = caption.strip()
                await self._send_to_webhook(webhook_url, sender, text, file_data, filename)

            elif message.document:
                file_data, filename = await self._download_file(message.document.file_id)
                text = caption.strip()
                await self._send_to_webhook(webhook_url, sender, text, file_data, filename)

            elif message.sticker:
                sticker_text = message.sticker.emoji or '(sticker)'
                text = sticker_text
                # >>> STICKER TO GIF CONVERSION START >>>
                file_data, filename = await self._download_file(message.sticker.file_id)
                try:
                    # Determine original file suffix for temporary file
                    suffix = os.path.splitext(filename)[1] or '.dat'
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_in:
                        temp_in.write(file_data)
                        temp_in_path = temp_in.name

                    temp_gif_path = temp_in_path + '.gif'

                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-y', '-i', temp_in_path,
                        '-filter_complex', 'fps=15,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
                        '-loop', '0', temp_gif_path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE
                    )

                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)

                    if process.returncode == 0 and os.path.exists(temp_gif_path):
                        gif_size = os.path.getsize(temp_gif_path)
                        if gif_size < 10 * 1024 * 1024:
                            with open(temp_gif_path, 'rb') as f:
                                file_data = f.read()
                            filename = filename.rsplit('.', 1)[0] + '.gif'
                        else:
                            print('[STICKER-HANDLER] Converted GIF exceeds size limit; sending original file', flush=True)
                    else:
                        print(f"[STICKER-HANDLER] ffmpeg error: {stderr.decode()}", flush=True)
                except Exception as e:
                    print(f'Sticker conversion skipped/failed: {e}', flush=True)
                finally:
                    if os.path.exists(temp_in_path):
                        os.remove(temp_in_path)
                    if os.path.exists(temp_gif_path):
                        os.remove(temp_gif_path)
                await self._send_to_webhook(webhook_url, sender, text, file_data, filename)
                # <<< STICKER TO GIF CONVERSION END <<<

            elif message.text:
                text = message.text
                await self._send_to_webhook(webhook_url, sender, text)

            else:
                text = '*(unsupported message type)*'
                await self._send_to_webhook(webhook_url, sender, text)

        except Exception as e:
            print(f'failed to forward message from {sender}: {e}')