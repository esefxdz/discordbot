#this runs live health checks for the gacha system, used by !gacha health##
"""Gacha health checks — every row is a check actually run just now.

Nothing here changes bot state: live data is fetched and compared, never
written into the banner cache or the roster.
"""
######################################################################
import asyncio
import io
import time
from pathlib import Path
from typing import Optional

import aiohttp
from PIL import Image

from . import db as gacha_db
from .data import db, fetch_banners, banner_unavailable_reason
from .gacha_renderer import render_pull
from .constants import CHARACTER_API, BG_PATH, GACHA_ANIM_PATH, FONT_PATHS

OK, WARN, FAIL, INFO = "✅", "⚠️", "❌", "ℹ️"
REFRESH_INTERVAL = 6 * 3600  # must match BlueArchiveGacha._periodic_refresh
MAX_PORTRAIT_PROBES = 6
BANNER_TIMEOUT = 20

Row = tuple[str, str, str]  # (status, section, text)


def _ago(ts: Optional[float]) -> str:
    return f"<t:{int(ts)}:R>" if ts else "never"


async def _probe_image(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Fetch and decode an image exactly like the renderer. None = success."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                return f"HTTP {resp.status}"
            data = await resp.read()
        Image.open(io.BytesIO(data)).convert("RGBA")
        return None
    except asyncio.TimeoutError:
        return "timed out after 5s"
    except Exception as e:
        return type(e).__name__


async def _check_banners(cog) -> tuple[list[Row], list[dict]]:
    """Cached banner state (what pulls use) + a live fetch compared against it."""
    rows: list[Row] = []
    s = "Banners"
    cache = cog._banner_cache
    cached = cache.get("current", []) + cache.get("upcoming", [])

    if cog._last_good_at is None:
        rows.append((FAIL, s, "Banner data has never been fetched successfully — every pull falls back to no rate-up"))
    elif cog._last_refresh_ok is False:
        rows.append((WARN, s, f"Last refresh {_ago(cog._last_refresh_at)} FAILED; pulls use data from {_ago(cog._last_good_at)}"))
    else:
        rows.append((OK, s, f"Pull data refreshed {_ago(cog._last_good_at)} ({len(cached)} current/upcoming)"))

    try:
        live = await asyncio.wait_for(fetch_banners(), timeout=BANNER_TIMEOUT)
    except asyncio.TimeoutError:
        live = None
        rows.append((FAIL, s, f"Banner API check now: timed out after {BANNER_TIMEOUT}s"))
    else:
        if live is None:
            rows.append((FAIL, s, "Banner API check now: request failed (see bot log)"))
    if live is not None:
        live_list = live.get("current", []) + live.get("upcoming", [])
        if {str(b.get("id")) for b in live_list} == {str(b.get("id")) for b in cached}:
            rows.append((OK, s, "Banner API check now: reachable, matches pull data"))
        else:
            rows.append((WARN, s, f"Banner API check now: reachable, but has {len(live_list)} banners vs {len(cached)} "
                                  "in pull data — pulls use stale data until the next refresh"))

    now_ms = time.time() * 1000
    live_banners = [b for b in cached if banner_unavailable_reason(b) is None]
    upcoming = [b for b in cached if b.get("startedAt", 0) > now_ms]
    ended = len(cached) - len(live_banners) - len(upcoming)
    rows.append((INFO, s, f"{len(live_banners)} live now, {len(upcoming)} upcoming"
                          + (f", {ended} ended but still listed (blocked from pulls)" if ended else "")))
    return rows, live_banners


def _check_rateups(live_banners: list[dict]) -> tuple[list[Row], list[dict]]:
    """Run each live banner through the exact pool builder pulls use."""
    rows: list[Row] = []
    s = "Rate-ups (live banners)"
    students: list[dict] = []
    if not live_banners:
        rows.append((INFO, s, "No live banners to check"))
        return rows, students

    for b in live_banners[:6]:
        pool = db.build_pool(b)
        pooled = {st["Name"].lower() for r in pool.values() for st in r}
        label = f"#{b.get('id')} {b.get('gachaType', '?')}"
        problems: list[tuple[str, str]] = []  # (status, text)
        for name in b.get("rateups", []):
            st = db.get_by_name(name, strict=True)
            if not st:
                problems.append((FAIL, f"{name}: not in roster, can't be pulled"))
            elif st["Name"].lower() not in pooled:
                problems.append((FAIL, f"{name}: marked welfare, excluded from pulls"))
            else:
                students.append(st)
                if st["StarGrade"] < 3:
                    problems.append((WARN, f"{name}: {st['StarGrade']}★, rate-up boost only applies to 3★"))
        if problems:
            status = FAIL if any(p[0] == FAIL for p in problems) else WARN
            rows.append((status, s, f"{label}: " + "; ".join(p[1] for p in problems)))
        else:
            names = ", ".join(b.get("rateups", [])) or "no rate-ups"
            rows.append((OK, s, f"{label}: all pullable ({names})"))
    if len(live_banners) > 6:
        rows.append((INFO, s, f"{len(live_banners) - 6} more live banners not checked"))
    return rows, students


async def _check_portraits(students: list[dict]) -> list[Row]:
    """Probe wiki art and the sprite fallback separately for rate-up students."""
    s = "Portraits"
    if not students:
        return [(INFO, s, "No live rate-up students to probe")]
    seen, sample = set(), []
    for st in students:
        if st["Name"] not in seen:
            seen.add(st["Name"])
            sample.append(st)
    sample = sample[:MAX_PORTRAIT_PROBES]

    rows: list[Row] = []
    timeout = aiohttp.ClientTimeout(total=30, connect=5)  # same as render_pull
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(*(
            asyncio.gather(_probe_image(session, db.cdn_portrait(st)), _probe_image(session, db.cdn_sprite(st)))
            for st in sample
        ))
    for st, (wiki, sprite) in zip(sample, results):
        if wiki is None:
            rows.append((OK, s, f"{st['Name']}: wiki art OK"))
        elif sprite is None:
            rows.append((WARN, s, f"{st['Name']}: wiki art failed ({wiki}), sprite fallback OK"))
        else:
            rows.append((FAIL, s, f"{st['Name']}: no art (wiki {wiki}, sprite {sprite}) — card renders blank"))
    return rows


async def _check_roster() -> list[Row]:
    s = "Roster"
    std = db.build_pool(None)  # exactly what Regular Recruitment pulls from
    welfare = sum(1 for st in db.students if (st.get("IsLimited") or 0) >= 2)
    limited = sum(1 for st in db.students if (st.get("IsLimited") or 0) == 1)
    rows: list[Row] = [(OK if db.students else FAIL, s,
                        f"{len(db.students)} students loaded — standard pool 3★ {len(std[3])}, "
                        f"2★ {len(std[2])}, 1★ {len(std[1])}; {limited} limited (Fes/own banner only); "
                        f"{welfare} welfare (never pulled)")]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CHARACTER_API, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    rows.append((FAIL, s, f"Character API check now: HTTP {resp.status}"))
                    return rows
                chars = await resp.json()
    except asyncio.TimeoutError:
        rows.append((FAIL, s, "Character API check now: timed out after 15s"))
        return rows
    except Exception as e:
        rows.append((FAIL, s, f"Character API check now: {type(e).__name__}"))
        return rows
    missing = [c.get("name", "?") for c in chars if c.get("id") not in db.by_id]
    if missing:
        shown = ", ".join(missing[:5]) + (f" +{len(missing) - 5} more" if len(missing) > 5 else "")
        rows.append((WARN, s, f"Character API check now: {len(missing)} not loaded yet ({shown}); "
                              "added on next refresh"))
    else:
        rows.append((OK, s, f"Character API check now: reachable, all {len(chars)} loaded"))
    return rows


def _check_refresh_task(cog) -> list[Row]:
    s = "Background refresh"
    t = getattr(cog, "_refresh_task", None)
    if t is None:
        return [(FAIL, s, "Refresh task was never started — banners will never update")]
    if t.cancelled():
        return [(FAIL, s, "Refresh task was cancelled — banners will not update")]
    if t.done():
        return [(FAIL, s, f"Refresh task crashed: {t.exception()!r} — banners will not update")]
    due = (cog._last_refresh_at or 0) + REFRESH_INTERVAL
    return [(OK, s, f"Running, next refresh <t:{int(due)}:R>")]


async def _check_storage(user_id: int) -> list[Row]:
    s = "Storage & render"
    rows: list[Row] = []
    try:
        await gacha_db.get_inventory_stats(user_id)
        rows.append((OK, s, "Inventory database: read OK (writes not tested)"))
    except Exception as e:
        rows.append((FAIL, s, f"Inventory database read failed: {type(e).__name__}"))

    if any(Path(fp).exists() for fp in FONT_PATHS):
        rows.append((OK, s, "Font found"))
    else:
        rows.append((WARN, s, "No font found — render uses tiny default font"))
    rows.append((OK, s, "Background image found") if BG_PATH.exists()
                else (WARN, s, "Background image missing — plain colour used"))
    rows.append((OK, s, "Pull animation found") if GACHA_ANIM_PATH.exists()
                else (WARN, s, "Pull animation missing — text teaser used"))

    sample = next(iter(db.by_rarity.get(3) or db.students), None)
    if sample is None:
        rows.append((FAIL, s, "Render test skipped: no students loaded"))
        return rows
    try:
        start = time.monotonic()
        await asyncio.wait_for(render_pull([sample], "Health check", 0), timeout=30)
        rows.append((OK, s, f"Render test OK in {time.monotonic() - start:.1f}s (art checked under Portraits)"))
    except asyncio.TimeoutError:
        rows.append((FAIL, s, "Render test timed out after 30s"))
    except Exception as e:
        rows.append((FAIL, s, f"Render test failed: {type(e).__name__}: {e}"))
    return rows


def _check_user_banner(cog, banner_id: Optional[str]) -> list[Row]:
    s = "Your banner"
    if not banner_id or banner_id == "regular":
        return [(OK, s, "Regular Recruitment")]
    for b in cog._all_banners():
        if str(b.get("id")) == banner_id:
            reason = banner_unavailable_reason(b)
            if reason:
                return [(WARN, s, f"#{banner_id}: {reason} Pick another with `!gacha pick`")]
            return [(OK, s, f"#{banner_id} {b.get('gachaType', '?')} is live")]
    return [(WARN, s, f"#{banner_id} is no longer listed — `!pull` will ask you to pick another")]


async def collect_health(cog, user_id: int, banner_id: Optional[str]) -> list[Row]:
    """Run every check. Network checks run concurrently."""
    (banner_rows, live_banners), roster_rows, storage_rows = await asyncio.gather(
        _check_banners(cog), _check_roster(), _check_storage(user_id),
    )
    rateup_rows, students = _check_rateups(live_banners)
    portrait_rows = await _check_portraits(students)
    return (
        banner_rows + rateup_rows + portrait_rows + roster_rows
        + _check_refresh_task(cog) + storage_rows + _check_user_banner(cog, banner_id)
    )
