"""SQLite 持久化 — accounts + videos"""

import json
import sqlite3
import time
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
_lock = threading.Lock()
_conn_cache: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _conn_cache
    if _conn_cache is None:
        _conn_cache = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn_cache.row_factory = sqlite3.Row
        _conn_cache.execute("PRAGMA journal_mode=WAL")
    return _conn_cache


def init_db():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts (
        email       TEXT PRIMARY KEY,
        password    TEXT NOT NULL,
        points      INTEGER DEFAULT 0,
        invite_code TEXT DEFAULT '',
        cookies     TEXT DEFAULT '{}',
        status      TEXT DEFAULT 'active',
        created_at  INTEGER DEFAULT 0,
        last_used   INTEGER DEFAULT 0,
        locked      INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS videos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     TEXT UNIQUE,
        account_email TEXT,
        prompt      TEXT,
        model       TEXT DEFAULT 'Seedance 2.0 Mini',
        duration    INTEGER DEFAULT 5,
        ratio       TEXT DEFAULT '16:9',
        video_url   TEXT DEFAULT '',
        local_path  TEXT DEFAULT '',
        log_id      TEXT DEFAULT '',
        points_cost INTEGER DEFAULT 20,
        status      TEXT DEFAULT 'pending',
        created_at  INTEGER DEFAULT 0,
        FOREIGN KEY (account_email) REFERENCES accounts(email)
    );
    """)
    c.commit()


def migrate_jsonl():
    jsonl = Path(__file__).resolve().parent.parent / "accounts.jsonl"
    if not jsonl.exists():
        return 0
    c = _conn()
    count = 0
    for line in jsonl.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        a = json.loads(line)
        try:
            c.execute(
                "INSERT OR IGNORE INTO accounts (email, password, points, invite_code, cookies, status, created_at) VALUES (?,?,?,?,?,?,?)",
                (a["email"], a["password"], a.get("points", 0), a.get("invite_code", ""), "{}", "active", a.get("ts", int(time.time()))),
            )
            count += 1
        except Exception:
            pass
    c.commit()
    return count


# --- Account ops ---

def save_account(email: str, password: str, points: int, invite_code: str, cookies: dict | None = None):
    c = _conn()
    with _lock:
        c.execute(
        """INSERT OR REPLACE INTO accounts (email, password, points, invite_code, cookies, status, created_at, last_used, locked)
           VALUES (?,?,?,?,?,?,?,0,0)""",
        (email, password, points, invite_code, json.dumps(cookies or {}), "active", int(time.time())),
    )
    c.commit()


def update_account_cookies(email: str, cookies: dict):
    c = _conn()
    c.execute("UPDATE accounts SET cookies=? WHERE email=?", (json.dumps(cookies), email))
    c.commit()


def update_account_points(email: str, points: int):
    c = _conn()
    c.execute("UPDATE accounts SET points=?, last_used=? WHERE email=?", (points, int(time.time()), email))
    c.commit()


def set_account_status(email: str, status: str):
    c = _conn()
    c.execute("UPDATE accounts SET status=? WHERE email=?", (status, email))
    c.commit()


def lock_account(email: str):
    c = _conn()
    c.execute("UPDATE accounts SET locked=1 WHERE email=?", (email,))
    c.commit()


def unlock_account(email: str):
    c = _conn()
    c.execute("UPDATE accounts SET locked=0 WHERE email=?", (email,))
    c.commit()


def get_account(email: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


def get_best_account(min_points: int = 20) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM accounts WHERE status='active' AND locked=0 AND points>=? ORDER BY points DESC, last_used ASC LIMIT 1",
        (min_points,),
    ).fetchone()
    return dict(row) if row else None


def list_accounts() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def count_active_accounts(min_points: int = 20) -> int:
    c = _conn()
    row = c.execute("SELECT COUNT(*) as n FROM accounts WHERE status='active' AND points>=?", (min_points,)).fetchone()
    return row["n"]


# --- Video ops ---

def save_video(task_id: str, account_email: str, prompt: str, model: str, duration: int, ratio: str, status: str = "pending"):
    c = _conn()
    c.execute(
        """INSERT OR REPLACE INTO videos (task_id, account_email, prompt, model, duration, ratio, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, account_email, prompt, model, duration, ratio, status, int(time.time())),
    )
    c.commit()


def update_video(task_id: str, **fields):
    c = _conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [task_id]
    c.execute(f"UPDATE videos SET {sets} WHERE task_id=?", vals)
    c.commit()


def list_videos(limit: int = 50) -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_video(task_id: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM videos WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


# --- Stats ---

def get_stats() -> dict:
    c = _conn()
    accs = c.execute("SELECT COUNT(*) as n, COALESCE(SUM(points),0) as pts FROM accounts").fetchone()
    vids = c.execute("SELECT COUNT(*) as n FROM videos WHERE status='done'").fetchone()
    running = c.execute("SELECT COUNT(*) as n FROM videos WHERE status IN ('pending','generating')").fetchone()
    return {
        "accounts": accs["n"],
        "total_points": accs["pts"],
        "videos": vids["n"],
        "running_tasks": running["n"],
    }
