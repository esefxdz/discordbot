#msgmap, avatar cache and sticker cache for the telegram bridge##
"""SQLite persistence for the Telegram bridge — async wrapper layer."""
######################################################################
import asyncio
import sqlite3
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent
DB_PATH = _PKG_DIR.parent / "data" / "tgbridge.db"

MSGMAP_TTL = 60 * 60 * 24 * 30      # 30 days, then the row is pruned
AVATAR_URL_TTL = 60 * 60 * 12       # 12h — re-upload before the cdn link dies
AVATAR_RECHECK = 60 * 60 * 6        # 6h — re-ask telegram if the photo changed


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH), timeout=10)


def _init_db_sync() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS msgmap (
                tg_chat_id     INTEGER NOT NULL,
                tg_msg_id      INTEGER NOT NULL,
                dc_channel_id  INTEGER NOT NULL,
                dc_msg_id      INTEGER NOT NULL,
                origin         TEXT    NOT NULL,
                created_at     INTEGER NOT NULL,
                PRIMARY KEY (tg_chat_id, tg_msg_id)
            );
            CREATE INDEX IF NOT EXISTS idx_msgmap_dc
                ON msgmap (dc_channel_id, dc_msg_id);
            CREATE INDEX IF NOT EXISTS idx_msgmap_age
                ON msgmap (created_at);

            CREATE TABLE IF NOT EXISTS avatar_cache (
                tg_user_id     INTEGER PRIMARY KEY,
                file_unique_id TEXT,
                image          BLOB,
                cdn_url        TEXT,
                uploaded_at    INTEGER NOT NULL DEFAULT 0,
                checked_at     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS media_cache (
                file_unique_id TEXT PRIMARY KEY,
                filename       TEXT NOT NULL,
                data           BLOB NOT NULL,
                created_at     INTEGER NOT NULL
            );
        """)
    log.info("tgbridge database initialised at %s", DB_PATH)


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


# ── message id mapping ─────────────────────────────────────────────────────
# many-to-one: an album's N telegram ids all point at one discord message

def _link_sync(tg_chat_id, tg_msg_ids, dc_channel_id, dc_msg_id, origin):
    now = int(time.time())
    rows = [(tg_chat_id, mid, dc_channel_id, dc_msg_id, origin, now)
            for mid in tg_msg_ids]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO msgmap "
            "(tg_chat_id, tg_msg_id, dc_channel_id, dc_msg_id, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", rows)


async def link(tg_chat_id: int, tg_msg_ids, dc_channel_id: int,
               dc_msg_id: int, origin: str) -> None:
    """Record that these Telegram message(s) correspond to one Discord message."""
    if isinstance(tg_msg_ids, int):
        tg_msg_ids = [tg_msg_ids]
    tg_msg_ids = [m for m in tg_msg_ids if m is not None]
    if not tg_msg_ids:
        return
    await asyncio.to_thread(_link_sync, tg_chat_id, tg_msg_ids,
                            dc_channel_id, dc_msg_id, origin)


def _dc_for_tg_sync(tg_chat_id, tg_msg_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT dc_channel_id, dc_msg_id FROM msgmap "
            "WHERE tg_chat_id = ? AND tg_msg_id = ?",
            (tg_chat_id, tg_msg_id)).fetchone()
    return tuple(row) if row else None


async def dc_for_tg(tg_chat_id: int, tg_msg_id: int):
    """-> (discord_channel_id, discord_message_id) or None."""
    return await asyncio.to_thread(_dc_for_tg_sync, tg_chat_id, tg_msg_id)


def _tg_for_dc_sync(dc_channel_id, dc_msg_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT tg_chat_id, tg_msg_id FROM msgmap "
            "WHERE dc_channel_id = ? AND dc_msg_id = ? "
            "ORDER BY tg_msg_id ASC LIMIT 1",
            (dc_channel_id, dc_msg_id)).fetchone()
    return tuple(row) if row else None


async def tg_for_dc(dc_channel_id: int, dc_msg_id: int):
    """-> (telegram_chat_id, telegram_message_id) or None."""
    return await asyncio.to_thread(_tg_for_dc_sync, dc_channel_id, dc_msg_id)


def _prune_sync():
    cutoff = int(time.time()) - MSGMAP_TTL
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM msgmap WHERE created_at < ?", (cutoff,)).rowcount


async def prune() -> int:
    return await asyncio.to_thread(_prune_sync)


# ── avatar cache ───────────────────────────────────────────────────────────

def _get_avatar_sync(tg_user_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT file_unique_id, image, cdn_url, uploaded_at, checked_at "
            "FROM avatar_cache WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
    if not row:
        return None
    return {"file_unique_id": row[0],
            "image": bytes(row[1]) if row[1] else None,
            "cdn_url": row[2], "uploaded_at": row[3], "checked_at": row[4]}


async def get_avatar(tg_user_id: int):
    return await asyncio.to_thread(_get_avatar_sync, tg_user_id)


def _put_avatar_sync(tg_user_id, file_unique_id, image, cdn_url,
                     uploaded_at, checked_at):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO avatar_cache "
            "(tg_user_id, file_unique_id, image, cdn_url, uploaded_at, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tg_user_id, file_unique_id,
             sqlite3.Binary(image) if image else None,
             cdn_url, uploaded_at, checked_at))


async def put_avatar(tg_user_id: int, file_unique_id, image, cdn_url,
                     uploaded_at: int, checked_at: int) -> None:
    await asyncio.to_thread(_put_avatar_sync, tg_user_id, file_unique_id,
                            image, cdn_url, uploaded_at, checked_at)


def _touch_avatar_sync(tg_user_id, checked_at):
    with _connect() as conn:
        conn.execute("UPDATE avatar_cache SET checked_at = ? WHERE tg_user_id = ?",
                     (checked_at, tg_user_id))


async def touch_avatar(tg_user_id: int, checked_at: int) -> None:
    """Record that we asked Telegram and the photo had not changed."""
    await asyncio.to_thread(_touch_avatar_sync, tg_user_id, checked_at)


def _count_avatars_sync():
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM avatar_cache").fetchone()[0]


async def count_avatars() -> int:
    return await asyncio.to_thread(_count_avatars_sync)


# ── converted media cache ──────────────────────────────────────────────────

def _get_media_sync(file_unique_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT filename, data FROM media_cache WHERE file_unique_id = ?",
            (file_unique_id,)).fetchone()
    return (row[0], bytes(row[1])) if row else None


async def get_media(file_unique_id: str):
    """-> (filename, data) for a previously converted sticker, or None."""
    return await asyncio.to_thread(_get_media_sync, file_unique_id)


def _put_media_sync(file_unique_id, filename, data):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO media_cache "
            "(file_unique_id, filename, data, created_at) VALUES (?, ?, ?, ?)",
            (file_unique_id, filename, sqlite3.Binary(data), int(time.time())))


async def put_media(file_unique_id: str, filename: str, data: bytes) -> None:
    await asyncio.to_thread(_put_media_sync, file_unique_id, filename, data)


def _count_media_sync():
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM media_cache").fetchone()[0]


async def count_media() -> int:
    return await asyncio.to_thread(_count_media_sync)
