"""Persistent MES sources, submission receipts and bounded audit history."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Stage2SourceStore:
    def __init__(self, path: Path, defaults: dict[str, Path], max_events: int = 20000,
                 retention_days: int = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_events = max(1, max_events)
        self.retention_seconds = max(1, retention_days) * 86400
        with self.connect() as db:
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY, directory TEXT NOT NULL,
                owner_device_id TEXT, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
                owner_device_id TEXT, created REAL NOT NULL, entry_json TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS source_events ON events(source_id,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                actor TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                task_id TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(actor,request_id))""")
            for source_id, directory in defaults.items():
                db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,NULL,1,?)",
                           (source_id, str(directory), self.timestamp()))

    @staticmethod
    def timestamp():
        return datetime.now(timezone.utc).isoformat()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def sources(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY source_id")]

    def get(self, source_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def save(self, source_id, directory, owner_device_id, revision):
        value = dict(source_id=source_id, directory=str(directory), owner_device_id=owner_device_id,
                     revision=revision, updated_at=self.timestamp())
        with self.connect() as db:
            db.execute("""INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                directory=excluded.directory, owner_device_id=excluded.owner_device_id,
                revision=excluded.revision, updated_at=excluded.updated_at""", tuple(value.values()))
            from services.idempotency import link_domain_receipt
            link_domain_receipt(db,{**value,'exists':True},201 if revision==1 else 200)
        return value

    def log(self, source_id, owner_device_id, **values):
        # Only structured caller metadata is passed here; never headers or credentials.
        entry = dict(timestamp=self.timestamp(), source_id=source_id, **values)
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events(source_id,owner_device_id,created,entry_json) VALUES (?,?,?,?)",
                                (source_id, owner_device_id, time.time(), json.dumps(entry, ensure_ascii=False)))
            db.execute("DELETE FROM events WHERE created < ?", (time.time() - self.retention_seconds,))
            db.execute("DELETE FROM events WHERE id <= ?", (cursor.lastrowid - self.max_events,))
            db.execute("PRAGMA incremental_vacuum(32)")
        return entry

    def logs(self, source_id, owner_device_id, limit, offset):
        condition = "source_id=?"
        params = [source_id]
        if owner_device_id is not None:
            condition += " AND owner_device_id=?"
            params.append(owner_device_id)
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE created < ?", (time.time() - self.retention_seconds,))
            total = db.execute(f"SELECT COUNT(*) FROM events WHERE {condition}", params).fetchone()[0]
            rows = db.execute(f"SELECT entry_json FROM events WHERE {condition} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*params, limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows], total

    def receipt(self, actor, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM receipts WHERE actor=? AND request_id=? AND created>=?",
                             (actor, request_id, time.time() - self.retention_seconds)).fetchone()
        return dict(row) if row else None

    def receipt_capacity(self):
        with self.connect() as db:
            db.execute("DELETE FROM receipts WHERE created < ?", (time.time() - self.retention_seconds,))
            return db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] < self.max_events

    def save_receipt(self, actor, request_id, fingerprint, task_id):
        with self.connect() as db:
            db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                       (actor, request_id, fingerprint, task_id, time.time()))
