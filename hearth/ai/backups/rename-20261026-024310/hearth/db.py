"""SQLite storage shared by every part of the app."""

import json
import os
import sqlite3
import threading
import time
import uuid

from .config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "hearth.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT, created REAL, updated REAL,
    pinned INTEGER DEFAULT 0, skill TEXT DEFAULT '', model TEXT DEFAULT '', origin TEXT DEFAULT 'chat'
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT, data TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, created);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT DEFAULT 'fact', created REAL, used REAL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, due REAL, repeat TEXT DEFAULT '',
    done INTEGER DEFAULT 0, created REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, start REAL, end REAL, location TEXT DEFAULT '',
    notes TEXT DEFAULT '', remind INTEGER DEFAULT 10, notified INTEGER DEFAULT 0, created REAL
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT, data TEXT, created REAL, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_records_coll ON records(collection);
CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, prompt TEXT, schedule TEXT, enabled INTEGER DEFAULT 1,
    skill TEXT DEFAULT '', notify INTEGER DEFAULT 1, key TEXT, last_run REAL, next_run REAL,
    last_status TEXT DEFAULT '', conv_id TEXT DEFAULT '', created REAL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, title TEXT, status TEXT, created REAL, updated REAL, conv_id TEXT,
    run_id TEXT, origin TEXT DEFAULT 'task', result TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT DEFAULT 'custom', icon TEXT DEFAULT 'agent', description TEXT DEFAULT '',
    instructions TEXT DEFAULT '', groups TEXT DEFAULT '[]', skill TEXT DEFAULT 'workflow', model TEXT DEFAULT '',
    auto_approve INTEGER DEFAULT 0, max_steps INTEGER DEFAULT 40, schedule TEXT DEFAULT '', action TEXT DEFAULT 'run',
    task TEXT DEFAULT '', enabled INTEGER DEFAULT 1, next_run REAL, last_run REAL, last_status TEXT DEFAULT '',
    conv_id TEXT DEFAULT '', created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS kb_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, path TEXT, added REAL, chunks INTEGER DEFAULT 0,
    embedded INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, ord INTEGER, text TEXT, emb BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(doc_id);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT, level TEXT DEFAULT 'info',
    created REAL, read INTEGER DEFAULT 0, link TEXT DEFAULT ''
);
"""

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")
_conn.executescript(SCHEMA)

# columns added after the first release
for _table, _column in (("tasks", "agent_id TEXT DEFAULT ''"), ("tasks", "parent_conv TEXT DEFAULT ''"), ("tasks", "goal TEXT DEFAULT ''"),
                        ("tasks", "oc_session TEXT DEFAULT ''"), ("tasks", "project TEXT DEFAULT ''"), ("tasks", "model TEXT DEFAULT ''")):
    try:
        _conn.execute("ALTER TABLE %s ADD COLUMN %s" % (_table, _column))
    except sqlite3.OperationalError:
        pass  # already there


def q(sql, params=()):
    with _lock:
        return [dict(row) for row in _conn.execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        return cur.lastrowid if cur.lastrowid else cur.rowcount


def new_id():
    return uuid.uuid4().hex[:16]


# ---------- conversations ----------

def create_conversation(title="New conversation", skill="", origin="chat"):
    cid = new_id()
    now = time.time()
    x(
        "INSERT INTO conversations (id, title, created, updated, skill, origin) VALUES (?,?,?,?,?,?)",
        (cid, title, now, now, skill or "", origin),
    )
    return get_conversation(cid)


def get_conversation(cid):
    return one("SELECT * FROM conversations WHERE id=?", (cid,))


def list_conversations(limit=300):
    rows = q(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS count "
        # agents' working conversations live in the Agents window, not among the chats
        "FROM conversations c WHERE origin!='agent' ORDER BY pinned DESC, updated DESC LIMIT ?",
        (limit,),
    )
    return [r for r in rows if r["count"]]


def touch_conversation(cid, **fields):
    fields["updated"] = time.time()
    keys = [k for k in fields if k in ("title", "pinned", "skill", "model", "updated")]
    x(
        "UPDATE conversations SET %s WHERE id=?" % ", ".join("%s=?" % k for k in keys),
        tuple(fields[k] for k in keys) + (cid,),
    )


def delete_conversation(cid):
    x("DELETE FROM messages WHERE conv_id=?", (cid,))
    x("DELETE FROM conversations WHERE id=?", (cid,))


def add_message(cid, role, content, data=None, mid=None):
    mid = mid or new_id()
    x(
        "INSERT INTO messages (id, conv_id, role, content, data, created) VALUES (?,?,?,?,?,?)",
        (mid, cid, role, content or "", json.dumps(data or {}), time.time()),
    )
    touch_conversation(cid)
    return mid


def update_message(mid, content=None, data=None):
    if content is not None:
        x("UPDATE messages SET content=? WHERE id=?", (content, mid))
    if data is not None:
        x("UPDATE messages SET data=? WHERE id=?", (json.dumps(data), mid))


def get_messages(cid):
    rows = q("SELECT * FROM messages WHERE conv_id=? ORDER BY created, rowid", (cid,))
    for row in rows:
        try:
            row["data"] = json.loads(row["data"] or "{}")
        except Exception:
            row["data"] = {}
    return rows


def delete_messages_from(cid, mid):
    row = one("SELECT created FROM messages WHERE id=?", (mid,))
    if row:
        x("DELETE FROM messages WHERE conv_id=? AND created>=?", (cid, row["created"]))
