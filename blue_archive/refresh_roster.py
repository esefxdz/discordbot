"""Rebuild the bundled Blue Archive roster from upstream sources.

Downloads the latest SchaleDB export (full fields including portrait data),
then merges the live ennead.cc character feed for anything SchaleDB doesn't
cover (variants, welfare, collabs). Writes the result atomically.

Fails WITHOUT writing if any required upstream fetch fails, so a transient
network error can never shrink the existing roster.

Run weekly via the discordbot-refresh systemd timer.
"""
import asyncio
import json
from pathlib import Path

import aiohttp

from blue_archive.constants import CHARACTER_API
from blue_archive.data import StudentDB, fetch_banners

SCHALEDB_URL = "https://raw.githubusercontent.com/SchaleDB/SchaleDB/main/data/en/students.min.json"
OUT = Path(__file__).with_name("ba_students.json")


async def fetch_json(session: aiohttp.ClientSession, url: str, timeout: int = 30) -> list[dict]:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        resp.raise_for_status()
        return json.loads(await resp.text())


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        base = await fetch_json(session, SCHALEDB_URL)
        chars = await fetch_json(session, CHARACTER_API)
        banners = await fetch_banners(session)

    if not chars:
        raise RuntimeError("ennead character API returned an empty list")

    db = StudentDB()
    db.load_data(base)
    added = db.merge_chars(chars, banners)

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(db.students, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)  # atomic swap so a reader never sees a partial file
    print(f"refreshed {OUT.name}: {len(db.students)} students "
          f"(SchaleDB {len(base)} + {added} ennead)")


if __name__ == "__main__":
    asyncio.run(main())
