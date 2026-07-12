#this holds all shared constants for the blue archive gacha system##
"""Centralised constants — colours, rates, layout, and paths."""
######################################################################
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────
BUNDLED_DB = Path(__file__).parent / "ba_students.json"
BANNER_FILE = Path("data/ba_banner_state.json")
ASSETS_DIR = Path(__file__).parent / "assets"
BG_PATH = ASSETS_DIR / "gacha_bg.png"
GACHA_ANIM_PATH = ASSETS_DIR / "Arona_Blue_Gacha_Animation.gif"
GACHA_ANIM_DURATION = 19.5  # 195 frames × 100 ms

# ── APIs ────────────────────────────────────────────────────────────────────
BANNER_API = "https://api.ennead.cc/buruaka/banner"
JOEXYZ_CDN = "https://ba.joexyz.online/cdn/v2/images"

# ── Gacha rates (matching real Blue Archive) ────────────────────────────────
# Regular/Pickup:   3★ = 3.0%,  2★ = 18.5%,  1★ = 78.5%
# Fes:              3★ = 6.0%,  2★ = 18.5%,  1★ = 75.5%
# 10th pull:        3★ = 3.0%,  2★ = 97.0%,  1★ = 0%   (guaranteed 2★+)
DEFAULT_RATES = (0.03, 0.185, 0.785)
PULL10_RATES = (0.03, 0.97, 0.0)
BANNER_RATES: dict[str, tuple[float, float, float]] = {
    "FesGacha": (0.06, 0.185, 0.755),
}

# ── Spark & Eligma ──────────────────────────────────────────────────────────
SPARK_TARGET = 200
ELIGMA_YIELD = {1: 1, 2: 10, 3: 50}

# ── Rarity colours (frame borders, stars, card backgrounds) ─────────────────
RARITY_COLOR = {
    3: (250, 210, 255),  # bright purple
    2: (255, 252, 160),  # bright gold
    1: (190, 235, 255),  # bright sky blue
}
RARITY_BG = {
    3: (42, 26, 54),
    2: (54, 48, 26),
    1: (26, 34, 54),
}
RARITY_EMBED_COLOR = {
    3: 0xF0D0FF,
    2: 0xFFF9A0,
    1: 0xC0E8FF,
}
RARITY_LABEL = {3: "3★", 2: "2★", 1: "1★"}
RARITY_SYMBOL = {3: "◆", 2: "⬟", 1: "●"}

# ── Canvas & card layout (1920×1080) ────────────────────────────────────────
CANVAS_W, CANVAS_H = 1920, 1080
CARD_W, CARD_H = 280, 390
CARD_RADIUS = 10
GAP_X, GAP_Y = 25, 20
COLS, ROWS = 5, 2
GRID_W = COLS * CARD_W + (COLS - 1) * GAP_X  # 1500
GRID_H = ROWS * CARD_H + (ROWS - 1) * GAP_Y  # 800
GRID_X = (CANVAS_W - GRID_W) // 2            # 210
GRID_Y = 260
HEADER_Y = 120
FOOTER_Y = GRID_Y + GRID_H + 50
STAR_BANNER_H = 36
NAME_STRIP_H = 42
PORTRAIT_TOP = STAR_BANNER_H + 4
PORTRAIT_H = CARD_H - PORTRAIT_TOP - NAME_STRIP_H - 4

# ── Inventory display ───────────────────────────────────────────────────────
PAGE_SIZE = 10
POOL_TOTALS = {3: 106, 2: 22, 1: 11}  # standard-pool size for progress bars
FILTER_LABELS: dict[str, int | None] = {"All": None, "3★": 3, "2★": 2, "1★": 1}

# ── CDN icon slug mapping (SchaleDB variant suffix → joexyz CDN slug suffix) ─
# Base names without a parenthesised variant use the lowercased name directly.
# "Aris" is a special case — the CDN uses "aris", not "arisu".
CDN_SUFFIX_MAP: dict[str, str] = {
    "New Year": "newyear",
    "Hot Spring": "onsen",
    "Bunny": "bunnygirl",
    "Cheer Squad": "cheerleader",
    "Camp": "camp",
    "Cycling": "cycling",
    "Dress": "dress",
    "Small": "small",
    "Maid": "maid",
    "Track": "track",
    "Swimsuit": "swimsuit",
    "Band": "band",
    "Christmas": "christmas",
    "Casual": "casual",
    "Guide": "guide",
}

def cdn_icon_slug(name: str) -> str:
    """Convert a SchaleDB student Name to the joexyz CDN icon filename slug."""
    # Normalise "Name Terror" into the same parenthesised path
    if name.endswith(" Terror"):
        name = name[:-7] + " (Terror)"
    # "Aris" is "aris" on the CDN, not "arisu"
    if name == "Aris":
        return "aris"

    if "(" in name:
        base, rest = name.split("(", 1)
        base = base.strip().lower().replace(" ", "_")
        variant = rest.rstrip(")").strip()
        suffix = CDN_SUFFIX_MAP.get(variant, variant.lower().replace(" ", "_"))
        return f"{base}_{suffix}"

    return name.lower().replace(" ", "_")

# ── Font paths ──────────────────────────────────────────────────────────────
FONT_PATHS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
