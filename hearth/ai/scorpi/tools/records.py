"""A small document database the assistant can read and update, plus read-only SQL on SQLite files."""

import json
import os
import sqlite3
import time

from .. import db
from . import Result, ToolError, resolve_path, tool


def _fmt(row):
    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"] or "{}")
    return "#%d [%s] %s" % (row["id"], row["collection"], json.dumps(data, ensure_ascii=False))


@tool("db_collections", "List record collections in the database and how many records each holds.", {}, group="records")
def db_collections(ctx):
    rows = db.q("SELECT collection, COUNT(*) AS n, MAX(updated) AS last FROM records GROUP BY collection ORDER BY collection")
    if not rows:
        return Result("The database is empty. Use db_insert to create the first collection.")
    return Result("\n".join("%s: %d records (updated %s)" % (r["collection"], r["n"], time.strftime("%Y-%m-%d", time.localtime(r["last"] or 0))) for r in rows))


@tool(
    "db_insert",
    "Store one or more records in a collection (e.g. contacts, expenses, books, workouts). Records are JSON objects.",
    {"collection": "string: Collection name, lowercase", "records": {"type": "array", "description": "List of JSON objects", "items": {"type": "object"}}},
    group="records",
    risk="write",
)
def db_insert(ctx, collection, records):
    if isinstance(records, dict):
        records = [records]
    ids = []
    now = time.time()
    for rec in records or []:
        ids.append(db.x("INSERT INTO records (collection, data, created, updated) VALUES (?,?,?,?)",
                        (collection.strip().lower(), json.dumps(rec, ensure_ascii=False), now, now)))
    return Result("Inserted %d record(s) into %s: ids %s" % (len(ids), collection, ", ".join(map(str, ids))))


@tool(
    "db_search",
    "Search records: optional collection, free-text query, and exact field filters. Returns matching records with ids.",
    {"collection?": "string: Collection", "query?": "string: Text contained anywhere", "where?": {"type": "object", "description": "Exact field matches, e.g. {\"status\": \"open\"}"}, "limit?": "integer: Max (default 50)"},
    group="records",
)
def db_search(ctx, collection="", query="", where=None, limit=50):
    sql = "SELECT * FROM records WHERE 1=1"
    params = []
    if collection:
        sql += " AND collection=?"
        params.append(collection.strip().lower())
    if query:
        sql += " AND data LIKE ?"
        params.append("%" + query + "%")
    for key, value in (where or {}).items():
        sql += " AND json_extract(data, ?) = ?"
        params.extend(["$." + key, value])
    sql += " ORDER BY updated DESC LIMIT ?"
    params.append(min(int(limit or 50), 500))
    rows = db.q(sql, tuple(params))
    if not rows:
        return Result("No matching records.")
    parsed = [dict(r, data=json.loads(r["data"])) for r in rows]
    columns = sorted({k for r in parsed for k in r["data"]})[:10]
    table = {"type": "table", "columns": ["id"] + columns, "rows": [[str(r["id"])] + [str(r["data"].get(c, "")) for c in columns] for r in parsed[:25]],
             "caption": "%s · %d record(s)" % (collection or "all collections", len(parsed))}
    return Result("\n".join(_fmt(r) for r in parsed), display=table)


@tool(
    "db_update",
    "Update fields of a record by id (merges into the existing JSON; set a field to null to remove it).",
    {"id": "integer: Record id", "fields": {"type": "object", "description": "Fields to set"}},
    group="records",
    risk="write",
)
def db_update(ctx, id, fields):
    row = db.one("SELECT * FROM records WHERE id=?", (int(id),))
    if not row:
        raise ToolError("No record #%s" % id)
    data = json.loads(row["data"] or "{}")
    for k, v in (fields or {}).items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    db.x("UPDATE records SET data=?, updated=? WHERE id=?", (json.dumps(data, ensure_ascii=False), time.time(), int(id)))
    return Result("Updated #%s: %s" % (id, json.dumps(data, ensure_ascii=False)))


@tool("db_delete", "Delete records by id.", {"ids": "array<integer>: Record ids"}, group="records", risk="danger")
def db_delete(ctx, ids):
    count = 0
    for rid in ids or []:
        count += db.x("DELETE FROM records WHERE id=?", (int(rid),)) or 0
    return Result("Deleted %d record(s)." % count)


@tool(
    "sql_query",
    "Run a read-only SQL SELECT on a SQLite database file (or Scorpi's own database if no path). "
    "Scorpi tables: records(collection, data JSON), reminders, events, memories, conversations, messages.",
    {"sql": "string: SELECT statement", "path?": "string: Path to a .db/.sqlite file"},
    group="records",
)
def sql_query(ctx, sql, path=""):
    statement = sql.strip().rstrip(";")
    if not statement.lower().startswith(("select", "with", "pragma table_info", "pragma table_list")):
        raise ToolError("Only read-only SELECT queries are allowed here; use db_insert/db_update to change data.")
    target = resolve_path(path, must_exist=True) if path else db.DB_PATH
    if not os.path.isfile(target):
        raise ToolError("Not a database file: %s" % target)
    conn = sqlite3.connect("file:%s?mode=ro" % target.replace("\\", "/"), uri=True)
    try:
        cur = conn.execute(statement)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(200)
    except sqlite3.Error as exc:
        raise ToolError("SQL error: %s" % exc)
    finally:
        conn.close()
    lines = [" | ".join(cols)] + [" | ".join("" if v is None else str(v)[:120] for v in row) for row in rows]
    return Result("\n".join(lines), display={"type": "table", "columns": cols, "rows": [[str(v) for v in r] for r in rows[:25]], "caption": "%d row(s)" % len(rows)})
