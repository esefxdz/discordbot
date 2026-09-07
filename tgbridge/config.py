#bridge routes, discovered from env instead of hardcoded in main.py##
"""Route configuration for the bridge.

Any TELEGRAM_GROUP_ID_<SUFFIX> + DISCORD_WEBHOOK_URL_<SUFFIX> +
DISCORD_CHANNEL_ID_<SUFFIX> trio becomes a route, so a new bridged channel is
an .env edit. The unsuffixed trio still works, as "main".
data/bridge_routes.json adds to or overrides them.
"""
######################################################################
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ROUTES_FILE = Path(__file__).parent.parent / "data" / "bridge_routes.json"

_TG_PREFIX = "TELEGRAM_GROUP_ID"
_WH_PREFIX = "DISCORD_WEBHOOK_URL"
_CH_PREFIX = "DISCORD_CHANNEL_ID"


@dataclass
class Route:
    name: str
    tg_chat_id: int
    dc_channel_id: int | None = None
    webhook_url: str | None = None

    @property
    def tg_to_dc(self) -> bool:
        return bool(self.webhook_url)

    @property
    def dc_to_tg(self) -> bool:
        return bool(self.dc_channel_id)


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _env_routes() -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for key, value in os.environ.items():
        if not key.startswith(_TG_PREFIX):
            continue
        suffix = key[len(_TG_PREFIX):]      # "" or "_SHITPOST"
        chat_id = _int_or_none(value)
        if chat_id is None:
            log.warning("%s is not a valid chat id, skipping", key)
            continue
        name = suffix.lstrip("_").lower() or "main"
        routes[name] = Route(
            name=name,
            tg_chat_id=chat_id,
            dc_channel_id=_int_or_none(os.getenv(_CH_PREFIX + suffix)),
            webhook_url=os.getenv(_WH_PREFIX + suffix) or None,
        )
    return routes


def _file_routes() -> dict[str, Route]:
    if not ROUTES_FILE.exists():
        return {}
    try:
        raw = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("could not read %s: %s", ROUTES_FILE, exc)
        return {}

    routes = {}
    for index, entry in enumerate(raw or []):
        chat_id = _int_or_none(entry.get("telegram_chat_id"))
        if chat_id is None:
            log.warning("route %d in %s has no telegram_chat_id", index, ROUTES_FILE)
            continue
        name = entry.get("name") or f"route{index}"
        routes[name] = Route(
            name=name,
            tg_chat_id=chat_id,
            dc_channel_id=_int_or_none(entry.get("discord_channel_id")),
            webhook_url=entry.get("webhook_url") or None,
        )
    return routes


def load_routes() -> list[Route]:
    """Env routes first; the json file wins on a name clash."""
    routes = _env_routes()
    routes.update(_file_routes())

    usable = []
    for route in routes.values():
        if not (route.tg_to_dc or route.dc_to_tg):
            log.warning("route %r has neither a webhook nor a channel — ignored",
                        route.name)
            continue
        usable.append(route)

    for route in usable:
        directions = []
        if route.tg_to_dc:
            directions.append("tg->dc")
        if route.dc_to_tg:
            directions.append("dc->tg")
        log.info("bridge route %r: chat %s <-> channel %s (%s)",
                 route.name, route.tg_chat_id, route.dc_channel_id,
                 ", ".join(directions))
    return usable


def avatar_channel_id() -> int | None:
    return _int_or_none(os.getenv("AVATAR_CACHE_CHANNEL_ID"))
