"""Bounded admission for long-running in-process jobs."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class CapacityError(Exception):
    """The service cannot safely accept another job now."""


class Reservation:
    """A queue permit held while the caller creates durable task state."""

    def __init__(self, owner: "BoundedExecutor", token: object, submitted: float) -> None:
        self.owner = owner
        self.token = token
        self.submitted = submitted
        self._claimed = False
        self._released = False
        self._lock = threading.Lock()

    def claim(self) -> None:
        with self._lock:
            if self._claimed or self._released:
                raise RuntimeError("reservation has already been used")
            self._claimed = True

    def release(self) -> None:
        with self._lock:
            if self._claimed or self._released:
                return
            self._released = True
        self.owner._release(self.token)

    def complete(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self.owner._release(self.token)


class BoundedExecutor:
    def __init__(self, workers: int, waiting: int, name: str) -> None:
        if workers < 1 or waiting < 0:
            raise ValueError("workers must be positive and waiting nonnegative")
        self.workers = workers
        self.waiting_capacity = waiting
        self._permits = threading.BoundedSemaphore(workers + waiting)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._lock = threading.Lock()
        self._outstanding = 0
        self._started = 0
        self._wait_seconds = 0.0
        self._submissions: dict[object, float] = {}
        self._waiting_submissions: dict[object, float] = {}
        self._rejected = 0

    def submit(self, fn: Callable[..., Any], *args: Any,
               max_queue_wait_seconds: float | None = None,
               on_expired: Callable[[], None] | None = None,
               **kwargs: Any) -> Future[Any]:
        reservation = self.reserve()
        return self.submit_reserved(reservation, fn, *args,
                                    max_queue_wait_seconds=max_queue_wait_seconds,
                                    on_expired=on_expired, **kwargs)

    def reserve(self) -> Reservation:
        if not self._permits.acquire(blocking=False):
            with self._lock:
                self._rejected += 1
            raise CapacityError("job queue is full")
        submitted = time.monotonic()
        token = object()
        with self._lock:
            self._outstanding += 1
            self._submissions[token] = submitted
            self._waiting_submissions[token] = submitted
        return Reservation(self, token, submitted)

    def _release(self, token: object) -> None:
        with self._lock:
            self._outstanding -= 1
            self._submissions.pop(token, None)
            self._waiting_submissions.pop(token, None)
        self._permits.release()

    def submit_reserved(self, reservation: Reservation, fn: Callable[..., Any], *args: Any,
                        max_queue_wait_seconds: float | None = None,
                        on_expired: Callable[[], None] | None = None,
                        **kwargs: Any) -> Future[Any]:
        if reservation.owner is not self:
            raise ValueError("reservation belongs to another executor")
        reservation.claim()
        submitted = reservation.submitted

        def run() -> Any:
            with self._lock:
                self._started += 1
                self._wait_seconds += time.monotonic() - submitted
                self._waiting_submissions.pop(reservation.token, None)
            if max_queue_wait_seconds is not None and time.monotonic() - submitted > max_queue_wait_seconds:
                if on_expired is not None:
                    on_expired()
                return None
            return fn(*args, **kwargs)

        try:
            future = self._executor.submit(run)
            def finished(_future: Future[Any]) -> None:
                reservation.complete()
            future.add_done_callback(finished)
            return future
        except Exception:
            reservation.complete()
            raise

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "workers": self.workers,
                "waiting_capacity": self.waiting_capacity,
                "outstanding": self._outstanding,
                "waiting": max(0, self._outstanding - self.workers),
                "rejected": self._rejected,
                "oldest_waiting_seconds": round(time.monotonic() - min(self._waiting_submissions.values()), 2)
                if self._waiting_submissions else 0.0,
                "oldest_outstanding_seconds": round(time.monotonic() - min(self._submissions.values()), 2)
                if self._submissions else 0.0,
                "average_wait_seconds": round(self._wait_seconds / self._started, 2)
                if self._started else 0.0,
            }

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


class InferencePools:
    """Reserve a worker and waiting budget for each interactive stage."""

    def __init__(self, stage2_workers: int, stage3_workers: int,
                 stage2_waiting: int, stage3_waiting: int) -> None:
        self.stage2 = BoundedExecutor(stage2_workers, stage2_waiting, "liante-stage2")
        self.stage3 = BoundedExecutor(stage3_workers, stage3_waiting, "liante-stage3")

    def submit(self, stage: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        pool = self.stage2 if stage == "stage2" else self.stage3
        return pool.submit(fn, *args, **kwargs)

    def reserve(self, stage: str) -> Reservation:
        pool = self.stage2 if stage == "stage2" else self.stage3
        return pool.reserve()

    def submit_reserved(self, stage: str, reservation: Reservation,
                        fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        pool = self.stage2 if stage == "stage2" else self.stage3
        return pool.submit_reserved(reservation, fn, *args, **kwargs)

    def stats(self) -> dict[str, dict[str, int | float]]:
        return dict(stage2=self.stage2.stats(), stage3=self.stage3.stats())

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        self.stage2.shutdown(wait=wait, cancel_futures=cancel_futures)
        self.stage3.shutdown(wait=wait, cancel_futures=cancel_futures)
