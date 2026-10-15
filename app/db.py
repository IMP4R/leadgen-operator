import json
import sqlite3
from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id TEXT UNIQUE,
    username TEXT,
    name TEXT,
    company TEXT,
    status TEXT DEFAULT 'new',
    drop_reason TEXT,
    mode TEXT DEFAULT 'auto',
    qualification TEXT,
    source TEXT DEFAULT 'tg',
    refusals INTEGER DEFAULT 0,
    dialog_closed INTEGER DEFAULT 0,
    closed_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lead_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    direction TEXT CHECK(direction IN ('in','out','draft')),
    text TEXT,
    variants TEXT,
    qualification TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pg_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pg_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES pg_sessions(id),
    role TEXT CHECK(role IN ('lead','operator')),
    text TEXT,
    variants TEXT,
    qualification TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

GLOBAL_MODE_KEY = "global_mode"


def get_global_mode(conn=None):
    """Return the workspace-wide operator mode: auto or manual."""
    if conn is not None:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (GLOBAL_MODE_KEY,)
        ).fetchone()
        return row["value"] if row and row["value"] in {"auto", "manual"} else "auto"
    with get_conn() as own_conn:
        return get_global_mode(own_conn)


def set_global_mode(mode):
    """Persist the workspace-wide mode and sync the legacy per-lead column."""
    if mode not in {"auto", "manual"}:
        raise ValueError("bad global mode")
    legacy_mode = "auto" if mode == "auto" else "suggest"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (GLOBAL_MODE_KEY, mode),
        )
        conn.execute("UPDATE leads SET mode = ?", (legacy_mode,))
    return mode


def upsert_tg_lead(conn, tg_id, username=None, name=None,
                   new_status="new", force_status=None):
    """Find/create a TG lead by numeric ID and username, merging legacy duplicates."""
    tg_key = str(tg_id)
    username = (username or "").strip().lstrip("@") or None
    clauses = ["CAST(tg_user_id AS TEXT) = ?"]
    params = [tg_key]
    if username:
        clauses.extend([
            "lower(COALESCE(username, '')) = lower(?)",
            "lower(CAST(tg_user_id AS TEXT)) = lower(?)",
        ])
        params.extend([username, username])
    rows = conn.execute(
        "SELECT * FROM leads WHERE COALESCE(source, 'tg') = 'tg' AND (" +
        " OR ".join(clauses) + ")",
        params,
    ).fetchall()

    if not rows:
        cur = conn.execute(
            "INSERT INTO leads (tg_user_id, username, name, status, source) "
            "VALUES (?,?,?,?, 'tg')",
            (tg_key, username, name or username or tg_key, new_status),
        )
        return dict(conn.execute("SELECT * FROM leads WHERE id = ?", (cur.lastrowid,)).fetchone()), True

    status_rank = {"in_work": 0, "new": 1, "paused": 2, "call_booked": 3, "dropped": 4}
    primary = min(rows, key=lambda row: (status_rank.get(row["status"], 9), row["id"]))
    primary_id = primary["id"]
    activities = [row["last_activity_at"] for row in rows if row["last_activity_at"]]
    latest_activity = max(activities) if activities else primary["last_activity_at"]

    for duplicate in rows:
        if duplicate["id"] == primary_id:
            continue
        conn.execute("UPDATE lead_messages SET lead_id = ? WHERE lead_id = ?",
                     (primary_id, duplicate["id"]))
        conn.execute("DELETE FROM leads WHERE id = ?", (duplicate["id"],))

    resolved_username = username or primary["username"]
    resolved_name = name or primary["name"] or resolved_username or tg_key
    resolved_status = force_status or primary["status"] or new_status
    conn.execute(
        "UPDATE leads SET tg_user_id = ?, username = ?, name = ?, status = ?, "
        "source = 'tg', last_activity_at = ? WHERE id = ?",
        (tg_key, resolved_username, resolved_name, resolved_status,
         latest_activity, primary_id),
    )
    return dict(conn.execute("SELECT * FROM leads WHERE id = ?", (primary_id,)).fetchone()), False


def set_dialog_state(conn, lead_id, refusals=None, closed=None, reason=None):
    """Persist two-refusal counter and closed state for one lead."""
    fields, params = [], []
    if refusals is not None:
        fields.append("refusals = ?")
        params.append(int(refusals))
    if closed is not None:
        fields.append("dialog_closed = ?")
        params.append(1 if closed else 0)
        fields.append("closed_reason = ?")
        params.append(reason if closed else None)
    if not fields:
        return
    params.append(lead_id)
    conn.execute("UPDATE leads SET " + ", ".join(fields) + " WHERE id = ?", params)


def reopen_dialog(conn, lead_id, reset_refusals=False):
    """Resume auto-replies for a closed dialog."""
    set_dialog_state(
        conn, lead_id,
        refusals=0 if reset_refusals else None,
        closed=False,
    )


MIGRATIONS = {
    "drop_reason": "ALTER TABLE leads ADD COLUMN drop_reason TEXT",
    "mode": "ALTER TABLE leads ADD COLUMN mode TEXT DEFAULT 'auto'",
    "qualification": "ALTER TABLE leads ADD COLUMN qualification TEXT",
    "last_activity_at": "ALTER TABLE leads ADD COLUMN last_activity_at TEXT",
    "account_id": "ALTER TABLE leads ADD COLUMN account_id INTEGER",
    "source": "ALTER TABLE leads ADD COLUMN source TEXT DEFAULT 'tg'",
    "refusals": "ALTER TABLE leads ADD COLUMN refusals INTEGER DEFAULT 0",
    "dialog_closed": "ALTER TABLE leads ADD COLUMN dialog_closed INTEGER DEFAULT 0",
    "closed_reason": "ALTER TABLE leads ADD COLUMN closed_reason TEXT",
}

def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        for col, ddl in MIGRATIONS.items():
            if col not in cols:
                conn.execute(ddl)
        inserted = conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, 'auto')",
            (GLOBAL_MODE_KEY,),
        ).rowcount
        if inserted:
            conn.execute("UPDATE leads SET mode = 'auto'")
