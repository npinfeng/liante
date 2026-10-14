"""ThreadPoolExecutor-backed task manager with replaceable boundaries."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import CancelledError, Future
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from config import TRAINING_WAITING
from services.admission import BoundedExecutor, CapacityError


LOGGER = logging.getLogger(__name__)
TASK_STATUSES = {
    "queued",
    "preparing",
    "training",
    "retraining",
    "evaluating",
    "saving",
    "completed",
    "failed",
    "cancelled",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskRecord:
    task_id: str
    status: str
    channel: int | None
    current_trial: int
    total_trials: int
    progress: int
    best_score: float | None
    message: str
    error: str | None = None
    result: Any = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def public_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("result", None)
        return values


TaskRunner = Callable[[Callable[..., None]], Any]


class TrainingTaskManager:
    """Run GPU-heavy jobs serially by default without blocking HTTP requests."""

    def __init__(self, max_concurrent_jobs: int = 1, max_waiting_jobs: int = TRAINING_WAITING, store=None) -> None:
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs 必须大于 0")
        self._executor = BoundedExecutor(max_concurrent_jobs, max_waiting_jobs, "liante-automl")
        self._tasks: dict[str, TaskRecord] = {}
        self._futures: dict[str, Future] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self.store=store

    def _persist_record(self,record):
        if self.store is None: return
        canonical={'queued':'QUEUED','completed':'COMPLETED','failed':'FAILED','cancelled':'CANCELLED'}.get(record.status,'RUNNING')
        task=self.store.get(record.task_id)
        if canonical=='COMPLETED' and task['status'] in {'PENDING','QUEUED'}:
            self.store.update(record.task_id,status='RUNNING')
        self.store.update(record.task_id,status=canonical,progress=record.progress,
                          channel=record.channel,operation=record.status,
                          training_record=record.public_dict(),result=record.result,
                          error_code='TRAINING_FAILED' if record.error else None,error_message=record.error)
        self.store.safe_log(record.task_id,'ERROR' if record.error else 'INFO',record.status,record.message)

    def submit(self, channel: int | None, total_trials: int, runner: TaskRunner) -> str:
        reservation=self._executor.reserve()
        task_id=None
        try:
            task_id = self.store.create('automl',f'CH{channel}' if channel is not None else 'MULTI-CHANNEL')['task_id'] if self.store else uuid.uuid4().hex
            record = TaskRecord(
                task_id=task_id,
                status="queued",
                channel=channel,
                current_trial=0,
                total_trials=total_trials,
                progress=0,
                best_score=None,
                message="Training task queued",
            )
            with self._lock:
                self._tasks[task_id] = record
                self._persist_record(record)
            future = self._executor.submit_reserved(reservation,self._execute, task_id, runner)
            with self._lock:
                self._futures[task_id] = future
            future.add_done_callback(lambda _: self._forget(task_id))
        except Exception:
            reservation.release()
            with self._lock:
                self._tasks.pop(task_id, None)
            if self.store and task_id:
                self.store.update(task_id,status='FAILED',error_code='QUEUE_SUBMISSION_FAILED',error_message='训练队列提交失败')
            raise
        return task_id

    def capacity(self) -> dict[str, int | float]:
        return self._executor.stats()

    def _forget(self, task_id: str) -> None:
        with self._lock:
            self._futures.pop(task_id, None)
            self._cancelled.discard(task_id)
            if self.store:
                self._tasks.pop(task_id,None)
            elif len(self._tasks)>1000:
                for key,record in list(self._tasks.items()):
                    if key not in self._futures and record.status in {'completed','failed','cancelled'}:
                        self._tasks.pop(key,None)
                        if len(self._tasks)<=1000: break

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
            future = self._futures.get(task_id)
            if record is None or future is None or record.status in {"completed", "failed", "cancelled"}:
                return False
            self._cancelled.add(task_id)
            record.status = "cancelled"
            record.message = "Training cancelled"
            record.updated_at = _now()
            future.cancel()
            self._persist_record(record)
            return True

    def _execute(self, task_id: str, runner: TaskRunner) -> None:
        try:
            result = runner(lambda **values: self.update(task_id, **values))
            with self._lock:
                record = self._tasks[task_id]
                if record.status == "cancelled":
                    return
                record.status = "completed"
                record.progress = 100
                record.result = result
                record.message = "Training completed"
                record.updated_at = _now()
                self._persist_record(record)
        except CancelledError:
            return
        except Exception as exc:
            LOGGER.exception("training task failed task_id=%s", task_id)
            with self._lock:
                record = self._tasks[task_id]
                if record.status == "cancelled":
                    return
                record.status = "failed"
                record.error = str(exc)
                record.message = f"Training failed: {exc}"
                record.updated_at = _now()
                self._persist_record(record)

    def update(self, task_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "channel",
            "current_trial",
            "total_trials",
            "progress",
            "best_score",
            "message",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"不支持的任务字段: {sorted(unknown)}")
        if "status" in values and values["status"] not in TASK_STATUSES:
            raise ValueError(f"不支持的任务状态: {values['status']}")
        with self._lock:
            if task_id in self._cancelled:
                raise CancelledError()
            record = self._tasks[task_id]
            for key, value in values.items():
                setattr(record, key, value)
            record.progress = max(0, min(100, int(record.progress)))
            record.updated_at = _now()
            self._persist_record(record)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record: return record.public_dict()
            task=self.store.get(task_id) if self.store else None
            if task and task['stage']=='automl':
                return {**(task.get('training_record') or {}),'task_id':task_id,'status':task['status'].lower(),
                        'error':task.get('error_message'),'progress':task['progress']}
            return None

    def get_result(self, task_id: str) -> tuple[str, Any, str | None] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                task=self.store.get(task_id) if self.store else None
                if task and task['stage']=='automl': return task['status'].lower(),task['result'],task['error_message']
                return None
            return record.status, record.result, record.error

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

