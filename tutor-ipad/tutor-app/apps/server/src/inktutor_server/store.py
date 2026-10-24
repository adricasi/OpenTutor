"""SQLite store: sessions, pages, strokes, readings, feedback. JSON blobs keyed by ids; one writer lock.

Small and synchronous on purpose: one Mac, a few users, writes are tiny batches of strokes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from inktutor_protocol import FeedbackEvent, PageRef, Reading, Session, Stroke

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pages (session_id TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY (session_id, id));
CREATE TABLE IF NOT EXISTS strokes (
  session_id TEXT, page_id TEXT, id TEXT, seq INTEGER, data TEXT NOT NULL, removed INTEGER DEFAULT 0,
  PRIMARY KEY (session_id, page_id, id));
CREATE TABLE IF NOT EXISTS readings (session_id TEXT, page_id TEXT, version INTEGER, data TEXT NOT NULL,
  PRIMARY KEY (session_id, page_id));
CREATE TABLE IF NOT EXISTS feedback (session_id TEXT, page_id TEXT, id TEXT, data TEXT NOT NULL, created REAL,
  provider TEXT, model TEXT, PRIMARY KEY (session_id, id));
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._seq = 0

    def close(self) -> None:
        self._db.close()

    # ---- sessions / pages
    def put_session(self, s: Session) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (s.id, s.model_dump_json(), time.time()),
            )

    def get_session(self, sid: str) -> Session | None:
        row = self._db.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        return Session.model_validate_json(row[0]) if row else None

    def put_page(self, sid: str, p: PageRef) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO pages VALUES (?,?,?) ON CONFLICT DO UPDATE SET data=excluded.data",
                (sid, p.id, p.model_dump_json()),
            )

    # ---- strokes
    def apply_ink(self, sid: str, page_id: str, changed: list[Stroke], removed: list[str]) -> None:
        with self._lock, self._db:
            for s in changed:
                self._seq += 1
                self._db.execute(
                    "INSERT INTO strokes VALUES (?,?,?,?,?,0) ON CONFLICT DO UPDATE SET data=excluded.data, removed=0",
                    (sid, page_id, s.id, self._seq, s.model_dump_json()),
                )
            for rid in removed:
                self._db.execute(
                    "UPDATE strokes SET removed=1 WHERE session_id=? AND page_id=? AND id=?", (sid, page_id, rid)
                )

    def strokes(self, sid: str, page_id: str) -> list[Stroke]:
        rows = self._db.execute(
            "SELECT data FROM strokes WHERE session_id=? AND page_id=? AND removed=0 ORDER BY seq", (sid, page_id)
        ).fetchall()
        return [Stroke.model_validate_json(r[0]) for r in rows]

    # ---- readings / feedback
    def put_reading(self, sid: str, r: Reading) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO readings VALUES (?,?,?,?)"
                " ON CONFLICT DO UPDATE SET version=excluded.version, data=excluded.data",
                (sid, r.page_id, r.version, r.model_dump_json()),
            )

    def put_feedback(self, sid: str, f: FeedbackEvent, provider: str = "", model: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO feedback VALUES (?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET data=excluded.data",
                (sid, f.page_id, f.id, f.model_dump_json(), time.time(), provider, model),
            )

    def feedback(self, sid: str, page_id: str) -> list[FeedbackEvent]:
        rows = self._db.execute(
            "SELECT data FROM feedback WHERE session_id=? AND page_id=? ORDER BY created", (sid, page_id)
        ).fetchall()
        return [FeedbackEvent.model_validate(json.loads(r[0])) for r in rows]
