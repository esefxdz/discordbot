"""Gensokyo Radio API — polls their REST endpoint for rich now-playing data."""
import asyncio
import logging
from typing import Any

import aiohttp
import discord

from ..shared.constants import C_RADIO

log = logging.getLogger(__name__)

_API_URL = "https://gensokyoradio.net/api/station/playing/"
_ART_BASE = "https://gensokyoradio.net/images/albums/500"
_POLL_SECS = 15


async def poll(cog: Any, guild_id: int, station_name: str) -> None:
    """Poll Gensokyo Radio's API and post embeds on track change.

    ``cog`` is the Music cog instance (needed for bot, _radio_channels, etc.).
    Stores itself in ``cog._icy_tasks`` so ``_cancel_icy_poller`` handles cleanup.
    """
    last_song_id = None
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"User-Agent": "yuuka-radio/1.0"},
    ) as session:
        while True:
            try:
                async with session.get(_API_URL) as resp:
                    if resp.status != 200:
                        log.warning("Gensokyo API returned %d", resp.status)
                        await asyncio.sleep(_POLL_SECS)
                        continue
                    data = await resp.json()

                song_id = data["SONGDATA"]["SONGID"]
                if song_id == last_song_id:
                    await asyncio.sleep(_POLL_SECS)
                    continue
                last_song_id = song_id

                await _send_now_playing(cog, guild_id, station_name, data)

            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("Gensokyo API poller error", exc_info=True)

            await asyncio.sleep(_POLL_SECS)


async def _send_now_playing(
    cog: Any, guild_id: int, station_name: str, data: dict
) -> None:
    """Build and post a rich embed from Gensokyo Radio API JSON."""
    channel_id = cog._radio_channels.get(guild_id)
    if channel_id is None:
        return
    guild = cog.bot.get_guild(guild_id)
    if guild is None:
        return
    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        return

    song = data.get("SONGINFO", {})
    times = data.get("SONGTIMES", {})
    sdata = data.get("SONGDATA", {})
    misc = data.get("MISC", {})
    server = data.get("SERVERINFO", {})

    title = song.get("TITLE", "Unknown")
    artist = song.get("ARTIST", "")
    album = song.get("ALBUM", "")
    year = song.get("YEAR", "")
    circle = song.get("CIRCLE", "")
    duration = times.get("DURATION", 0)
    rating = sdata.get("RATING")
    times_rated = sdata.get("TIMESRATED", 0)
    listeners = server.get("LISTENERS", 0)

    # Bitrate — prefer highest quality stream
    streams = server.get("STREAMS", {})
    bitrate = ""
    for sid in ("3", "2", "1"):
        s = streams.get(sid, {})
        if s.get("BITRATE"):
            bitrate = str(s["BITRATE"])
            break

    # Album art
    album_art = misc.get("ALBUMART", "")
    album_art_url = f"{_ART_BASE}/{album_art}" if album_art else None

    # Build embed
    desc_parts = []
    if artist:
        desc_parts.append(f"**{artist}**")
    if album:
        album_str = album
        if year:
            album_str += f" ({year})"
        desc_parts.append(album_str)

    embed = discord.Embed(
        title=f"🎵 {title}",
        description=" · ".join(desc_parts) if desc_parts else None,
        color=C_RADIO,
    )

    if circle:
        embed.add_field(name="Circle", value=circle, inline=True)

    if duration > 0:
        mins, secs = divmod(duration, 60)
        embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)

    if rating:
        embed.add_field(
            name="Rating",
            value=f"{rating} ({times_rated} votes)",
            inline=True,
        )

    if bitrate or listeners:
        parts = []
        if bitrate:
            parts.append(f"{bitrate} kbps")
        if listeners:
            parts.append(f"{listeners} listeners")
        embed.add_field(name="Stream", value=" · ".join(parts), inline=True)

    if album_art_url:
        embed.set_thumbnail(url=album_art_url)

    embed.set_footer(text=f"now on {station_name} · Gensokyo Radio")

    try:
        await channel.send(embed=embed)
    except Exception:
        log.debug(
            "Failed to send Gensokyo now-playing in guild %d", guild_id, exc_info=True
        )
