"""ThreadPoolExecutor-backed task manager with replaceable boundaries."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


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

    def __init__(self, max_concurrent_jobs: int = 1) -> None:
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs 必须大于 0")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent_jobs, thread_name_prefix="liante-automl"
        )
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()

    def submit(self, channel: int | None, total_trials: int, runner: TaskRunner) -> str:
        task_id = uuid.uuid4().hex
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
        self._executor.submit(self._execute, task_id, runner)
        return task_id

    def _execute(self, task_id: str, runner: TaskRunner) -> None:
        try:
            result = runner(lambda **values: self.update(task_id, **values))
            with self._lock:
                record = self._tasks[task_id]
                record.status = "completed"
                record.progress = 100
                record.result = result
                record.message = "Training completed"
                record.updated_at = _now()
        except Exception as exc:
            LOGGER.exception("training task failed task_id=%s", task_id)
            with self._lock:
                record = self._tasks[task_id]
                record.status = "failed"
                record.error = str(exc)
                record.message = f"Training failed: {exc}"
                record.updated_at = _now()

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
            record = self._tasks[task_id]
            for key, value in values.items():
                setattr(record, key, value)
            record.progress = max(0, min(100, int(record.progress)))
            record.updated_at = _now()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            return record.public_dict() if record else None

    def get_result(self, task_id: str) -> tuple[str, Any, str | None] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            return record.status, record.result, record.error

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

