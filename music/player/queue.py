import discord
import random
from collections import deque
from discord.ext import commands
from ..shared.constants import C_QUEUE, C_OK, C_ERR, C_HIST, LOOP_LABEL
from ..shared.utils import fmt_duration, is_dj


class QueueCommands:
    """Mixin: queue management commands (queue, remove, move, shuffle, clear, history)."""

    # ── !queue ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["q"])
    async def queue(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)

        if not st.current and not st.queue:
            return await ctx.reply(embed=discord.Embed(
                description="📋 Queue is empty.", color=C_QUEUE))

        embed = discord.Embed(title="📋  Queue", color=C_QUEUE)

        if st.current:
            embed.add_field(
                name="▶️  Now Playing",
                value=f"**{st.current.title}** `{fmt_duration(st.current.length)}`",
                inline=False,
            )

        if st.queue:
            lines = []
            for i, t in enumerate(list(st.queue)[:15], 1):
                lines.append(f"`{i:02}.` **{t.title}** `{fmt_duration(t.length)}`")
            if len(st.queue) > 15:
                lines.append(f"*... and {len(st.queue) - 15} more*")
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)

        total_ms = sum(t.length for t in st.queue)
        embed.set_footer(text=f"{len(st.queue)} tracks · {fmt_duration(total_ms)} remaining · Loop: {LOOP_LABEL[st.loop]}")
        await ctx.reply(embed=embed)

    # ── !remove ───────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def remove(self, ctx: commands.Context, position: int):
        st = self.state(ctx.guild.id)
        if position < 1 or position > len(st.queue):
            return await ctx.reply(embed=discord.Embed(
                description=f"❌ Position must be between 1 and {len(st.queue)}. Use `!queue` to see positions.",
                color=C_ERR))
        q = list(st.queue)
        removed = q.pop(position - 1)
        st.queue = deque(q)
        await ctx.reply(embed=discord.Embed(
            description=f"🗑️ Removed **{removed.title}** from queue.", color=C_OK))

    # ── !move ─────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        st   = self.state(ctx.guild.id)
        size = len(st.queue)
        if not (1 <= from_pos <= size) or not (1 <= to_pos <= size):
            return await ctx.reply(embed=discord.Embed(
                description=f"❌ Invalid positions. Queue has {size} items. Use `!queue` to see positions.",
                color=C_ERR))
        q = list(st.queue)
        track = q.pop(from_pos - 1)
        q.insert(to_pos - 1, track)
        st.queue = deque(q)
        await ctx.reply(embed=discord.Embed(
            description=f"↕️ Moved **{track.title}** to position **{to_pos}**.", color=C_OK))

    # ── !shuffle ──────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def shuffle(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        if not st.queue:
            return await ctx.reply(embed=discord.Embed(
                description="❌ Queue is empty.", color=C_ERR))
        q = list(st.queue)
        random.shuffle(q)
        st.queue = deque(q)
        await ctx.reply(embed=discord.Embed(description="🔀 Queue shuffled.", color=C_OK))

    # ── !clear ────────────────────────────────────────────────────────────────
    @commands.command()
    @is_dj()
    async def clear(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        st.queue.clear()
        await ctx.reply(embed=discord.Embed(description="🗑️ Queue cleared.", color=C_OK))

    # ── !history ──────────────────────────────────────────────────────────────
    @commands.command(aliases=["h"])
    async def history(self, ctx: commands.Context):
        st = self.state(ctx.guild.id)
        if not st.history:
            return await ctx.reply(embed=discord.Embed(
                description="📜 No history yet.", color=C_HIST))
        lines = [f"`{i:02}.` **{t.title}** `{fmt_duration(t.length)}`"
                 for i, t in enumerate(st.history, 1)]
        embed = discord.Embed(
            title="📜  Recently Played",
            description="\n".join(lines),
            color=C_HIST,
        )
        await ctx.reply(embed=embed)
