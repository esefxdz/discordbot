#this is the sqlite database layer for gacha inventory, spark, and eligma##
"""SQLite database for Blue Archive gacha persistence."""
######################################################################
import sqlite3
import logging
from pathlib import Path

from .constants import ELIGMA_YIELD

log = logging.getLogger(__name__)

DB_PATH = Path("data/ba_gacha.db")


def _connect() -> sqlite3.Connection:
    """Open a connection with WAL mode for concurrent reads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
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


# ── Inventory ──────────────────────────────────────────────────────────────

def add_pull(user_id: int, student_id: int, rarity: int) -> int:
    """Record a pull. If student already owned, increment count and return eligma earned."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT count FROM inventory WHERE user_id = ? AND student_id = ?",
            (user_id, student_id),
        )
        row = cur.fetchone()
        if row:
            # Duplicate — increment and earn eligma
            conn.execute(
                "UPDATE inventory SET count = count + 1 WHERE user_id = ? AND student_id = ?",
                (user_id, student_id),
            )
            eligma = ELIGMA_YIELD.get(rarity, 0)
            _add_eligma(conn, user_id, eligma)
            return eligma
        else:
            # New student
            conn.execute(
                "INSERT INTO inventory (user_id, student_id, count) VALUES (?, ?, 1)",
                (user_id, student_id),
            )
            return 0


def get_inventory(user_id: int) -> list[tuple[int, int]]:
    """Return [(student_id, count), ...] for a user."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT student_id, count FROM inventory WHERE user_id = ? ORDER BY student_id",
            (user_id,),
        ).fetchall()
    return rows


def get_inventory_stats(user_id: int) -> dict:
    """Return {unique_total, total_pulls, by_rarity: {1: n, 2: n, 3: n}}."""
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


# ── Spark ───────────────────────────────────────────────────────────────────

def get_spark(user_id: int, banner_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
    return row[0] if row else 0


def add_spark(user_id: int, banner_id: str, points: int) -> int:
    """Add points to a banner's spark counter. Returns new total."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO spark (user_id, banner_id, points) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, banner_id) DO UPDATE SET points = points + ?",
            (user_id, banner_id, points, points),
        )
        conn.commit()
        row = conn.execute(
            "SELECT points FROM spark WHERE user_id = ? AND banner_id = ?",
            (user_id, banner_id),
        ).fetchone()
    return row[0] if row else 0


def spend_spark(user_id: int, banner_id: str, cost: int = 200) -> bool:
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


# ── Eligma ──────────────────────────────────────────────────────────────────

def _add_eligma(conn: sqlite3.Connection, user_id: int, amount: int) -> None:
    conn.execute(
        "INSERT INTO eligma (user_id, amount) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?",
        (user_id, amount, amount),
    )


def get_eligma(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT amount FROM eligma WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0
