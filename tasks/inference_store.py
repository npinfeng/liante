"""Durable, task-scoped inference state and structured JSONL event log."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
import re
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tasks.task_index import TaskIndex
from tasks.state_machine import TERMINAL, validate_transition


STAGE_NAMES = {
    "stage1": "AOI外观检测",
    "stage2": "EA/Bias参数推荐",
    "stage3": "多模态质量分级",
    "automl": "AutoML训练",
}
LOGGER = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InferenceTaskStore:
    def __init__(self, output_dir: str | Path, log_dir: str | Path) -> None:
        self.output_dir = Path(output_dir) / "tasks"
        self.log_dir = Path(log_dir) / "tasks"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.index = TaskIndex(self.output_dir / "index.sqlite3")
        # One-time migration imports old JSON only when SQLite has no authoritative payload.
        self.snapshot_errors = 0
        self.audit_mirror_errors = 0
        paths = self.output_dir.glob('*.json') if self.index.fresh or self.index.needs_migration else ()
        for path in paths:
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
                if not re.fullmatch(r'[a-f0-9]{32}', task.get('task_id','')):
                    continue
                if self.index.get(task['task_id']) is None:
                    self.index.upsert(task)
                    self.index.import_events(task["task_id"], self.log_dir / f"{task['task_id']}.jsonl")
                recoverable_stream = (task.get("stage") == "stage1" and
                                      (task.get("status") == "RUNNING" or
                                       task.get("error_code") == "FRAME_STORAGE_FAILED")
                                      and isinstance(task.get("result"), dict)
                                      and task["result"].get("stream_id") == task.get("task_id"))
                task = self.index.get(task['task_id']) or task
                if task["status"] in {"PENDING", "QUEUED", "RUNNING", "CANCEL_REQUESTED"} and not recoverable_stream:
                    task.update(status="FAILED", error_code="TASK_FAILED",
                                error_message="服务重启，中断了运行中的任务；使用 retry 创建新任务", finish_time=now(), update_time=now())
                    self._persist(task)
            except (OSError, ValueError, KeyError):
                LOGGER.exception("Unable to restore task %s", path)
        # Tasks whose snapshot was lost must also reach a terminal state.
        for task_id in self.index.active_ids():
            task = self.index.get(task_id)
            if task and not (task['stage']=='stage1' and (task.get('result') or {}).get('stream_id')==task_id):
                self.update(task_id,status='FAILED',error_code='SERVICE_RESTARTED',
                            error_message='服务重启中断任务；使用 retry 创建新任务')

    def _persist(self, task: dict[str, Any]) -> None:
        # Commit authority first. A snapshot failure cannot undo an acknowledged task.
        self.index.upsert(task)
        self._snapshot(task)

    def _snapshot(self, task):
        target = self.output_dir / f"{task['task_id']}.json"
        temporary = target.with_suffix('.tmp')
        try:
            with temporary.open('w',encoding='utf-8') as stream:
                stream.write(json.dumps(task,ensure_ascii=False,default=str))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,target)
        except OSError:
            self.snapshot_errors += 1
            LOGGER.exception('SQLite committed; task snapshot failed task_id=%s',task['task_id'])
            try: temporary.unlink(missing_ok=True)
            except OSError: LOGGER.warning('Unable to remove failed snapshot temporary file')

    def _read(self, task_id: str) -> dict[str, Any] | None:
        if not isinstance(task_id,str) or not re.fullmatch(r'[a-f0-9]{32}',task_id):
            return None
        return self.index.get(task_id)

    def create(self, stage: str, product_id: str, request_id: str | None = None,
               *, task_id: str | None = None, **metadata) -> dict[str, Any]:
        if request_id is None:
            from flask import g,has_request_context
            if has_request_context() and getattr(g,'idempotency_context',None):
                request_id=g.idempotency_context[2]
        if stage not in STAGE_NAMES:
            raise ValueError("stage 必须是 stage1、stage2、stage3 或 automl")
        if not isinstance(product_id,str) or not 1 <= len(product_id.strip()) <= 200:
            raise ValueError('product_id 长度必须在 1..200')
        if request_id is not None and (not isinstance(request_id,str) or not 1 <= len(request_id.strip()) <= 200):
            raise ValueError('request_id 长度必须在 1..200')
        if task_id is not None and not re.fullmatch(r'[a-f0-9]{32}',task_id):
            raise ValueError('task_id 格式无效')
        timestamp = now()
        task = dict(task_id=task_id or uuid.uuid4().hex, request_id=request_id or uuid.uuid4().hex,
                    product_id=product_id, stage=stage, stage_name=STAGE_NAMES[stage],
                    status="PENDING", progress=0, current_file=None, file_index=0, file_total=0,
                    current_channel=None, channel_index=0, channel_total=0, operation="create",
                    start_time=timestamp, update_time=timestamp, finish_time=None, result=None,
                    error_code=None, error_message=None, revision=0, **metadata)
        with self._lock:
            if self.index.get(task['task_id']):
                raise ValueError('task_id 已存在')
            from services.idempotency import link_task_receipt
            with self.index.connect() as db:
                self.index.write_task(db,task)
                link_task_receipt(db,task)
            self._snapshot(task)
        self.safe_log(task["task_id"], "INFO", "create", "任务已创建")
        return dict(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._read(task_id)
            return copy.deepcopy(task) if task else None

    def all_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            with self.index.connect() as db:
                return [json.loads(row[0]) for row in db.execute("SELECT task_json FROM tasks WHERE stage='stage1' AND task_json IS NOT NULL AND (status IN ('PENDING','QUEUED','RUNNING') OR json_extract(task_json,'$.error_code')='FRAME_STORAGE_FAILED')")]

    def find_request(self, stage: str, request_id: str) -> dict[str, Any] | None:
        task_id = self.index.find_request(stage, request_id)
        return self.get(task_id) if task_id else None

    def update(self, task_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            with self.index.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT task_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()
                if not row or not row[0]: raise KeyError(task_id)
                task=json.loads(row[0])
                if task['status'] in TERMINAL:
                    return copy.deepcopy(task)  # Late callbacks cannot alter a terminal record or its result.
                expected=values.pop('expected_revision',None)
                if expected is not None and task.get('revision',0)!=expected:
                    raise ValueError('任务版本冲突')
                validate_transition(task['status'],values.get('status',task['status']))
                task.update(values)
                task['progress']=max(0,min(100,int(task['progress'])))
                task['update_time']=now(); task['revision']=task.get('revision',0)+1
                if task['status'] in TERMINAL and not task['finish_time']:
                    task['finish_time']=task['update_time']
                self.index.write_task(db,task)
            self._snapshot(task)
            return copy.deepcopy(task)

    def safe_log(self, *args, **kwargs):
        try:
            return self.log(*args,**kwargs)
        except Exception:
            LOGGER.exception('Operational event persistence failed')
            return None

    def log(self, task_id: str, level: str, operation: str, message: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            task = self._read(task_id)
            if task is None:
                raise KeyError(task_id)
            entry = dict(event_id=uuid.uuid4().hex, timestamp=now(), level=level, request_id=task["request_id"],
                         task_id=task_id, product_id=task["product_id"], stage=task["stage"],
                         stage_name=task["stage_name"], file_name=task["current_file"],
                         file_path=None, channel=task["current_channel"], model_name=None,
                         model_version=None, recipe_version=None, operation=operation,
                         status=task["status"], progress=task["progress"], duration_ms=None,
                         message=message, error_code=task["error_code"], exception=None)
            entry.update(values)
            self.index.append_event(entry)
            audit_path=self.log_dir / f'{task_id}.jsonl'
            try:
                if audit_path.exists() and audit_path.stat().st_size >= 32*1024*1024:
                    raise OSError('任务 JSONL 审计副本已达到 32 MiB 上限，请归档')
                with audit_path.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(entry,ensure_ascii=False,default=str)+'\n')
            except OSError:
                self.audit_mirror_errors += 1
                LOGGER.exception('SQLite event committed; JSONL mirror unavailable')
            LOGGER.log(getattr(logging, level, logging.INFO),
                       "task_id=%s stage=%s file=%s channel=%s operation=%s %s",
                       task_id, task["stage"], entry["file_name"], entry["channel"], operation, message)
            return entry

    def logs(self, task_id: str, stage: str | None = None, limit: int = 200,
             offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if self.get(task_id) is None:
            raise KeyError(task_id)
        return self.index.logs(task_id, stage, limit, offset)
