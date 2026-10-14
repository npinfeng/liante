"""SQLite journal for acknowledged AOI frames and their outcomes."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class FrameJournalFull(Exception):
    pass


class FrameJournal:
    def __init__(self, data_dir: Path, retention_days: int = 30,
                 max_bytes: int = 20 * 1024 * 1024 * 1024) -> None:
        self.path = Path(data_dir) / "aoi_frames.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        self._last_prune = time.monotonic()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS frames (
                stream_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                product_id TEXT NOT NULL, data BLOB NOT NULL,
                mime_type TEXT NOT NULL, suffix TEXT NOT NULL, checksum TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(stream_id, sequence))""")
            db.execute("CREATE INDEX IF NOT EXISTS frames_status ON frames(stream_id, status, sequence)")
            db.execute("CREATE TABLE IF NOT EXISTS storage_meta (id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO storage_meta VALUES (1, (SELECT COALESCE(SUM(length(data)),0) FROM frames))")
            if 'view_type' not in {row[1] for row in db.execute('PRAGMA table_info(frames)')}:
                db.execute("ALTER TABLE frames ADD COLUMN view_type TEXT NOT NULL DEFAULT 'UNSPECIFIED'")
            db.execute('''CREATE TABLE IF NOT EXISTS streams (
                stream_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS products (
                stream_id TEXT NOT NULL,product_id TEXT NOT NULL,required_views TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,reason TEXT,PRIMARY KEY(stream_id,product_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS product_lookup ON products(product_id,updated_at)')
            db.execute('PRAGMA user_version=2')

    @staticmethod
    def timestamp(): return datetime.now(timezone.utc).isoformat()

    def register_stream(self,stream_id,metadata,closed=False):
        stamp=self.timestamp()
        with self._lock,self._connect() as db:
            db.execute('INSERT OR IGNORE INTO streams VALUES (?,?,?,?,?)',
                       (stream_id,json.dumps(metadata,ensure_ascii=False),int(closed),stamp,stamp))

    def stream(self,stream_id):
        with self._lock,self._connect() as db:
            row=db.execute('SELECT metadata_json,closed,created_at,updated_at FROM streams WHERE stream_id=?',(stream_id,)).fetchone()
        return dict(stream_id=stream_id,closed=bool(row[1]),created_at=row[2],updated_at=row[3],**json.loads(row[0])) if row else None

    def recovery_streams(self):
        with self._lock,self._connect() as db:
            # Include journal-only orphans; do not rely on a task snapshot's state.
            ids=[row[0] for row in db.execute('''SELECT stream_id FROM streams WHERE closed=0
                UNION SELECT stream_id FROM frames WHERE status IN ('QUEUED','RUNNING')
                UNION SELECT stream_id FROM products WHERE status IN ('RECEIVING','PROCESSING')''')]
        return ids

    def open_product(self,stream_id,product_id,required_views):
        stamp=self.timestamp()
        with self._lock,self._connect() as db:
            row=db.execute('SELECT required_views,closed FROM products WHERE stream_id=? AND product_id=?',(stream_id,product_id)).fetchone()
            if row:
                if set(json.loads(row[0]))!=set(required_views): raise ValueError('产品 required_views 已确定，不能修改')
                if row[1]: raise ValueError('产品已关闭')
            else:
                db.execute('INSERT INTO products VALUES (?,?,?,0,?,?,?,NULL)',
                           (stream_id,product_id,json.dumps(required_views),'RECEIVING',stamp,stamp))
        return self.product(stream_id,product_id)

    def _settle(self,db,stream_id,product_id):
        row=db.execute('SELECT required_views,closed FROM products WHERE stream_id=? AND product_id=?',(stream_id,product_id)).fetchone()
        if not row: return
        views={item[0] for item in db.execute('SELECT DISTINCT view_type FROM frames WHERE stream_id=? AND product_id=?',(stream_id,product_id))}
        missing=set(json.loads(row[0]))-views
        count,pending,failed=db.execute('''SELECT COUNT(*),SUM(CASE WHEN status IN ('QUEUED','RUNNING') THEN 1 ELSE 0 END),
            SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) FROM frames WHERE stream_id=? AND product_id=?''',(stream_id,product_id)).fetchone()
        if not row[1]: status,reason='RECEIVING',None
        elif missing or not count: status,reason='INCOMPLETE','MISSING_REQUIRED_VIEWS' if missing else 'NO_FRAMES'
        elif pending: status,reason='PROCESSING',None
        elif failed: status,reason='FAILED','FRAME_FAILED'
        else: status,reason='COMPLETED',None
        db.execute('UPDATE products SET status=?,reason=?,updated_at=? WHERE stream_id=? AND product_id=?',
                   (status,reason,self.timestamp(),stream_id,product_id))

    def close_product(self,stream_id,product_id):
        with self._lock,self._connect() as db:
            if not db.execute('SELECT 1 FROM products WHERE stream_id=? AND product_id=?',(stream_id,product_id)).fetchone():
                raise KeyError(product_id)
            db.execute('UPDATE products SET closed=1 WHERE stream_id=? AND product_id=?',(stream_id,product_id))
            self._settle(db,stream_id,product_id)
        return self.product(stream_id,product_id)

    def close_stream(self,stream_id):
        with self._lock,self._connect() as db:
            db.execute('UPDATE streams SET closed=1,updated_at=? WHERE stream_id=?',(self.timestamp(),stream_id))
            db.execute('UPDATE products SET closed=1 WHERE stream_id=?',(stream_id,))
            for (product_id,) in db.execute('SELECT product_id FROM products WHERE stream_id=?',(stream_id,)).fetchall():
                self._settle(db,stream_id,product_id)

    def product(self,stream_id,product_id):
        with self._lock,self._connect() as db:
            row=db.execute('SELECT required_views,closed,status,created_at,updated_at,reason FROM products WHERE stream_id=? AND product_id=?',(stream_id,product_id)).fetchone()
            if not row: return None
            received=[x[0] for x in db.execute('SELECT DISTINCT view_type FROM frames WHERE stream_id=? AND product_id=? ORDER BY view_type',(stream_id,product_id))]
            counts=db.execute('SELECT status,COUNT(*) FROM frames WHERE stream_id=? AND product_id=? GROUP BY status',(stream_id,product_id)).fetchall()
        required=json.loads(row[0])
        return dict(stream_id=stream_id,product_id=product_id,required_views=required,received_views=received,
                    missing_views=sorted(set(required)-set(received)),closed=bool(row[1]),status=row[2],
                    created_at=row[3],updated_at=row[4],reason=row[5],frames=dict(counts))

    def products(self,stream_id=None,product_id=None,limit=100,offset=0,owner=None):
        clauses=[];args=[]
        for key,value in (('stream_id',stream_id),('product_id',product_id)):
            if value is not None: clauses.append(key+'=?');args.append(value)
        if owner is not None:
            clauses.append("stream_id IN (SELECT stream_id FROM streams WHERE json_extract(metadata_json,'$.owner_device_id')=?)")
            args.append(owner)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self._lock,self._connect() as db:
            total=db.execute('SELECT COUNT(*) FROM products'+where,args).fetchone()[0]
            rows=db.execute('SELECT stream_id,product_id FROM products'+where+' ORDER BY updated_at DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return [self.product(*row) for row in rows],total

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def insert(self, stream_id: str, frame: Any) -> None:
        if time.monotonic() - self._last_prune >= 3600:
            self.prune()
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            used = db.execute("SELECT bytes FROM storage_meta WHERE id=1").fetchone()[0]
            if used + len(frame.data) > self.max_bytes:
                raise FrameJournalFull("AOI 帧持久化容量已满")
            metadata=db.execute('SELECT metadata_json,closed FROM streams WHERE stream_id=?',(stream_id,)).fetchone()
            if not metadata: raise ValueError('AOI 流元数据缺失，不能确认帧')
            if metadata[1]: raise ValueError('AOI 流已关闭')
            defaults=json.loads(metadata[0]).get('required_views',['UNSPECIFIED'])
            db.execute('INSERT OR IGNORE INTO products VALUES (?,?,?,0,?,?,?,NULL)',
                       (stream_id,frame.product_id,json.dumps(defaults),'RECEIVING',timestamp,timestamp))
            product_closed=db.execute('SELECT closed FROM products WHERE stream_id=? AND product_id=?',(stream_id,frame.product_id)).fetchone()[0]
            if product_closed: raise ValueError('产品已关闭')
            db.execute("""INSERT INTO frames(stream_id,sequence,product_id,data,mime_type,suffix,checksum,status,result_json,created_at,updated_at,view_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', NULL, ?, ?, ?)""",
                       (stream_id, frame.sequence, frame.product_id, frame.data,
                        frame.mime_type, frame.suffix, frame.checksum, timestamp, timestamp,getattr(frame,'view_type','UNSPECIFIED')))
            db.execute("UPDATE storage_meta SET bytes=bytes+? WHERE id=1", (len(frame.data),))

    def get(self, stream_id: str, sequence: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("""SELECT product_id, data, mime_type, suffix, checksum, status,
                                result_json,view_type,created_at,updated_at FROM frames WHERE stream_id=? AND sequence=?""",
                             (stream_id, sequence)).fetchone()
        if row is None:
            return None
        return dict(sequence=sequence, product_id=row[0], data=row[1], mime_type=row[2],
                    suffix=row[3], checksum=row[4], status=row[5],
                    result=json.loads(row[6]) if row[6] else None,view_type=row[7],received_at=row[8],processed_at=row[9])

    def pending(self, stream_id: str, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT sequence, product_id, data, mime_type, suffix, checksum,view_type
                                 FROM frames WHERE stream_id=? AND status IN ('QUEUED','RUNNING')
                                 ORDER BY sequence LIMIT ?""", (stream_id,limit)).fetchall()
        return [dict(sequence=row[0], product_id=row[1], data=row[2], mime_type=row[3],
                     suffix=row[4], checksum=row[5],view_type=row[6]) for row in rows]

    def mark_running(self, stream_id: str, sequence: int) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE frames SET status='RUNNING', updated_at=? WHERE stream_id=? AND sequence=? AND status IN ('QUEUED','RUNNING')",
                       (datetime.now(timezone.utc).isoformat(), stream_id, sequence))

    def finish(self, stream_id: str, sequence: int, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            row=db.execute('SELECT product_id,view_type,created_at FROM frames WHERE stream_id=? AND sequence=?',(stream_id,sequence)).fetchone()
            if not row: raise KeyError(sequence)
            record.update(view_type=row[1],received_at=row[2],processed_at=self.timestamp())
            db.execute("UPDATE frames SET status=?, result_json=?, updated_at=? WHERE stream_id=? AND sequence=? AND status IN ('QUEUED','RUNNING')",
                       (record["status"], json.dumps(record, ensure_ascii=False, default=str),
                        datetime.now(timezone.utc).isoformat(), stream_id, sequence))
            self._settle(db,stream_id,row[0])

    def records(self, stream_id: str, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self._lock, self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM frames WHERE stream_id=?",
                               (stream_id,)).fetchone()[0]
            rows = db.execute("""SELECT result_json,sequence,product_id,checksum,status,view_type,created_at,updated_at FROM frames WHERE stream_id=?
                                 ORDER BY sequence LIMIT ? OFFSET ?""", (stream_id, limit, offset)).fetchall()
        return [(json.loads(row[0]) if row[0] else dict(sequence=row[1],product_id=row[2],sha256=row[3],status=row[4],
                  view_type=row[5],received_at=row[6],processed_at=row[7] if row[4]!='QUEUED' else None)) for row in rows], total

    def qualification_counts(self, stream_id: str, product_id: str) -> tuple[int, int, int]:
        total = failed = not_qualified = 0
        with self._lock, self._connect() as db:
            for status, result_json in db.execute(
                "SELECT status,result_json FROM frames WHERE stream_id=? AND product_id=? ORDER BY sequence",
                (stream_id, product_id)):
                total += 1
                if status != "COMPLETED":
                    failed += 1
                else:
                    output = json.loads(result_json).get("output") if result_json else None
                    if not isinstance(output, dict) or output.get("qualified") is not True:
                        not_qualified += 1
        return total, failed, not_qualified

    def qualification_versions(self, stream_id: str, product_id: str) -> tuple[set[str], set[str]]:
        model_versions, recipe_versions = set(), set()
        with self._lock, self._connect() as db:
            for (result_json,) in db.execute(
                "SELECT result_json FROM frames WHERE stream_id=? AND product_id=? AND status='COMPLETED'",
                (stream_id, product_id)):
                if result_json:
                    result = json.loads(result_json)
                    if result.get("model_version"):
                        model_versions.add(str(result["model_version"]))
                    if result.get("recipe_version"):
                        recipe_versions.add(str(result["recipe_version"]))
        return model_versions, recipe_versions

    def stats(self, stream_id: str) -> tuple[int, int, int, int, list[dict[str, Any]]]:
        with self._lock, self._connect() as db:
            total, processed, failed, last = db.execute("""SELECT COUNT(*),
                SUM(CASE WHEN status IN ('COMPLETED','FAILED') THEN 1 ELSE 0 END),
                SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END), COALESCE(MAX(sequence),0)
                FROM frames WHERE stream_id=?""", (stream_id,)).fetchone()
            rows = db.execute("""SELECT result_json FROM frames WHERE stream_id=? AND result_json IS NOT NULL
                ORDER BY sequence DESC LIMIT 20""", (stream_id,)).fetchall()
        return total, processed or 0, failed or 0, last, [json.loads(row[0]) for row in reversed(rows)]

    def prune(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._connect() as db:
            freed = db.execute("""SELECT COALESCE(SUM(length(data)),0) FROM frames WHERE updated_at < ?
                AND status IN ('COMPLETED','FAILED') AND length(data)>0""", (cutoff,)).fetchone()[0]
            cursor = db.execute("""UPDATE frames SET data=x'' WHERE updated_at < ?
                AND status IN ('COMPLETED','FAILED') AND length(data)>0""", (cutoff,))
            db.execute("UPDATE storage_meta SET bytes=MAX(0,bytes-?) WHERE id=1", (freed,))
            self._last_prune = time.monotonic()
            return cursor.rowcount

    def usage(self) -> dict[str, int]:
        with self._lock, self._connect() as db:
            used = db.execute("SELECT bytes FROM storage_meta WHERE id=1").fetchone()[0]
        physical=sum(p.stat().st_size for p in (self.path,Path(str(self.path)+'-wal')) if p.exists())
        return dict(journal_frame_bytes=used, journal_max_bytes=self.max_bytes,journal_database_bytes=physical)
