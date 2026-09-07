"""!bridge — health and routing readout for the Telegram bridge."""
######################################################################
import logging
import time

import discord
from discord.ext import commands

from . import media, store

log = logging.getLogger(__name__)


def _uptime(started_at) -> str:
    if not started_at:
        return "not running"
    seconds = int(time.time() - started_at)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


class BridgeStatus(commands.Cog):
    def __init__(self, bot, forwarder, reverse):
        self.bot = bot
        self.forwarder = forwarder
        self.reverse = reverse

    @commands.command(name="bridge")
    async def bridge(self, ctx, action: str = "status"):
        """Show Telegram bridge routes, counters and health."""
        if action != "status":
            await ctx.reply("usage: `!bridge status`")
            return

        forwarder = self.forwarder
        embed = discord.Embed(
            title="Telegram bridge",
            colour=discord.Colour.green() if forwarder and forwarder._running
            else discord.Colour.red())

        embed.add_field(
            name="Polling",
            value=("running — up " + _uptime(forwarder.started_at))
            if forwarder and forwarder._running else "stopped",
            inline=True)

        avatars = forwarder.avatars if forwarder else None
        embed.add_field(
            name="Avatars",
            value=(f"on ({avatars.hits} hits, {avatars.uploads} uploads)"
                   if avatars and avatars.enabled
                   else "off — set AVATAR_CACHE_CHANNEL_ID"),
            inline=True)

        embed.add_field(
            name="Lottie (.tgs)",
            value="installed" if media.lottie_available() else "not installed",
            inline=True)

        lines = []
        for chat_id, route in (forwarder.routes.items() if forwarder else []):
            channel = self.bot.get_channel(route.dc_channel_id) \
                if route.dc_channel_id else None
            target = channel.mention if channel else f"`{route.dc_channel_id}`"
            arrows = "↔" if route.dc_to_tg and route.tg_to_dc else "→"
            lines.append(f"**{route.name}** `{chat_id}` {arrows} {target}")
        embed.add_field(name=f"Routes ({len(lines)})",
                        value="\n".join(lines) or "none configured",
                        inline=False)

        webhook = forwarder.webhook if forwarder else None
        counters = [
            f"TG → Discord: **{forwarder.forwarded if forwarder else 0}**",
            f"Discord → TG: **{self.reverse.forwarded if self.reverse else 0}**",
        ]
        if webhook:
            counters.append(f"webhook posts: {webhook.sent} ok / {webhook.failed} failed")
        try:
            counters.append(f"cached avatars: {await store.count_avatars()}")
            counters.append(f"cached stickers: {await store.count_media()}")
        except Exception:
            pass
        embed.add_field(name="Counters", value="\n".join(counters), inline=False)

        errors = [e for e in (getattr(forwarder, "last_error", None),
                              getattr(self.reverse, "last_error", None),
                              getattr(webhook, "last_error", None)) if e]
        if errors:
            embed.add_field(name="Last error",
                            value="```" + "\n".join(errors)[:900] + "```",
                            inline=False)

        await ctx.reply(embed=embed)
