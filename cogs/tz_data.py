"""Shared timezone data — country → UTC offset, fuzzy lookup, autocomplete.
######################################################################
Used by the calendar, /timestamp, and timestamp-friends cogs.
"""
######################################################################

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
