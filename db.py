"""
db.py — SQLite database layer for piphone.
Single file, portable, no ORM dependency.
"""

import sqlite3, os, threading, time, uuid as _uuid

DB_FILE = os.path.join(os.path.dirname(__file__), "piphone.db")

_local = threading.local()


def _conn():
    """Return a thread-local connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Create tables if they don't exist."""
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id          TEXT PRIMARY KEY,
            first_name  TEXT NOT NULL,
            last_name   TEXT NOT NULL DEFAULT '',
            number      TEXT NOT NULL,
            email       TEXT NOT NULL DEFAULT '',
            color       TEXT NOT NULL DEFAULT '#1d4ed8'
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            number      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            text        TEXT NOT NULL,
            ts          TEXT NOT NULL,
            ts_epoch    INTEGER NOT NULL DEFAULT 0,
            status      TEXT NOT NULL DEFAULT '',
            sim_index   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_number
            ON messages(number);
        CREATE INDEX IF NOT EXISTS idx_messages_epoch
            ON messages(ts_epoch);

        CREATE TABLE IF NOT EXISTS calls (
            id          TEXT PRIMARY KEY,
            number      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            started_at  INTEGER NOT NULL DEFAULT 0,
            duration    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_calls_started
            ON calls(started_at DESC);
    """)
    c.commit()


# ── helpers ───────────────────────────────────────────────────────────────────

def _row_to_contact(row):
    first = row["first_name"]
    last  = row["last_name"]
    name  = (first + " " + last).strip()
    return {
        "id":         row["id"],
        "first_name": first,
        "last_name":  last,
        "name":       name,
        "number":     row["number"],
        "email":      row["email"],
        "color":      row["color"],
    }


# ── contacts ──────────────────────────────────────────────────────────────────

def contacts_all():
    rows = _conn().execute(
        "SELECT * FROM contacts ORDER BY first_name, last_name"
    ).fetchall()
    return [_row_to_contact(r) for r in rows]


def contacts_get(contact_id):
    row = _conn().execute(
        "SELECT * FROM contacts WHERE id=?", (contact_id,)
    ).fetchone()
    return _row_to_contact(row) if row else None


def contacts_find_by_number(number):
    row = _conn().execute(
        "SELECT * FROM contacts WHERE number=?", (number,)
    ).fetchone()
    return _row_to_contact(row) if row else None


def contacts_add(first_name, last_name, number, email="", color="#1d4ed8"):
    cid = str(_uuid.uuid4())
    _conn().execute(
        "INSERT INTO contacts(id,first_name,last_name,number,email,color)"
        " VALUES(?,?,?,?,?,?)",
        (cid, first_name.strip(), last_name.strip(),
         number, email.strip(), color)
    )
    _conn().commit()
    return contacts_get(cid)


def contacts_update(contact_id, first_name, last_name, number,
                    email="", color=None):
    c = contacts_get(contact_id)
    if not c:
        return None
    _conn().execute(
        "UPDATE contacts SET first_name=?,last_name=?,number=?,"
        "email=?,color=? WHERE id=?",
        (first_name.strip(), last_name.strip(), number,
         email.strip(), color or c["color"], contact_id)
    )
    _conn().commit()
    return contacts_get(contact_id)


def contacts_delete(contact_id):
    _conn().execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    _conn().commit()


# ── messages ──────────────────────────────────────────────────────────────────

def messages_get_inbox():
    """
    Return threads grouped by number, each with messages sorted by epoch.
    Shape mirrors the old JSON structure so the frontend needs no changes.
    """
    rows = _conn().execute(
        "SELECT * FROM messages ORDER BY ts_epoch ASC"
    ).fetchall()

    threads = {}
    for r in rows:
        num = r["number"]
        if num not in threads:
            threads[num] = {
                "id":        num,
                "thread_id": num,
                "number":    num,
                "name":      num,   # frontend resolves via Contacts
                "color":     "#1d4ed8",
                "messages":  [],
                "unread":    0,
            }
        msg = {
            "id":        r["id"],
            "dir":       r["direction"],
            "text":      r["text"],
            "ts":        r["ts"],
            "ts_epoch":  r["ts_epoch"],
            "status":    r["status"],
        }
        threads[num]["messages"].append(msg)
        if r["direction"] == "in" and r["status"] == "":
            threads[num]["unread"] += 1

    # sort threads by most recent message
    result = list(threads.values())
    result.sort(key=lambda t: t["messages"][-1]["ts_epoch"]
                if t["messages"] else 0, reverse=True)
    return result


def messages_add(number, direction, text, ts=None,
                 status="", sim_index=None):
    mid   = str(_uuid.uuid4())
    epoch = int(time.time())
    ts    = ts or time.strftime("%H:%M")
    _conn().execute(
        "INSERT INTO messages(id,number,direction,text,ts,ts_epoch,status,sim_index)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (mid, number, direction, text, ts, epoch, status, sim_index)
    )
    _conn().commit()
    return mid


def messages_update_status(msg_id, status):
    _conn().execute(
        "UPDATE messages SET status=? WHERE id=?", (status, msg_id)
    )
    _conn().commit()


def messages_exists(sim_index, number):
    """Check if a SIM-indexed message already exists (avoid duplicates)."""
    row = _conn().execute(
        "SELECT id FROM messages WHERE sim_index=? AND number=?",
        (sim_index, number)
    ).fetchone()
    return row is not None


def messages_delete(msg_id):
    _conn().execute("DELETE FROM messages WHERE id=?", (msg_id,))
    _conn().commit()


# ── calls ─────────────────────────────────────────────────────────────────────

def calls_add(number, direction, started_at=None, duration=0):
    cid = str(_uuid.uuid4())
    _conn().execute(
        "INSERT INTO calls(id,number,direction,started_at,duration)"
        " VALUES(?,?,?,?,?)",
        (cid, number, direction, started_at or int(time.time()), duration)
    )
    _conn().commit()
    return cid


def calls_update_duration(call_id, duration):
    _conn().execute(
        "UPDATE calls SET duration=? WHERE id=?", (duration, call_id)
    )
    _conn().commit()


def calls_get_recent(limit=50):
    rows = _conn().execute(
        "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
