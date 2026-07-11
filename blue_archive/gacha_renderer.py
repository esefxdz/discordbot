#this renders the gacha recruitment result screen using pillow, mimics the in-game look##
"""Pillow-based compositor: renders an in-game-style gacha recruitment result screen."""
######################################################################
import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from .data import db

log = logging.getLogger(__name__)

# Layout: 1920x1080 canvas, 5x2 card grid, 10px rounded corners
# Each card: star banner → portrait → name strip
CANVAS_W, CANVAS_H = 1920, 1080
CARD_W, CARD_H = 280, 390
CARD_RADIUS = 10                             # corner radius
GAP_X, GAP_Y = 25, 20
COLS, ROWS = 5, 2
GRID_W = COLS * CARD_W + (COLS - 1) * GAP_X  # 1500
GRID_H = ROWS * CARD_H + (ROWS - 1) * GAP_Y  # 800
GRID_X = (CANVAS_W - GRID_W) // 2            # 210
GRID_Y = 260                                 # below the header
HEADER_Y = 120
FOOTER_Y = GRID_Y + GRID_H + 50

STAR_BANNER_H = 36                            # star rating strip at top of card
NAME_STRIP_H = 42                             # name/school strip at bottom
PORTRAIT_TOP = STAR_BANNER_H + 4              # portrait starts below star banner
PORTRAIT_H = CARD_H - PORTRAIT_TOP - NAME_STRIP_H - 4

# Rarity frame and star colours
RARITY_COLORS = {
    3: (240, 190, 255),  # bright purple
    2: (255, 250, 140),  # bright gold
    1: (170, 225, 255),  # bright sky blue
}
RARITY_BG = {
    3: (38, 22, 50),
    2: (50, 42, 22),
    1: (22, 30, 50),
}

ASSETS_DIR = Path(__file__).parent / "assets"
BG_PATH = ASSETS_DIR / "gacha_bg.png"

# ── Font helpers ───────────────────────────────────────────────────────────

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a nice font; fall back to default."""
    font_paths = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for fp in font_paths:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


HEADER_FONT = _get_font(52, bold=True)
FOOTER_FONT = _get_font(18, bold=False)


# ── Image fetching ─────────────────────────────────────────────────────────

async def _fetch_portrait(session: aiohttp.ClientSession, student: dict) -> Optional[Image.Image]:
    """Download a student's face portrait.

    Tries the wiki CDN first (252x204 face image). Falls back to SchaleDB
    full-body sprite with an upper-body crop on failure.
    """
    student_id = student["Id"]

    # 1. Try wiki face portrait
    wiki_url = db.wiki_portrait(student)
    try:
        async with session.get(wiki_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        log.debug("Wiki portrait failed for %s, trying SchaleDB fallback", student["Name"])

    # 2. Fallback: SchaleDB full-body sprite with upper-body crop
    schale_url = db.schale_portrait(student_id)
    try:
        async with session.get(schale_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                # Crop top portion for face/upper-body (source is ~614x1024)
                p_w, p_h = img.size
                crop_h = int(p_w * 1.20)
                if crop_h < p_h:
                    img = img.crop((0, 0, p_w, crop_h))
                return img
    except Exception:
        log.exception("Failed to fetch portrait for student %d", student_id)

    return None


# ── Drawing helpers ────────────────────────────────────────────────────────

def _rounded_rect(
    draw: ImageDraw.Draw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: Optional[tuple] = None,
    outline: Optional[tuple] = None,
    width: int = 3,
) -> None:
    """Draw a rounded rectangle."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _star_polygon(cx: int, cy: int, outer_r: int, inner_r: int) -> list[tuple[int, int]]:
    """Generate vertex list for a 5-pointed star centered at (cx, cy).
    
    Uses 10 alternating outer/inner radius points, rotated by pi/5 each step.
    """
    import math
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


def _draw_rounded_rect(
    draw: ImageDraw.Draw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: Optional[tuple] = None,
    outline: Optional[tuple] = None,
    width: int = 3,
) -> None:
    _rounded_rect(draw, xy, radius, fill, outline, width)


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
    # Load background
    if BG_PATH.exists():
        canvas = Image.open(BG_PATH).convert("RGBA")
    else:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (180, 226, 245))
    draw = ImageDraw.Draw(canvas)

    # ── Header ──────────────────────────────────────────────────────────
    _draw_header(draw, banner_name)

    # ── Footer / spark ──────────────────────────────────────────────────
    _draw_footer(draw, spark_count)

    # ── Fetch all portraits ─────────────────────────────────────────────
    timeout = aiohttp.ClientTimeout(total=60, connect=10)
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

    # ── Encode ──────────────────────────────────────────────────────────
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


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
    text = f"Recruitment Points: {spark_count} / 200"
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
    color = RARITY_COLORS.get(rarity, (150, 150, 150))
    bg = RARITY_BG.get(rarity, (20, 20, 20))

    # Card background with rounded corners
    _draw_rounded_rect(draw, (x, y, x + CARD_W, y + CARD_H), radius=CARD_RADIUS, fill=bg + (255,))
    # Card border
    _draw_rounded_rect(
        draw, (x, y, x + CARD_W, y + CARD_H), radius=CARD_RADIUS, outline=color, width=2
    )

    # ── Star banner strip at top ────────────────────────────────────────
    banner_top = y + 1
    banner_bottom = y + STAR_BANNER_H
    # Semi-transparent dark strip
    _draw_rounded_rect(
        draw,
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
        _draw_rounded_rect(draw, (px, py, px + pw, py + ph), radius=6, fill=(30, 30, 40, 180))
        ph_font = _get_font(16)
        msg = "No Image"
        mw = draw.textlength(msg, font=ph_font)
        draw.text((px + (pw - mw) / 2, py + ph / 2 - 10), msg, fill=(150, 150, 150), font=ph_font)

    # ── Name strip at bottom ────────────────────────────────────────────
    name_y = y + CARD_H - NAME_STRIP_H + 2
    _draw_rounded_rect(
        draw,
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
