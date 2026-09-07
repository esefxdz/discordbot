#two-way telegram <-> discord bridge, loaded as an extension##
"""tgbridge — two-way Telegram <-> Discord bridge.

Polling starts at extension load, not on_ready: on_ready fires again after
every gateway reconnect and a second poller on one token means telegram 409s.
"""
######################################################################
import asyncio
import logging
import os

from discord.ext import commands

from . import config
from .discord_to_telegram import DiscordToTelegramForwarder
from .forwarder import TelegramForwarder
from .status_cog import BridgeStatus

log = logging.getLogger(__name__)

_forwarder: TelegramForwarder | None = None


async def setup(bot: commands.Bot) -> None:
    global _forwarder

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN is not set — bridge not loaded")
        return

    routes = config.load_routes()
    if not routes:
        log.warning("no bridge routes configured — bridge not loaded")
        return

    replay = os.getenv("TELEGRAM_REPLAY_BACKLOG", "").lower() in ("1", "true", "yes")

    _forwarder = TelegramForwarder(
        token=token,
        discord_bot=bot,
        avatar_channel_id=config.avatar_channel_id(),
        routes=routes,
        drop_pending=not replay,
    )
    reverse = DiscordToTelegramForwarder(bot=bot, tg_token=token, routes=routes)

    await bot.add_cog(reverse)
    await bot.add_cog(BridgeStatus(bot, _forwarder, reverse))
    bot.tg_bridge = _forwarder

    # setup() runs before the gateway connects, so raising here stops the whole
    # bot booting. retry in the background instead; !bridge status shows it.
    try:
        await _forwarder.start()
    except Exception as exc:
        log.error("telegram bridge failed to start (%s) — retrying in the "
                  "background; the rest of the bot is unaffected", exc)
        _forwarder.last_error = f"start failed: {exc}"
        # keep the reference; a bare task can be gc'd mid-flight
        bot.tg_bridge_retry = asyncio.create_task(_retry_start(_forwarder))


async def _retry_start(forwarder) -> None:
    delay = 30
    for attempt in range(5):
        await asyncio.sleep(delay)
        try:
            await forwarder.start()
            log.info("telegram bridge recovered on attempt %d", attempt + 1)
            return
        except Exception as exc:
            log.warning("telegram bridge retry %d failed: %s", attempt + 1, exc)
            forwarder.last_error = f"start failed: {exc}"
            delay = min(delay * 2, 600)
    log.error("telegram bridge gave up after 5 retries — restart the bot")


async def teardown(bot: commands.Bot) -> None:
    global _forwarder
    task = getattr(bot, "tg_bridge_retry", None)
    if task and not task.done():
        task.cancel()
    if _forwarder is not None:
        await _forwarder.stop()
        _forwarder = None


__all__ = ["TelegramForwarder", "DiscordToTelegramForwarder", "setup", "teardown"]
