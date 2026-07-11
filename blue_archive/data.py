#this module loads student data and fetches live banner info from the api##
"""Student and banner data layer for the Blue Archive gacha simulator."""
######################################################################
import hashlib
import json
import logging
import random
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

BUNDLED_DB = Path(__file__).parent / "ba_students.json"
BANNER_API = "https://api.ennead.cc/buruaka/banner"
SCHALE_PORTRAIT = "https://raw.githubusercontent.com/SchaleDB/SchaleDB/main/images/student/portrait"
SCHALE_ICON = "https://raw.githubusercontent.com/SchaleDB/SchaleDB/main/images/student/icon"

# Wiki portrait CDN: static.wikitide.net/bluearchivewiki/{md5[0]}/{md5[:2]}/{filename}
WIKI_CDN = "https://static.wikitide.net/bluearchivewiki"

# schaleDB Name → wiki Portrait_ filename (without .png extension)
# Most work automatically by replacing spaces with underscores; these are the exceptions.
WIKI_NAME_MAP: dict[str, str] = {
    "Aris": "Arisu",
    "Shiroko (Cycling)": "Shiroko_(Riding)",
    "Shun (Small)": "Shun_(Kid)",
    "Neru (Bunny)": "Neru_(Bunny_Girl)",
    "Karin (Bunny)": "Karin_(Bunny_Girl)",
    "Asuna (Bunny)": "Asuna_(Bunny_Girl)",
    "Utaha (Cheer Squad)": "Utaha_(Cheerleader)",
    "Hibiki (Cheer Squad)": "Hibiki_(Cheerleader)",
    "Akane (Bunny)": "Akane_(Bunny_Girl)",
    "Aris (Maid)": "Arisu_(Maid)",
    "Toki (Bunny)": "Toki_(Bunny_Girl)",
    "Kotori (Cheer Squad)": "Kotori_(Cheerleader)",
    "Kotama (Camp)": "Kotama_(Camping)",
    "Hare (Camp)": "Hare_(Camping)",
    "Shiroko Terror": "Shiroko_(Terror)",
    # Track variants — no wiki portrait; fall back to SchaleDB
}


def _wiki_filename(student: dict) -> str:
    """Build the wiki Portrait_ filename (no extension) for a student."""
    name = student["Name"]
    mapped = WIKI_NAME_MAP.get(name)
    if mapped is not None:
        return mapped
    return name.replace(" ", "_")


def wiki_portrait_url(student: dict) -> str:
    """Compute the CDN URL for a student's wiki portrait (252x204 face image)."""
    filename = f"Portrait_{_wiki_filename(student)}.png"
    h = hashlib.md5(filename.encode()).hexdigest()
    return f"{WIKI_CDN}/{h[0]}/{h[:2]}/{urllib.parse.quote(filename)}"


def schale_portrait_url(student_id: int) -> str:
    """Full-body sprite URL (fallback if wiki portrait is unavailable)."""
    return f"{SCHALE_PORTRAIT}/{student_id}.webp"


class StudentDB:
    """In-memory student database loaded from the bundled SchaleDB JSON."""

    def __init__(self) -> None:
        self.students: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.by_name: dict[str, dict] = {}
        self.by_rarity: dict[int, list[dict]] = {1: [], 2: [], 3: []}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with open(BUNDLED_DB, "r", encoding="utf-8") as f:
            self.students = json.load(f)

        excluded = 0
        for s in self.students:
            sid = s["Id"]
            name = s["Name"].lower()
            rarity = s["StarGrade"]
            limit = s.get("IsLimited") or 0

            self.by_id[sid] = s
            self.by_name[name] = s

            # Exclude welfare/event characters (IsLimited >= 2) from pullable pool
            if limit >= 2:
                excluded += 1
                continue

            if rarity in self.by_rarity:
                self.by_rarity[rarity].append(s)

        # Pre-build standard pool (IsLimited == 0 only) and full pool
        self._std_pool: dict[int, list[dict]] = {1: [], 2: [], 3: []}
        self._limited_pool: list[dict] = []  # all IsLimited == 1 chars
        for s in self.students:
            limit = s.get("IsLimited") or 0
            if limit >= 2:
                continue
            r = s["StarGrade"]
            if limit == 0 and r in self._std_pool:
                self._std_pool[r].append(s)
            elif limit == 1:
                self._limited_pool.append(s)

        self._loaded = True
        log.info(
            "Loaded %d students (std: %d, limited: %d, welfare: %d)",
            len(self.students),
            sum(len(v) for v in self._std_pool.values()),
            len(self._limited_pool),
            excluded,
        )

    def get(self, student_id: int) -> Optional[dict]:
        return self.by_id.get(student_id)

    def get_by_name(self, name: str) -> Optional[dict]:
        return self.by_name.get(name.lower())

    def build_pool(self, banner: Optional[dict]) -> dict[int, list[dict]]:
        """Build the effective character pool for a banner.

        Returns {1: [...], 2: [...], 3: [...]} with characters pullable on this banner.

        - Regular Recruitment (banner=None): standard pool only
        - PickupGacha / LimitedGacha: standard pool + rate-up students
        - FesGacha: standard pool + all limiteds + rate-ups
        """
        pool: dict[int, list[dict]] = {
            r: list(self._std_pool[r]) for r in (1, 2, 3)
        }

        if banner is None:
            return pool

        gtype = banner.get("gachaType", "")
        rateup_names = [n.lower() for n in banner.get("rateups", [])]

        # For Fes banners, add ALL past limited characters to the pool
        if gtype == "FesGacha":
            for s in self._limited_pool:
                r = s["StarGrade"]
                if r in pool:
                    pool[r].append(s)

        # Add rate-up students (they might already be in the pool, but we need
        # to identify them for weighted selection later)
        for name in rateup_names:
            s = self.get_by_name(name)
            if s and (s.get("IsLimited") or 0) < 2:
                r = s["StarGrade"]
                if r in pool and s not in pool[r]:
                    pool[r].append(s)

        return pool

    def weighted_pick(self, pool: list[dict], rateup_names: list[str], rarity: int) -> dict:
        """Pick a student from a pool with rate-up weighting.

        For 3★ rarity, rate-up students each get ~23% weight (matching the
        real game's 0.7% per rate-up out of 3% total). For 1-2★, uniform.
        """
        if not pool:
            return random.choice(self.students)

        if rarity == 3 and rateup_names:
            # Weighted: rate-up chars get 3x weight each vs normal chars
            weights = []
            for s in pool:
                if s["Name"].lower() in rateup_names:
                    weights.append(3.0)
                else:
                    weights.append(1.0)
            return random.choices(pool, weights=weights, k=1)[0]

        return random.choice(pool)

    def random_by_rarity(self, rarity: int) -> dict:
        """Pick any student of given rarity from the full pullable pool (fallback)."""
        pool = self.by_rarity.get(rarity, [])
        return random.choice(pool) if pool else random.choice(self.students)

    def wiki_portrait(self, student: dict) -> str:
        """Wiki face-portrait CDN URL for a student dict (252x204)."""
        return wiki_portrait_url(student)

    def schale_portrait(self, student_id: int) -> str:
        """SchaleDB full-body sprite URL (fallback)."""
        return schale_portrait_url(student_id)


# Global singleton
db = StudentDB()


# --- Banner types & rates ---------------------------------------------------

# In-game rates per banner type
# Format: { gachaType: (rate_3star, rate_2star, rate_1star) }
# Fes banners double the 3-star rate to 6%
BANNER_RATES: dict[str, tuple[float, float, float]] = {
    "FesGacha": (0.06, 0.185, 0.755),
}
DEFAULT_RATES = (0.03, 0.185, 0.785)

# The 10th pull on a 10-pull: guarantee at least 2-star
# (97% 2-star, 3% 3-star — the 1-star rate is redirected into 2-star)
PULL10_RATES = (0.03, 0.97, 0.0)


def roll_rarity(rates: tuple[float, float, float]) -> int:
    """Roll rarity tier given (p_3star, p_2star, p_1star). Returns 3, 2, or 1."""
    r = random.random()
    if r < rates[0]:
        return 3
    elif r < rates[0] + rates[1]:
        return 2
    else:
        return 1


def get_rates_for_banner(banner: dict) -> tuple[float, float, float]:
    """Return (3star_rate, 2star_rate, 1star_rate) for a banner's gachaType."""
    return BANNER_RATES.get(banner.get("gachaType", ""), DEFAULT_RATES)


# --- Banner fetching ---------------------------------------------------------

async def fetch_banners(session: Optional[aiohttp.ClientSession] = None) -> dict:
    """Fetch live banner data from the BlueArchiveAPI.

    Returns the parsed JSON: {"current": [...], "upcoming": [...], "ended": [...]}
    """
    close_session = session is None
    if close_session:
        session = aiohttp.ClientSession()

    try:
        async with session.get(BANNER_API) as resp:
            if resp.status == 200:
                data = await resp.json()
                log.info("Fetched %d current, %d upcoming banners",
                         len(data.get("current", [])),
                         len(data.get("upcoming", [])))
                return data
            else:
                log.warning("Banner API returned status %d", resp.status)
                return {"current": [], "upcoming": [], "ended": []}
    except Exception:
        log.exception("Failed to fetch banners from API")
        return {"current": [], "upcoming": [], "ended": []}
    finally:
        if close_session:
            await session.close()


def format_banner_embed(banner: dict, index: int) -> str:
    """Format a single banner as an embed field value string."""
    gtype = banner.get("gachaType", "PickupGacha")
    rateups = ", ".join(banner.get("rateups", [])) or "Standard pool"
    started = banner.get("startedAt", 0)
    ended = banner.get("endedAt", 0)

    if started and ended:
        start_dt = datetime.fromtimestamp(started / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(ended / 1000, tz=timezone.utc)
        dates = f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d, %Y')}"
    else:
        dates = "Permanent"

    is_fes = " [FES - 6% 3*!]" if gtype == "FesGacha" else ""
    return (
        f"**#{index + 1} — {gtype}{is_fes}**\n"
        f"Rate-up: {rateups}\n"
        f"{dates}"
    )
