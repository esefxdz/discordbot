# this is where the channel archiver lives
import os
import json
import discord
from discord.ext import commands
from datetime import datetime

# characters that are illegal in Windows folder names
_WIN_INVALID_CHARS = '<>:"/\\|?*'


class Archive(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _owner_only(ctx) -> bool:
        """True if the invoker is the bot owner."""
        owner_id = os.getenv('OWNER_ID')
        if not owner_id:
            return False
        return ctx.author.id == int(owner_id)

    @staticmethod
    def _safe_folder(name: str, obj_id: int) -> str:
        """Sanitize *name* so it is safe on Windows and append the id so
        duplicate names never collide."""
        safe = name
        for ch in _WIN_INVALID_CHARS:
            safe = safe.replace(ch, '_')
        safe = safe.rstrip('. ').strip()
        if not safe:
            safe = str(obj_id)
        return f'{safe}_{obj_id}'

    @staticmethod
    def _write_output(chan_dir: str, messages_data: list) -> None:
        """Persist messages_data as messages.json and messages.txt."""
        # structured JSON
        json_path = os.path.join(chan_dir, 'messages.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(messages_data, f, ensure_ascii=False, indent=2)

        # human-readable text log
        txt_path = os.path.join(chan_dir, 'messages.txt')
        with open(txt_path, 'w', encoding='utf-8') as f:
            for msg in messages_data:
                f.write(
                    f"[{msg['timestamp']}] {msg['author']}: "
                    f"{msg['content']}\n"
                )
                for att in msg['attachments']:
                    name = att.get('filename', att.get('saved_as', '???'))
                    f.write(f'  \U0001f4ce {name}\n')
                f.write('\n')

    async def _archive_one_channel(
        self, channel, base_dir: str
    ) -> tuple[int, int, str | None]:
        """Archive a single channel into *base_dir*/<safe_name>/.

        Returns (message_count, attachment_count, error_string_or_None).
        Every failure is caught so one bad channel / message / attachment
        never kills the whole operation.
        """
        folder = self._safe_folder(channel.name or str(channel.id), channel.id)
        chan_dir = os.path.join(base_dir, folder)
        att_dir = os.path.join(chan_dir, 'attachments')
        os.makedirs(att_dir, exist_ok=True)

        messages_data: list[dict] = []
        msg_count = 0
        att_count = 0

        try:
            async for message in channel.history(limit=None, oldest_first=True):
                record = {
                    'id': message.id,
                    'author': str(message.author),
                    'author_id': message.author.id,
                    'content': message.content or '',
                    'timestamp': message.created_at.isoformat(),
                    'embeds': [e.to_dict() for e in message.embeds],
                    'attachments': [],
                }

                for att in message.attachments:
                    safe_fname = f'{message.id}_{att.filename}'
                    filepath = os.path.join(att_dir, safe_fname)
                    try:
                        await att.save(filepath)
                        record['attachments'].append({
                            'filename': att.filename,
                            'saved_as': safe_fname,
                            'url': att.url,
                            'size_bytes': att.size,
                        })
                        att_count += 1
                    except Exception as exc:
                        record['attachments'].append({
                            'filename': att.filename,
                            'error': str(exc),
                        })

                messages_data.append(record)
                msg_count += 1

        except discord.Forbidden:
            return 0, 0, 'forbidden (missing read perms)'
        except discord.HTTPException as exc:
            return msg_count, att_count, f'HTTP error: {exc}'
        except Exception as exc:
            return msg_count, att_count, f'unexpected error: {exc}'

        # persist to disk
        if messages_data:
            try:
                self._write_output(chan_dir, messages_data)
            except Exception as exc:
                return msg_count, att_count, f'disk write error: {exc}'

        return msg_count, att_count, None

    # ── single-channel archive ──────────────────────────────────────────────
    @commands.command()
    async def archive(self, ctx):
        """Archive all messages + media from the current channel.
        Owner-only.
        """
        if not self._owner_only(ctx):
            return

        channel = ctx.channel
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_dir = os.path.join('cogs', 'archived', f'{channel.id}_{timestamp}')

        status = await ctx.reply(
            f'\U0001f4e6 Archiving **#{channel.name}**… this may take a while.'
        )

        msgs, atts, err = await self._archive_one_channel(channel, out_dir)

        if err:
            await status.edit(content=(
                f'\u26a0\ufe0f Archive finished with errors.\n'
                f'**{msgs}** messages · **{atts}** attachments\n'
                f'Error: {err}\n`{out_dir}`'
            ))
        else:
            await status.edit(content=(
                f'\u2705 Archive complete! **{msgs}** messages · '
                f'**{atts}** attachments\n`{out_dir}`'
            ))

    # ── server-wide archive ─────────────────────────────────────────────────
    @commands.command()
    async def archiveserver(self, ctx):
        """Archive every text channel in the server.
        Owner-only. Resumes past any failure.
        """
        if not self._owner_only(ctx):
            return

        guild = ctx.guild
        if not guild:
            return await ctx.reply('This command only works inside a server.')

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        server_dir = os.path.join(
            'cogs', 'archived', f'{self._safe_folder(guild.name, guild.id)}_{timestamp}'
        )
        os.makedirs(server_dir, exist_ok=True)

        # only channels the bot can actually read
        text_channels = [
            ch for ch in guild.text_channels
            if ch.permissions_for(guild.me).read_messages
            and ch.permissions_for(guild.me).read_message_history
        ]

        status = await ctx.reply(
            f'\U0001f4e6 Archiving **{guild.name}** '
            f'(_{len(text_channels)} channels_)… buckle up.'
        )

        results: list[tuple[str, int, int]] = []   # (name, msgs, atts)
        failures: list[tuple[str, str]] = []        # (name, reason)
        grand_msgs = 0
        grand_atts = 0

        for i, channel in enumerate(text_channels):
            # refresh status
            await status.edit(content=(
                f'\U0001f4e6 **{guild.name}** [{i + 1}/{len(text_channels)}] '
                f'#{channel.name}…'
            ))

            msgs, atts, err = await self._archive_one_channel(channel, server_dir)

            if err:
                failures.append((channel.name, err))
            else:
                results.append((channel.name, msgs, atts))
                grand_msgs += msgs
                grand_atts += atts

        # ── assemble final report ───────────────────────────────────────
        lines = [
            f'\u2705 **Server archive finished!**',
            f'\U0001f4ca {grand_msgs} messages · {grand_atts} attachments · '
            f'{len(text_channels)} channels',
        ]

        if results:
            lines.append('')
            lines.append('**Archived:**')
            for name, m, a in results:
                lines.append(f'  \u2022 #{name} — {m} msgs, {a} files')

        if failures:
            lines.append('')
            lines.append('**\u26a0\ufe0f Failed:**')
            for name, reason in failures:
                lines.append(f'  \u2022 #{name} — {reason}')

        lines.append(f'\n`{server_dir}`')

        full = '\n'.join(lines)
        if len(full) > 2000:
            # trim to summary
            full = (
                f'\u2705 **Server archive finished!**\n'
                f'\U0001f4ca {grand_msgs} messages · {grand_atts} attachments · '
                f'{len(text_channels)} channels · {len(failures)} failed\n'
                f'`{server_dir}`'
            )
        await status.edit(content=full)

        # ── server metadata sidecar ─────────────────────────────────────
        try:
            meta = {
                'server_name': guild.name,
                'server_id': guild.id,
                'member_count': guild.member_count,
                'channels_scanned': len(text_channels),
                'channels_failed': len(failures),
                'total_messages': grand_msgs,
                'total_attachments': grand_atts,
                'failed': [
                    {'channel': name, 'reason': reason}
                    for name, reason in failures
                ],
            }
            with open(os.path.join(server_dir, '_server_info.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # metadata is nice-to-have, never fatal


async def setup(bot):
    await bot.add_cog(Archive(bot))
