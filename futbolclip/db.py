"""SQLite veri katmanı (kullanıcılar, projeler, videolar, klipler, dışa aktarımlar)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from .config import settings

JSON_COLS = {
    "jobs": {"params", "warnings"},
    "videos": {"meta"},
    "clips": {"tags", "hashtags", "speakers", "captions", "style", "meta"},
    "exports": {"meta"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, pw_hash TEXT NOT NULL, created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT, title TEXT, query TEXT,
  status TEXT, stage TEXT, progress REAL DEFAULT 0, message TEXT, error TEXT,
  params TEXT, warnings TEXT, created_at REAL, updated_at REAL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS videos(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source TEXT, source_id TEXT, title TEXT, channel TEXT,
  license TEXT, url TEXT, duration REAL, path TEXT, transcript_path TEXT, thumbnail TEXT,
  status TEXT, meta TEXT, created_at REAL, updated_at REAL,
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS clips(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, video_id TEXT, start REAL, "end" REAL, score REAL,
  title TEXT, reason TEXT, tags TEXT, hashtags TEXT, speakers TEXT, captions TEXT, layout TEXT,
  style TEXT, status TEXT, error TEXT, preview_path TEXT, thumb_path TEXT, meta TEXT,
  created_at REAL, updated_at REAL,
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS exports(
  id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, user_id TEXT NOT NULL, platform TEXT, status TEXT,
  progress REAL DEFAULT 0, error TEXT, path TEXT, size INTEGER, meta TEXT, created_at REAL, updated_at REAL,
  FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id, score);
CREATE INDEX IF NOT EXISTS idx_exports_user ON exports(user_id, created_at);
"""


@contextmanager
def conn():
    c = sqlite3.connect(settings.db_path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _decode(table: str, row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS.get(table, ()):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def _encode(table: str, vals: dict) -> dict:
    out = {}
    for k, v in vals.items():
        if k in JSON_COLS.get(table, ()) and v is not None:
            v = json.dumps(v, ensure_ascii=False)
        out[k] = v
    return out


def _q(col: str) -> str:
    return f'"{col}"'


def insert(table: str, **vals: Any) -> str:
    vals.setdefault("id", new_id())
    now = time.time()
    vals.setdefault("created_at", now)
    if table != "users":
        vals.setdefault("updated_at", now)
    vals = _encode(table, vals)
    cols = ",".join(_q(k) for k in vals)
    marks = ",".join("?" for _ in vals)
    with conn() as c:
        c.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", list(vals.values()))
    return vals["id"]


def update(table: str, id_: str, **vals: Any) -> None:
    if table != "users":
        vals["updated_at"] = time.time()
    vals = _encode(table, vals)
    sets = ",".join(f"{_q(k)}=?" for k in vals)
    with conn() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*vals.values(), id_])


def get(table: str, id_: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
    return _decode(table, row)


def select(table: str, where: str = "1=1", args: Iterable = (), order: str = "", limit: int = 0) -> list[dict]:
    sql = f"SELECT * FROM {table} WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn() as c:
        rows = c.execute(sql, list(args)).fetchall()
    return [_decode(table, r) for r in rows]  # type: ignore[misc]


def delete(table: str, id_: str) -> None:
    with conn() as c:
        c.execute(f"DELETE FROM {table} WHERE id=?", (id_,))


def get_user_by_email(email: str) -> Optional[dict]:
    rows = select("users", "email=?", (email.lower(),))
    return rows[0] if rows else None
