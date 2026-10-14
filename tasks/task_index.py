"""Indexed task catalog and structured event storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class TaskIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fresh = not path.exists()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, stage TEXT NOT NULL, request_id TEXT NOT NULL,
                status TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_request ON tasks(stage,request_id,updated_at)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status)")
            existing = {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
            self.needs_migration='task_json' not in existing
            for name in ('task_json', 'product_id', 'source_id', 'owner_device_id', 'created_at'):
                if name not in existing:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} TEXT')
            if 'revision' not in existing:
                db.execute('ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS tasks_product ON tasks(product_id,updated_at)')
            db.execute('CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner_device_id,updated_at)')
            db.execute('PRAGMA user_version=2')
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                stage TEXT NOT NULL, entry_json TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS prediction_rows (
                task_id TEXT NOT NULL, file_index INTEGER NOT NULL, channel INTEGER NOT NULL,
                row_num INTEGER NOT NULL, prediction_json TEXT NOT NULL,
                PRIMARY KEY(task_id,file_index,channel,row_num))""")
            db.execute("""CREATE TABLE IF NOT EXISTS prediction_usage (
                task_id TEXT PRIMARY KEY, result_bytes INTEGER NOT NULL)""")

    @contextmanager
    def connect(self):
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

    def upsert(self, task: dict) -> None:
        with self.connect() as db:
            self.write_task(db, task)

    @staticmethod
    def write_task(db, task):
        db.execute('''INSERT INTO tasks(task_id,stage,request_id,status,updated_at,task_json,
            product_id,source_id,owner_device_id,created_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,
            task_json=excluded.task_json,product_id=excluded.product_id,source_id=excluded.source_id,
            owner_device_id=excluded.owner_device_id,revision=excluded.revision''',
            (task['task_id'],task['stage'],task['request_id'],task['status'],task['update_time'],
             json.dumps(task,ensure_ascii=False,default=str),task['product_id'],task.get('source_id'),
             task.get('owner_device_id'),task['start_time'],task.get('revision',0)))

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT task_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def list_tasks(self, filters=None, limit=100, offset=0, summary=True):
        filters = filters or {}
        if not 1 <= limit <= 1000 or not 0 <= offset <= 10_000_000:
            raise ValueError('分页参数超出范围')
        clauses, params = [], []
        for key in ('task_id','stage','status','product_id','source_id','owner_device_id'):
            if filters.get(key) is not None:
                clauses.append(key+'=?'); params.append(filters[key])
        for key, symbol in (('from_time','>='),('to_time','<=')):
            if filters.get(key):
                clauses.append('updated_at'+symbol+'?'); params.append(filters[key])
        if filters.get('channel') is not None:
            clauses.append("(json_extract(task_json,'$.current_channel')=? OR json_extract(task_json,'$.channel')=?)")
            params.extend([f"CH{filters['channel']}",int(filters['channel'])])
        condition = ' WHERE '+ ' AND '.join(clauses) if clauses else ''
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM tasks'+condition,params).fetchone()[0]
            rows = db.execute('SELECT task_json FROM tasks'+condition+' ORDER BY updated_at DESC,task_id LIMIT ? OFFSET ?',
                              (*params,limit,offset)).fetchall()
        items = [json.loads(row[0]) for row in rows if row[0]]
        if summary:
            items = [{k:v for k,v in task.items() if k not in {'result','input_files'}} for task in items]
        return items, total

    def active_ids(self) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT task_id FROM tasks WHERE status IN ('PENDING','QUEUED','RUNNING','CANCEL_REQUESTED')")]

    def find_request(self, stage: str, request_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute("""SELECT task_id FROM tasks WHERE stage=? AND request_id=?
                                ORDER BY updated_at DESC LIMIT 1""", (stage, request_id)).fetchone()
        return row[0] if row else None

    def append_event(self, entry: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO events(task_id,stage,entry_json) VALUES (?,?,?)",
                       (entry["task_id"], entry["stage"], json.dumps(entry, ensure_ascii=False, default=str)))
            # A bounded, searchable operational window; the JSONL mirror is separate audit storage.
            db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events WHERE task_id=? ORDER BY id DESC LIMIT -1 OFFSET 20000)',(entry['task_id'],))
            db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET 200000)')

    def import_events(self, task_id: str, log_path: Path) -> None:
        if not log_path.is_file():
            return
        with self.connect() as db, log_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                db.execute("INSERT INTO events(task_id,stage,entry_json) VALUES (?,?,?)",
                           (task_id, entry.get("stage", ""), json.dumps(entry, ensure_ascii=False)))

    def logs(self, task_id: str, stage: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        condition = "WHERE task_id=?" + (" AND stage=?" if stage else "")
        params = (task_id, stage) if stage else (task_id,)
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM events {condition}", params).fetchone()[0]
            rows = db.execute(f"SELECT entry_json FROM events {condition} ORDER BY id LIMIT ? OFFSET ?",
                              (*params, limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows], total

    def append_predictions(self, task_id: str, file_index: int, channel: int,
                           rows: list[dict], max_bytes: int) -> None:
        encoded = [(task_id, file_index, channel, int(row["row"]),
                    json.dumps(row, ensure_ascii=False, separators=(",", ":"))) for row in rows]
        byte_count = sum(len(row[4].encode("utf-8")) for row in encoded)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task=db.execute('SELECT status FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if not task or task[0] in {'COMPLETED','FAILED','CANCELLED','TIMEOUT','REVIEW_REQUIRED'}:
                raise ValueError('终态或不存在的任务不能追加预测结果')
            used = db.execute("SELECT result_bytes FROM prediction_usage WHERE task_id=?",
                              (task_id,)).fetchone()
            if (used[0] if used else 0) + byte_count > max_bytes:
                raise ValueError("预测结果字节预算已用尽")
            db.executemany("INSERT INTO prediction_rows VALUES (?,?,?,?,?)", encoded)
            db.execute("""INSERT INTO prediction_usage VALUES (?,?) ON CONFLICT(task_id)
                          DO UPDATE SET result_bytes=result_bytes+excluded.result_bytes""",
                       (task_id, byte_count))

    def predictions(self, task_id: str, file_index: int, channel: int,
                    limit: int, offset: int) -> tuple[list[dict], int]:
        with self.connect() as db:
            total = db.execute("""SELECT COUNT(*) FROM prediction_rows WHERE task_id=?
                                 AND file_index=? AND channel=?""",
                               (task_id, file_index, channel)).fetchone()[0]
            rows = db.execute("""SELECT prediction_json FROM prediction_rows WHERE task_id=?
                                 AND file_index=? AND channel=? ORDER BY row_num LIMIT ? OFFSET ?""",
                              (task_id, file_index, channel, limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows], total
