# Discord → Telegram forwarding
import io
import discord
from discord.ext import commands
from telegram import Bot
from telegram.error import TelegramError


class DiscordToTelegramForwarder(commands.Cog):
    """
    Listens for Discord messages in configured channels and forwards them to
    the paired Telegram group.

    Routes are added via add_route(discord_channel_id, telegram_chat_id).
    Bot messages and webhook messages are silently ignored to prevent loops.
    """

    def __init__(self, bot: commands.Bot, tg_token: str):
        self.bot = bot
        self._tg_bot = Bot(token=tg_token)
        # discord_channel_id (int) -> telegram_chat_id (int)
        self.routes: dict[int, int] = {}

    def add_route(self, discord_channel_id: int, telegram_chat_id: int):
        self.routes[discord_channel_id] = telegram_chat_id

    # -- helpers --------------------------------------------------------------

    def _format_header(self, message: discord.Message) -> str:
        """Return a bold sender label: *Username* (Discord)"""
        name = message.author.display_name
        return f"{discord.utils.escape_markdown(name)}"

    async def _send_text(self, chat_id: int, text: str):
        try:
            await self._tg_bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown',
            )
        except TelegramError as e:
            print(f'[D->TG] send_message failed: {e}')

    async def _send_attachment(self, chat_id: int, attachment: discord.Attachment, caption: str):
        """Download a Discord attachment and upload it to Telegram."""
        try:
            file_bytes = await attachment.read()
            buf = io.BytesIO(file_bytes)
            buf.name = attachment.filename

            mime = attachment.content_type or ''

            if mime.startswith('image/') and not mime.startswith('image/gif'):
                await self._tg_bot.send_photo(chat_id=chat_id, photo=buf, caption=caption, parse_mode='Markdown')
            elif mime.startswith('video/') or mime == 'image/gif':
                await self._tg_bot.send_video(chat_id=chat_id, video=buf, caption=caption, parse_mode='Markdown')
            elif mime.startswith('audio/'):
                await self._tg_bot.send_audio(chat_id=chat_id, audio=buf, caption=caption, parse_mode='Markdown')
            else:
                await self._tg_bot.send_document(chat_id=chat_id, document=buf, caption=caption, parse_mode='Markdown')

        except TelegramError as e:
            print(f'[D->TG] send_attachment failed ({attachment.filename}): {e}')
        except Exception as e:
            print(f'[D->TG] unexpected error for attachment {attachment.filename}: {e}')

    # -- event handler --------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bots (including ourselves) and webhook posts to prevent echo loops
        if message.author.bot or message.webhook_id:
            return

        chat_id = self.routes.get(message.channel.id)
        if chat_id is None:
            return

        header = self._format_header(message)
        body = message.content or ''

        # text portion
        if body or not message.attachments:
            full_text = f"{header}\n{body}".strip()
            await self._send_text(chat_id, full_text)

        # attachments
        for i, attachment in enumerate(message.attachments):
            # first attachment gets the header as caption; rest are bare
            if i == 0:
                caption = header if body else f"{header}\n{body}".strip()
            else:
                caption = ''
            await self._send_attachment(chat_id, attachment, caption)

        print(f'[D->TG] {message.author} (channel {message.channel.id}) -> tg chat {chat_id}')
