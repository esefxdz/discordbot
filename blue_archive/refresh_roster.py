"""Refresh the bundled Blue Archive roster from the live ennead.cc API.

Loads the current ba_students.json, merges any characters that have been
released since the last run, and atomically writes the result back. Run weekly
via the discordbot-refresh systemd timer so fresh clones stay current without
depending on the live API at startup.
"""
import asyncio
import json
from pathlib import Path

from blue_archive.data import db, fetch_banners

OUT = Path(__file__).with_name("ba_students.json")


async def main() -> None:
    db.load()
    banners = await fetch_banners()
    added = await db.merge_ennead(banners)

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(db.students, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)  # atomic swap so a reader never sees a partial file
    print(f"refreshed {OUT.name}: {len(db.students)} students (+{added} new)")


if __name__ == "__main__":
    asyncio.run(main())
