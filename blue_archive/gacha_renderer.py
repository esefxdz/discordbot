#this renders the gacha recruitment result screen using pillow, mimics the in-game look##
"""Pillow-based compositor: renders an in-game-style gacha recruitment result screen."""
######################################################################
import asyncio
import functools
import io
import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from .data import db
from .constants import (
    CANVAS_W, CANVAS_H,
    CARD_W, CARD_H, CARD_RADIUS,
    GAP_X, GAP_Y, COLS, ROWS,
    GRID_W, GRID_H, GRID_X, GRID_Y,
    HEADER_Y, FOOTER_Y,
    STAR_BANNER_H, NAME_STRIP_H, PORTRAIT_TOP, PORTRAIT_H,
    RARITY_COLOR, RARITY_BG,
    BUST_HEIGHT_FRAC, BUST_HEAD_FRAC,
    BG_PATH, SPARK_TARGET,
    PORTRAIT_CACHE_DIR, WIKI_RETRY_HOURS,
    FONT_PATHS,
)

log = logging.getLogger(__name__)

# ── Font helpers ───────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=16)
def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a nice font; fall back to default.  Cached per (size, bold)."""
    for fp in FONT_PATHS:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


HEADER_FONT = _get_font(52, bold=True)
FOOTER_FONT = _get_font(18, bold=False)


# ── Image fetching ─────────────────────────────────────────────────────────

def _bust_crop(img: Image.Image) -> Image.Image:
    """Crop a full-body sprite to head-and-shoulders so it fills the card.

    Anchored on the alpha box top (Kid variants sit lower in the frame) and on
    the alpha-weighted centre of the top slice, so thin props like rifles and
    parasols barely pull the crop off the face.
    """
    alpha = img.getchannel("A")
    box = alpha.getbbox()
    if not box:
        return img
    left, top, right, bottom = box
    fig_h = bottom - top
    crop_h = min(img.height - top, max(1, int(fig_h * BUST_HEIGHT_FRAC)))
    head = alpha.crop((left, top, right, top + max(1, int(fig_h * BUST_HEAD_FRAC))))
    cols = head.resize((head.width, 1), Image.BOX).tobytes()
    weight = sum(cols)
    offset = sum(x * c for x, c in enumerate(cols)) // weight if weight else head.width // 2
    cx = left + offset
    crop_w = min(img.width, max(1, round(crop_h * (CARD_W - 12) / PORTRAIT_H)))
    x0 = max(0, min(img.width - crop_w, cx - crop_w // 2))
    return img.crop((x0, top, x0 + crop_w, top + crop_h))


def _cached_portrait(student: dict) -> Optional[Image.Image]:
    """Load a cached portrait, or None when one should be (re)fetched."""
    try:
        wiki = PORTRAIT_CACHE_DIR / f"{student['Id']}.wiki.png"
        if wiki.exists():
            return Image.open(wiki).convert("RGBA")
        alt = PORTRAIT_CACHE_DIR / f"{student['Id']}.alt.png"
        if alt.exists() and time.time() - alt.stat().st_mtime < WIKI_RETRY_HOURS * 3600:
            return Image.open(alt).convert("RGBA")
    except Exception:  # unreadable file would otherwise blank the card forever
        log.warning("bad cached portrait for %s, refetching", student["Name"])
    return None


def _cache_portrait(student: dict, img: Image.Image, from_wiki: bool) -> None:
    """Keep fetched art on disk so an upstream outage can't change the cards."""
    try:
        PORTRAIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        kind = "wiki" if from_wiki else "alt"
        path = PORTRAIT_CACHE_DIR / f"{student['Id']}.{kind}.png"
        tmp = path.parent / f"{path.name}.{uuid.uuid4().hex[:8]}.tmp"
        img.save(tmp, format="PNG")  # the .tmp name hides the format from Pillow
        os.replace(tmp, path)  # a restart mid-save must not leave half a card
        if from_wiki:  # wiki art wins from here on, drop the stand-in
            (PORTRAIT_CACHE_DIR / f"{student['Id']}.alt.png").unlink(missing_ok=True)
    except Exception:
        log.warning("could not cache portrait for %s", student["Name"], exc_info=True)


async def _fetch_portrait(session: aiohttp.ClientSession, student: dict) -> Optional[Image.Image]:
    """A student's portrait: disk cache, else wiki art, collection art, sprite."""
    cached = await asyncio.to_thread(_cached_portrait, student)
    if cached is not None:
        return cached

    sources = (
        (db.cdn_portrait(student), "wiki"),
        (db.cdn_collection(student), "collection"),
        (db.cdn_sprite(student), "sprite"),
    )
    for url, kind in sources:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            if kind == "sprite":
                img = _bust_crop(img)
            elif kind == "wiki":
                # Wiki art is a face shot; only trim it if it is very tall.
                p_w, p_h = img.size
                crop_h = int(p_w * 1.20)
                if crop_h < p_h:
                    img = img.crop((0, 0, p_w, crop_h))
            await asyncio.to_thread(_cache_portrait, student, img, kind == "wiki")
            return img
        except Exception:
            log.debug("portrait fetch failed via %s", url)

    log.warning("no portrait for %s", student["Name"])
    return None


# ── Drawing helpers ────────────────────────────────────────────────────────

def _star_polygon(cx: int, cy: int, outer_r: int, inner_r: int) -> list[tuple[int, int]]:
    """Generate vertex list for a 5-pointed star centered at (cx, cy).
    
    Uses 10 alternating outer/inner radius points, rotated by pi/5 each step.
    """
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5  # start from top
        r = outer_r if i % 2 == 0 else inner_r
        x = cx + r * math.cos(angle)
        y = cy - r * math.sin(angle)
        points.append((int(x), int(y)))
    return points


def _draw_star_icons(
    draw: ImageDraw.Draw,
    cx: int,
    cy: int,
    count: int,
    color: tuple,
    size: int = 7,
) -> None:
    """Draw <count> gold 5-pointed star icons horizontally centered at (cx, cy)."""
    outer = size
    inner = int(size * 0.38)
    spacing = int(size * 2.4)
    total_w = (count - 1) * spacing
    start_x = cx - total_w // 2
    for i in range(count):
        pts = _star_polygon(start_x + i * spacing, cy, outer, inner)
        # Shadow
        draw.polygon([(p[0] + 1, p[1] + 1) for p in pts], fill=(0, 0, 0, 100))
        draw.polygon(pts, fill=color)


# ── Sync render helpers (run in thread) ────────────────────────────────────

def _load_background() -> Image.Image:
    """Load or create the render canvas background."""
    if BG_PATH.exists():
        return Image.open(BG_PATH).convert("RGBA")
    return Image.new("RGBA", (CANVAS_W, CANVAS_H), (180, 226, 245))


def _encode_png(canvas: Image.Image) -> io.BytesIO:
    """Encode a Pillow image to optimized PNG bytes."""
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


# ── Main render ────────────────────────────────────────────────────────────

async def render_pull(
    pulls: list[dict],
    banner_name: str = "Regular Recruitment",
    spark_count: int = 0,
) -> io.BytesIO:
    """Render a 10-pull (or single-pull) result as a PNG image.

    Args:
        pulls: List of student dicts from StudentDB (must have Id, Name, StarGrade).
        banner_name: Display name for the banner header.
        spark_count: Current spark/pity count to show in the footer.

    Returns:
        BytesIO containing the rendered PNG image.
    """
    # Load background (file I/O off the event loop)
    canvas = await asyncio.to_thread(_load_background)
    draw = ImageDraw.Draw(canvas)

    # ── Header ──────────────────────────────────────────────────────────
    _draw_header(draw, banner_name)

    # ── Footer / spark ──────────────────────────────────────────────────
    _draw_footer(draw, spark_count)

    # ── Fetch all portraits ─────────────────────────────────────────────
    timeout = aiohttp.ClientTimeout(total=30, connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        portrait_tasks = [_fetch_portrait(session, p) for p in pulls]
        portraits = await asyncio.gather(*portrait_tasks, return_exceptions=True)
        # Unwrap exceptions: failed fetches become None
        for i, result in enumerate(portraits):
            if isinstance(result, Exception):
                log.warning("Portrait %d (%s) failed: %s", pulls[i]["Id"], pulls[i]["Name"], result)
                portraits[i] = None

    # ── Draw cards in grid ──────────────────────────────────────────────
    for idx, (student, portrait) in enumerate(zip(pulls, portraits)):
        col = idx % COLS
        row = idx // COLS
        cx = GRID_X + col * (CARD_W + GAP_X)
        cy = GRID_Y + row * (CARD_H + GAP_Y)
        _draw_card(canvas, draw, cx, cy, student, portrait)

    # ── Encode (CPU-bound — off the event loop) ─────────────────────────
    return await asyncio.to_thread(_encode_png, canvas)


def _draw_header(draw: ImageDraw.Draw, banner_name: str) -> None:
    """Draw the 'Recruitment Results' header at the top."""
    title = "Recruitment Results"
    tw = draw.textlength(title, font=HEADER_FONT)

    # Banner name subtitle
    subtitle = banner_name
    sub_font = _get_font(26, bold=False)

    # Shadow + text
    draw.text(((CANVAS_W - tw) / 2 + 2, HEADER_Y + 2), title, fill=(0, 0, 0, 100), font=HEADER_FONT)
    draw.text(((CANVAS_W - tw) / 2, HEADER_Y), title, fill=(255, 255, 255, 240), font=HEADER_FONT)

    # Subtitle
    sw = draw.textlength(subtitle, font=sub_font)
    draw.text(((CANVAS_W - sw) / 2, HEADER_Y + 60), subtitle, fill=(255, 255, 255, 200), font=sub_font)

    # Decorative line under header
    line_y = HEADER_Y + 100
    draw.line(
        [(GRID_X, line_y), (GRID_X + GRID_W, line_y)],
        fill=(255, 255, 255, 120),
        width=2,
    )


def _draw_footer(draw: ImageDraw.Draw, spark_count: int) -> None:
    """Draw spark counter at the bottom."""
    text = f"Recruitment Points: {spark_count} / {SPARK_TARGET}"
    tw = draw.textlength(text, font=FOOTER_FONT)
    draw.text(
        ((CANVAS_W - tw) / 2 + 1, FOOTER_Y + 1),
        text,
        fill=(0, 0, 0, 80),
        font=FOOTER_FONT,
    )
    draw.text(
        ((CANVAS_W - tw) / 2, FOOTER_Y),
        text,
        fill=(255, 255, 255, 200),
        font=FOOTER_FONT,
    )


def _draw_card(
    canvas: Image.Image,
    draw: ImageDraw.Draw,
    x: int,
    y: int,
    student: dict,
    portrait: Optional[Image.Image],
) -> None:
    """Draw a single recruitment result card at (x, y)."""
    rarity = student["StarGrade"]
    color = RARITY_COLOR.get(rarity, (150, 150, 150))
    bg = RARITY_BG.get(rarity, (20, 20, 20))

    # Card background with rounded corners
    draw.rounded_rectangle((x, y, x + CARD_W, y + CARD_H), radius=CARD_RADIUS, fill=bg + (255,))
    # Card border
    draw.rounded_rectangle(
        (x, y, x + CARD_W, y + CARD_H), radius=CARD_RADIUS, outline=color, width=2
    )

    # ── Star banner strip at top ────────────────────────────────────────
    banner_top = y + 1
    banner_bottom = y + STAR_BANNER_H
    # Semi-transparent dark strip
    draw.rounded_rectangle(
        (x + 3, banner_top, x + CARD_W - 3, banner_bottom),
        radius=CARD_RADIUS - 2,
        fill=(0, 0, 0, 80),
    )
    # Draw star icons centered in the banner
    _draw_star_icons(
        draw,
        x + CARD_W // 2,
        y + STAR_BANNER_H // 2,
        rarity,
        color,
        size=8,
    )

    # ── Portrait area ───────────────────────────────────────────────────
    px = x + 6
    py = y + PORTRAIT_TOP
    pw = CARD_W - 12
    ph = PORTRAIT_H

    if portrait:
        portrait = portrait.copy()
        p_w, p_h = portrait.size
        scale = min(pw / p_w, ph / p_h)
        new_w, new_h = int(p_w * scale), int(p_h * scale)
        portrait = portrait.resize((new_w, new_h), Image.LANCZOS)
        pox = px + (pw - new_w) // 2
        poy = py + (ph - new_h) // 2
        canvas.paste(portrait, (pox, poy), portrait if portrait.mode == "RGBA" else None)
    else:
        draw.rounded_rectangle((px, py, px + pw, py + ph), radius=6, fill=(30, 30, 40, 180))
        ph_font = _get_font(16)
        msg = "No Image"
        mw = draw.textlength(msg, font=ph_font)
        draw.text((px + (pw - mw) / 2, py + ph / 2 - 10), msg, fill=(150, 150, 150), font=ph_font)

    # ── Name strip at bottom ────────────────────────────────────────────
    name_y = y + CARD_H - NAME_STRIP_H + 2
    draw.rounded_rectangle(
        (x + 3, name_y, x + CARD_W - 3, y + CARD_H - 3),
        radius=CARD_RADIUS - 3,
        fill=(0, 0, 0, 150),
    )

    # Student name
    name = student["Name"]
    name_font = _get_font(20, bold=True)
    nw = draw.textlength(name, font=name_font)
    draw.text(
        (x + (CARD_W - nw) / 2 + 1, name_y + 2),
        name,
        fill=(0, 0, 0),
        font=name_font,
    )
    draw.text(
        (x + (CARD_W - nw) / 2, name_y + 1),
        name,
        fill=(255, 255, 255),
        font=name_font,
    )

    # School label
    school = student.get("School", "")
    if school:
        sch_font = _get_font(14)
        sw2 = draw.textlength(school, font=sch_font)
        draw.text(
            (x + (CARD_W - sw2) / 2 + 1, name_y + 23),
            school,
            fill=(0, 0, 0),
            font=sch_font,
        )
        draw.text(
            (x + (CARD_W - sw2) / 2, name_y + 22),
            school,
            fill=color + (200,),
            font=sch_font,
        )
