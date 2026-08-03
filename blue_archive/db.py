#this is the sqlite database layer for gacha inventory, spark, and eligma##
"""SQLite database for Blue Archive gacha persistence — async wrapper layer."""
######################################################################
import asyncio
import sqlite3
import logging
from pathlib import Path
from typing import Any

from .constants import ELIGMA_YIELD

log = logging.getLogger(__name__)

DB_PATH = Path("data/ba_gacha.db")


def _connect() -> sqlite3.Connection:
    """Open a connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(str(DB_PATH))
    return conn


def _init_db_sync() -> None:
    """Create tables if they don't exist (runs in thread)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id    INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                count      INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS spark (
                user_id   INTEGER NOT NULL,
                banner_id TEXT    NOT NULL,
                points    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, banner_id)
            );

            CREATE TABLE IF NOT EXISTS eligma (
                user_id INTEGER PRIMARY KEY,
                amount  INTEGER NOT NULL DEFAULT 0
            );
        """)
    log.info("Database initialised at %s", DB_PATH)


async def init_db() -> None:
    """Async wrapper — initialises the database off the event loop."""
    await asyncio.to_thread(_init_db_sync)


# ── Inventory ──────────────────────────────────────────────────────────────

def _add_pull_sync(user_id: int, student_id: int, rarity: int) -> int:
    """Record a pull. Returns eligma earned from duplicates."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT count FROM inventory WHERE user_id = ? AND student_id = ?",
            (user_id, student_id),
        )
        row = cur.fetchone()
        if row:
            conn.execute("UPDATE inventory SET count = count + 1 WHERE user_id = ? AND student_id = ?",
                         (user_id, student_id))
            eligma = ELIGMA_YIELD.get(rarity, 0)
            _add_eligma_sync(conn, user_id, eligma)
            return eligma
        else:
            conn.execute(
                "INSERT INTO inventory (user_id, student_id, count) VALUES (?, ?, 1)",
                (user_id, student_id),
            )
            return 0


async def add_pull(user_id: int, student_id: int, rarity: int) -> int:
    return await asyncio.to_thread(_add_pull_sync, user_id, student_id, rarity)


def _get_inventory_sync(user_id: int) -> list[tuple[int, int]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT student_id, count FROM inventory WHERE user_id = ? ORDER BY student_id",
            (user_id,),
        ).fetchall()
    return rows


async def get_inventory(user_id: int) -> list[tuple[int, int]]:
    return await asyncio.to_thread(_get_inventory_sync, user_id)


def _has_student_sync(user_id: int, student_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND student_id = ?",
            (user_id, student_id),
        ).fetchone()
    return row is not None


async def has_student(user_id: int, student_id: int) -> bool:
    return await asyncio.to_thread(_has_student_sync, user_id, student_id)


def _get_inventory_stats_sync(user_id: int) -> dict:
    with _connect() as conn:
        total_pulls = conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM inventory WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        unique = conn.execute(
            "SELECT COUNT(*) FROM inventory WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
    return {"total_pulls": total_pulls, "unique": unique}


async def get_inventory_stats(user_id: int) -> dict:
    return await asyncio.to_thread(_get_inventory_stats_sync, user_id)


# ── Spark ───────────────────────────────────────────────────────────────────

def _get_spark_sync(user_id: int, banner_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
    return row[0] if row else 0


async def get_spark(user_id: int, banner_id: str) -> int:
    return await asyncio.to_thread(_get_spark_sync, user_id, banner_id)


def _add_spark_sync(user_id: int, banner_id: str, points: int) -> int:
    """Add points to a banner's spark counter. Returns new total."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO spark (user_id, banner_id, points) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, banner_id) DO UPDATE SET points = points + ?",
            (user_id, banner_id, points, points),
        )
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
    return row[0] if row else 0


async def add_spark(user_id: int, banner_id: str, points: int) -> int:
    return await asyncio.to_thread(_add_spark_sync, user_id, banner_id, points)


def _spend_spark_sync(user_id: int, banner_id: str, cost: int = 200) -> bool:
    """Deduct spark points. Returns True if successful (had enough points)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
        if not row or row[0] < cost:
            return False
        conn.execute(
            "UPDATE spark SET points = points - ? WHERE user_id = ? AND banner_id = ?",
            (cost, user_id, banner_id),
        )
        return True


async def spend_spark(user_id: int, banner_id: str, cost: int = 200) -> bool:
    return await asyncio.to_thread(_spend_spark_sync, user_id, banner_id, cost)


def _spark_claim_sync(user_id: int, banner_id: str, student_id: int, rarity: int, cost: int = 200) -> tuple[bool, str]:
    """Atomically claim a spark student. Returns (success, message)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
        if not row or row[0] < cost:
            return False, "Not enough Recruitment Points."
        owned = conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND student_id = ?",
            (user_id, student_id),
        ).fetchone()
        if owned:
            return False, "already_owned"
        conn.execute("INSERT INTO inventory (user_id, student_id, count) VALUES (?, ?, 1)",
                     (user_id, student_id))
        conn.execute("UPDATE spark SET points = points - ? WHERE user_id = ? AND banner_id = ?",
                     (cost, user_id, banner_id))
        return True, ""


async def spark_claim(user_id: int, banner_id: str, student_id: int, rarity: int, cost: int = 200) -> tuple[bool, str]:
    return await asyncio.to_thread(_spark_claim_sync, user_id, banner_id, student_id, rarity, cost)


# ── Eligma ──────────────────────────────────────────────────────────────────

def _add_eligma_sync(conn: sqlite3.Connection, user_id: int, amount: int) -> None:
    conn.execute(
        "INSERT INTO eligma (user_id, amount) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?",
        (user_id, amount, amount),
    )


def _get_eligma_sync(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT amount FROM eligma WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


async def get_eligma(user_id: int) -> int:
    return await asyncio.to_thread(_get_eligma_sync, user_id)
