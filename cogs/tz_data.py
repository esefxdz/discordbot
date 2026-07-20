"""Shared timezone data — country → UTC offset, fuzzy lookup, autocomplete.
######################################################################
Used by the calendar, /timestamp, and timestamp-friends cogs.
"""
import re
from datetime import datetime, timezone, timedelta

######################################################################

# ---------------------------------------------------------------------------
# AM/PM detection — used to avoid overriding explicit meridian indicators
# ---------------------------------------------------------------------------
_AMPM_RE = re.compile(
    r"(?:\b|(?<=\d))(?:am|a\.m\.?|pm|p\.m\.?)\b", re.IGNORECASE
)

def has_ampm(text: str) -> bool:
    """Return True if *text* contains an explicit am/pm indicator."""
    return bool(_AMPM_RE.search(text))


# ---------------------------------------------------------------------------
# bare hour handling — two regexes: one for normalize, one for fallback
# ---------------------------------------------------------------------------

# Matches a bare hour number (1-24) NOT preceded by colon (excludes minutes
# in "3:00") and NOT followed by colon (excludes "3:00" hours).  Captures
# an optional am/pm suffix separately.
_HOUR_RE = re.compile(
    r"(?<!:)\b(\d{1,2})(?!\d)(?!\s*:)(?:\s*(am|a\.m\.?|pm|p\.m\.?))?",
    re.IGNORECASE,
)

def normalize_bare_hours(text: str) -> str:
    """Insert ``:00`` before optional am/pm so dateparser reads times correctly.

    ``sunday at 6`` → ``sunday at 6:00``
    ``sunday at 6 am`` → ``sunday at 6:00 am``
    ``3pm`` → ``3:00 pm``
    """
    def _repl(m: re.Match) -> str:
        h = m.group(1)
        mer = m.group(2)
        if mer:
            return f"{h}:00 {mer}"
        return f"{h}:00"
    return _HOUR_RE.sub(_repl, text)


def parse_bare_hours(text: str, offset: float) -> list[int]:
    """Find hour patterns in *text*, convert to UTC unix timestamps.

    Handles bare hours (``3``, ``at 11``), ``3pm``, ``3 am``, ``14``, etc.
    No-AM/PM hours 1-11 default to PM; if past, falls back to AM before
    pushing to tomorrow.
    """
    raw: list[tuple[str, str | None]] = _HOUR_RE.findall(text)
    if not raw:
        return []

    now_local = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=offset))
    )
    results: list[int] = []
    seen: set[int] = set()

    for hour_str, meridian in raw:
        h = int(hour_str)
        if h < 1 or h > 24:
            continue

        # Determine AM/PM from meridian or default
        if meridian:
            is_pm = meridian.lower().startswith("p")
            if is_pm and 1 <= h <= 11:
                h += 12
            elif not is_pm and h == 12:
                h = 0
        elif 1 <= h <= 11:
            # No meridian — try PM first, fall back to AM if past
            h_pm = h + 12
            dt_pm = now_local.replace(hour=h_pm, minute=0, second=0, microsecond=0)
            if dt_pm > now_local:
                h = h_pm
            else:
                dt_am = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
                if dt_am <= now_local:
                    pass  # both past, keep AM, push tomorrow below
        elif h == 24:
            h = 0

        dt = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
        if dt <= now_local:
            dt += timedelta(days=1)
        unix_ts = int(dt.astimezone(timezone.utc).timestamp())
        if unix_ts not in seen:
            seen.add(unix_ts)
            results.append(unix_ts)

    return results


# ---------------------------------------------------------------------------
# country offset data
# ---------------------------------------------------------------------------

# fmt: off
COUNTRY_TZ: dict[str, float] = {
    # Europe
    "Turkey":         3,      "UK":              0,
    "Germany":        2,      "France":          2,
    "Netherlands":    2,      "Belgium":         2,
    "Poland":         2,      "Italy":           2,
    "Spain":          2,      "Sweden":          2,
    "Norway":         2,      "Denmark":         2,
    "Switzerland":    2,      "Austria":         2,
    "Czechia":        2,      "Czech Republic":  2,
    "Czech":          2,      "Greece":          3,
    "Finland":        3,      "Romania":         3,
    "Ukraine":        3,      "Portugal":        1,
    "Ireland":        1,      "Iceland":         0,

    # Americas
    "US East":       -5,      "US Central":     -6,
    "US Mountain":   -7,      "US West":        -8,
    "US Alaska":     -9,      "US Hawaii":     -10,
    "Canada East":   -5,      "Canada Central": -6,
    "Canada West":   -8,      "Mexico":         -6,
    "Brazil":        -3,      "Argentina":      -3,
    "Colombia":      -5,      "Chile":          -4,

    # Asia / Pacific
    "Japan":          9,      "Korea":           9,
    "China":          8,      "Taiwan":          8,
    "Hong Kong":      8,      "Singapore":       8,
    "Malaysia":       8,      "Philippines":     8,
    "Indonesia West": 7,      "Thailand":        7,
    "Vietnam":        7,      "India":           5.5,
    "Pakistan":       5,      "Bangladesh":      6,
    "Iran":           3.5,    "Israel":          3,
    "Saudi Arabia":   3,      "UAE":             4,

    # Oceania
    "Australia East": 11,     "Australia Central": 10.5,
    "Australia West":  8,     "New Zealand":      13,

    # Africa
    "South Africa":    2,     "Egypt":            3,
    "Nigeria":         1,     "Kenya":            3,
    "Morocco":         1,
}
# fmt: on


def find_offset(country: str) -> float | None:
    """Look up a country's UTC offset. Case-insensitive fuzzy match."""
    c = country.strip().lower()
    if not c:
        return None
    for name, offset in COUNTRY_TZ.items():
        if c == name.lower():
            return offset
    for name, offset in COUNTRY_TZ.items():
        if name.lower().startswith(c):
            return offset
    for name, offset in COUNTRY_TZ.items():
        if c in name.lower():
            return offset
    return None


async def country_autocomplete(
    _interaction,
    current: str,
) -> list:
    """Return matching countries for slash-command autocomplete."""
    from discord import app_commands
    current = current.lower()
    matches = [
        app_commands.Choice(name=c, value=c)
        for c in sorted(COUNTRY_TZ)
        if current in c.lower()
    ]
    return matches[:25]  # Discord limits to 25 suggestions
