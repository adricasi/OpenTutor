"""SQLite store: sessions, pages, strokes, readings, feedback. JSON blobs keyed by ids; one writer lock.

Small and synchronous on purpose: one Mac, a few users, writes are tiny batches of strokes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from inktutor_protocol import FeedbackEvent, Homework, PageRef, ProgressSummary, Reading, Session, Stroke

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
CREATE TABLE IF NOT EXISTS homework (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS activity (session_id TEXT, page_id TEXT, kind TEXT, line_id TEXT, data TEXT NOT NULL,
  created REAL);
CREATE INDEX IF NOT EXISTS activity_session ON activity (session_id, created);
CREATE TABLE IF NOT EXISTS summaries (session_id TEXT PRIMARY KEY, log_hash TEXT, data TEXT NOT NULL, created REAL);
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

    # ---- teacher view (progress.py, teacher.py): queries over what is stored, plus homework and summaries
    def sessions(self, homework_id: str | None = None) -> list[tuple[Session, float]]:
        """(session, created) oldest first; only those of one homework when `homework_id` is given."""
        if homework_id is None:
            rows = self._db.execute("SELECT data, created FROM sessions ORDER BY created").fetchall()
        else:
            rows = self._db.execute(
                "SELECT data, created FROM sessions WHERE json_extract(data, '$.homework_id')=? ORDER BY created",
                (homework_id,),
            ).fetchall()
        return [(Session.model_validate_json(r[0]), float(r[1])) for r in rows]

    def session_created(self, sid: str) -> float:
        row = self._db.execute("SELECT created FROM sessions WHERE id=?", (sid,)).fetchone()
        return float(row[0]) if row else 0.0

    def feedback_log(self, sid: str) -> list[tuple[FeedbackEvent, float]]:
        """Every feedback event of a session (all pages, retracts included) with its time, in order."""
        rows = self._db.execute(
            "SELECT data, created FROM feedback WHERE session_id=? ORDER BY created, rowid", (sid,)
        ).fetchall()
        return [(FeedbackEvent.model_validate_json(r[0]), float(r[1] or 0)) for r in rows]

    def readings(self, sid: str) -> list[Reading]:
        rows = self._db.execute("SELECT data FROM readings WHERE session_id=?", (sid,)).fetchall()
        return [Reading.model_validate_json(r[0]) for r in rows]

    def ink_stats(self, sid: str) -> tuple[int, float | None, float | None]:
        """(strokes ever drawn, first and last ink time in ms on the session clock); erased strokes count too."""
        row = self._db.execute(
            "SELECT count(*), min(json_extract(data, '$.pts[0][0]')), max(json_extract(data, '$.pts[#-1][0]'))"
            " FROM strokes WHERE session_id=?",
            (sid,),
        ).fetchone()
        return (
            int(row[0]),
            (float(row[1]) if row[1] is not None else None),
            (float(row[2]) if row[2] is not None else None),
        )

    def all_strokes(self, sid: str, page_id: str) -> list[tuple[Stroke, bool]]:
        """(stroke, erased) for the read-only replay, in drawing order."""
        rows = self._db.execute(
            "SELECT data, removed FROM strokes WHERE session_id=? AND page_id=? ORDER BY seq", (sid, page_id)
        ).fetchall()
        return [(Stroke.model_validate_json(r[0]), bool(r[1])) for r in rows]

    def log_activity(self, sid: str, page_id: str, kind: str, line_id: str, data: dict[str, object]) -> None:
        """Learning-log facts the feedback table can't tell (verdict kinds, answers, corrections). Small text only."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO activity VALUES (?,?,?,?,?,?)",
                (sid, page_id, kind, line_id, json.dumps(data, ensure_ascii=False), time.time()),
            )

    def activity(self, sid: str) -> list[tuple[str, str, str, dict[str, object], float]]:
        """(kind, page id, line id, data, time) in order."""
        rows = self._db.execute(
            "SELECT kind, page_id, line_id, data, created FROM activity WHERE session_id=? ORDER BY created, rowid",
            (sid,),
        ).fetchall()
        return [(r[0], r[1], r[2], json.loads(r[3]), float(r[4] or 0)) for r in rows]

    def put_homework(self, hw: Homework) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO homework VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (hw.id, hw.model_dump_json(), hw.created),
            )

    def get_homework(self, hid: str) -> Homework | None:
        row = self._db.execute("SELECT data FROM homework WHERE id=?", (hid,)).fetchone()
        return Homework.model_validate_json(row[0]) if row else None

    def list_homework(self) -> list[Homework]:
        rows = self._db.execute("SELECT data FROM homework ORDER BY created DESC").fetchall()
        return [Homework.model_validate_json(r[0]) for r in rows]

    def delete_homework(self, hid: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM homework WHERE id=?", (hid,)).rowcount > 0

    def put_summary(self, s: ProgressSummary) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO summaries VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET"
                " log_hash=excluded.log_hash, data=excluded.data, created=excluded.created",
                (s.session_id, s.log_hash, s.model_dump_json(), s.created),
            )

    def get_summary(self, sid: str) -> ProgressSummary | None:
        row = self._db.execute("SELECT data FROM summaries WHERE session_id=?", (sid,)).fetchone()
        return ProgressSummary.model_validate_json(row[0]) if row else None
