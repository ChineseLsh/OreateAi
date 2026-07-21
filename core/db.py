"""SQLite persistence for accounts, mailboxes, and videos."""

import json
import sqlite3
import time
import threading
from functools import wraps
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
_lock = threading.RLock()
_conn_cache: sqlite3.Connection | None = None


def _serialized(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)

    return wrapper


@_serialized
def _conn() -> sqlite3.Connection:
    global _conn_cache
    if _conn_cache is None:
        _conn_cache = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn_cache.row_factory = sqlite3.Row
        _conn_cache.execute("PRAGMA journal_mode=WAL")
    return _conn_cache


@_serialized
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
    CREATE TABLE IF NOT EXISTS mailboxes (
        address       TEXT PRIMARY KEY,
        password      TEXT NOT NULL,
        client_id     TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        type           TEXT DEFAULT 'ms_imap',
        status         TEXT DEFAULT 'available',
        created_at     INTEGER DEFAULT 0,
        updated_at     INTEGER DEFAULT 0
    );
    """)
    c.execute(
        "UPDATE mailboxes SET status='used', updated_at=? WHERE status='reserved'",
        (int(time.time()),),
    )
    c.commit()


@_serialized
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

@_serialized
def save_account(email: str, password: str, points: int, invite_code: str, cookies: dict | None = None):
    c = _conn()
    c.execute(
        """INSERT OR REPLACE INTO accounts (email, password, points, invite_code, cookies, status, created_at, last_used, locked)
           VALUES (?,?,?,?,?,?,?,0,0)""",
        (email, password, points, invite_code, json.dumps(cookies or {}), "active", int(time.time())),
    )
    c.commit()


@_serialized
def update_account_cookies(email: str, cookies: dict):
    c = _conn()
    c.execute("UPDATE accounts SET cookies=? WHERE email=?", (json.dumps(cookies), email))
    c.commit()


@_serialized
def update_account_points(email: str, points: int):
    c = _conn()
    c.execute("UPDATE accounts SET points=?, last_used=? WHERE email=?", (points, int(time.time()), email))
    c.commit()


@_serialized
def set_account_status(email: str, status: str):
    c = _conn()
    c.execute("UPDATE accounts SET status=? WHERE email=?", (status, email))
    c.commit()


@_serialized
def lock_account(email: str):
    c = _conn()
    c.execute("UPDATE accounts SET locked=1 WHERE email=?", (email,))
    c.commit()


@_serialized
def try_lock_account(email: str) -> bool:
    c = _conn()
    changed = c.execute(
        "UPDATE accounts SET locked=1 WHERE email=? AND locked=0",
        (email,),
    ).rowcount
    c.commit()
    return changed == 1


@_serialized
def unlock_account(email: str):
    c = _conn()
    c.execute("UPDATE accounts SET locked=0 WHERE email=?", (email,))
    c.commit()


@_serialized
def get_account(email: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


@_serialized
def get_best_account(min_points: int = 20) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM accounts WHERE status='active' AND locked=0 AND points>=? ORDER BY points DESC, last_used ASC LIMIT 1",
        (min_points,),
    ).fetchone()
    return dict(row) if row else None


@_serialized
def acquire_best_account(min_points: int = 20) -> dict | None:
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        row = c.execute(
            "SELECT * FROM accounts WHERE status='active' AND locked=0 AND points>=? "
            "ORDER BY points DESC, last_used ASC LIMIT 1",
            (min_points,),
        ).fetchone()
        if row is None:
            c.commit()
            return None
        changed = c.execute(
            "UPDATE accounts SET locked=1 WHERE email=? AND locked=0",
            (row["email"],),
        ).rowcount
        c.commit()
        return dict(row) if changed == 1 else None
    except Exception:
        c.rollback()
        raise


@_serialized
def list_accounts() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@_serialized
def count_active_accounts(min_points: int = 20) -> int:
    c = _conn()
    row = c.execute("SELECT COUNT(*) as n FROM accounts WHERE status='active' AND points>=?", (min_points,)).fetchone()
    return row["n"]


# --- Mailbox ops ---

@_serialized
def upsert_mailboxes(records: list[dict]) -> dict[str, int]:
    c = _conn()
    now = int(time.time())
    imported = 0
    updated = 0
    for record in records:
        address = str(record["address"]).strip().lower()
        existing = c.execute(
            "SELECT status, created_at FROM mailboxes WHERE address=?",
            (address,),
        ).fetchone()
        account_exists = c.execute(
            "SELECT 1 FROM accounts WHERE lower(email)=?",
            (address,),
        ).fetchone()
        if account_exists:
            status = "registered"
        elif existing and existing["status"] in {"reserved", "used", "registered"}:
            status = existing["status"]
        else:
            status = "available"
        created_at = existing["created_at"] if existing else now
        c.execute(
            """INSERT OR REPLACE INTO mailboxes
               (address, password, client_id, refresh_token, type, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                address,
                record["password"],
                record["client_id"],
                record["refresh_token"],
                "ms_imap",
                status,
                created_at,
                now,
            ),
        )
        if existing:
            updated += 1
        else:
            imported += 1
    c.commit()
    return {"imported": imported, "updated": updated}


@_serialized
def acquire_mailbox() -> dict | None:
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        row = c.execute(
            """SELECT m.* FROM mailboxes m
               LEFT JOIN accounts a ON lower(a.email)=m.address
               WHERE m.status='available' AND a.email IS NULL
               ORDER BY m.created_at ASC, m.address ASC LIMIT 1"""
        ).fetchone()
        if row is None:
            c.commit()
            return None
        changed = c.execute(
            "UPDATE mailboxes SET status='reserved', updated_at=? "
            "WHERE address=? AND status='available'",
            (int(time.time()), row["address"]),
        ).rowcount
        c.commit()
        return dict(row) if changed == 1 else None
    except Exception:
        c.rollback()
        raise


@_serialized
def get_mailbox(address: str) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM mailboxes WHERE address=?",
        (address.strip().lower(),),
    ).fetchone()
    return dict(row) if row else None


@_serialized
def list_mailboxes() -> list[dict]:
    c = _conn()
    rows = c.execute(
        """SELECT address, type, status, created_at, updated_at
           FROM mailboxes ORDER BY created_at DESC, address ASC"""
    ).fetchall()
    return [dict(row) for row in rows]


@_serialized
def finalize_mailbox(address: str) -> str:
    c = _conn()
    normalized = address.strip().lower()
    account_exists = c.execute(
        "SELECT 1 FROM accounts WHERE lower(email)=?",
        (normalized,),
    ).fetchone()
    status = "registered" if account_exists else "used"
    c.execute(
        "UPDATE mailboxes SET status=?, updated_at=? WHERE address=?",
        (status, int(time.time()), normalized),
    )
    c.commit()
    return status


@_serialized
def reset_mailbox(address: str) -> bool:
    c = _conn()
    normalized = address.strip().lower()
    account_exists = c.execute(
        "SELECT 1 FROM accounts WHERE lower(email)=?",
        (normalized,),
    ).fetchone()
    if account_exists:
        return False
    changed = c.execute(
        "UPDATE mailboxes SET status='available', updated_at=? "
        "WHERE address=? AND status='used'",
        (int(time.time()), normalized),
    ).rowcount
    c.commit()
    return changed == 1


@_serialized
def update_mailbox_refresh_token(address: str, refresh_token: str) -> None:
    c = _conn()
    c.execute(
        "UPDATE mailboxes SET refresh_token=?, updated_at=? WHERE address=?",
        (refresh_token, int(time.time()), address.strip().lower()),
    )
    c.commit()


# --- Video ops ---

@_serialized
def save_video(task_id: str, account_email: str, prompt: str, model: str, duration: int, ratio: str, status: str = "pending"):
    c = _conn()
    c.execute(
        """INSERT OR REPLACE INTO videos (task_id, account_email, prompt, model, duration, ratio, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, account_email, prompt, model, duration, ratio, status, int(time.time())),
    )
    c.commit()


@_serialized
def update_video(task_id: str, **fields):
    c = _conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [task_id]
    c.execute(f"UPDATE videos SET {sets} WHERE task_id=?", vals)
    c.commit()


@_serialized
def list_videos(limit: int = 50) -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@_serialized
def get_video(task_id: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM videos WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


# --- Stats ---

@_serialized
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
