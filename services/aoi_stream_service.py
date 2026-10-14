"""Bounded, traceable AOI frame ingestion for push-based equipment streams."""

from __future__ import annotations

import hashlib
import io
import logging
import sqlite3
import time
import os
import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from threading import RLock, Thread, Timer
from typing import Any

from PIL import Image

from services.inference_service import InferenceError, ModelAdapterService
from services.frame_journal import FrameJournal, FrameJournalFull
from tasks.inference_store import InferenceTaskStore


LOGGER = logging.getLogger(__name__)
IMAGE_HEADERS = ((b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
                 (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
                 (b"BM", "image/bmp", ".bmp"),
                 (b"II*\x00", "image/tiff", ".tif"),
                 (b"MM\x00*", "image/tiff", ".tif"))


class StreamError(Exception):
    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


@dataclass
class Frame:
    sequence: int
    product_id: str
    data: bytes
    mime_type: str
    suffix: str
    checksum: str
    view_type: str = 'UNSPECIFIED'
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class StreamState:
    task_id: str
    product_id: str
    source_id: str
    queue: Queue[Frame]
    lock: RLock = field(default_factory=RLock)
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    accepted: int = 0
    processed: int = 0
    failed: int = 0
    rejected: int = 0
    last_sequence: int = 0
    latest_frame: bytes | None = None
    latest_mime: str | None = None
    closed: bool = False
    timed_out: bool = False
    in_flight: bool = False
    current_sequence: int | None = None
    frame_timed_out: bool = False
    folder_error: str | None = None
    recovering: bool = False
    started_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    last_persist_at: float = 0.0
    last_backpressure_log_at: float = 0.0


class AOIStreamManager:
    def __init__(self, store: InferenceTaskStore, adapter: ModelAdapterService,
                 data_dir: Path, max_streams: int = 4, queue_capacity: int = 8,
                 max_frame_bytes: int = 2 * 1024 * 1024,
                 idle_timeout_seconds: int = 300, retention_days: int = 30,
                 max_pixels: int = 20_000_000, frame_timeout_seconds: float = 30,
                 max_journal_bytes: int = 20 * 1024 * 1024 * 1024) -> None:
        self.store = store
        self.adapter = adapter
        self.data_dir = Path(data_dir)
        self.max_streams = max(1, max_streams)
        self.queue_capacity = max(1, queue_capacity)
        self.max_frame_bytes = max(1024, max_frame_bytes)
        self.idle_timeout_seconds = max(1, idle_timeout_seconds)
        self.max_pixels = max(1, max_pixels)
        self.frame_timeout_seconds = max(1, frame_timeout_seconds)
        self._lock = RLock()
        self._streams: dict[str, StreamState] = {}
        self._folder_runs: dict[str, dict[str, Any]] = {}
        self.journal = FrameJournal(self.data_dir, retention_days, max_journal_bytes)
        self.journal.prune()
        self.capacity_monitor=None
        self._recover()

    def _recover(self) -> None:
        # Upgrade legacy streams without loading frame BLOBs. Journal is the ACK authority.
        for task in self.store.all_tasks():
            summary = task.get("result")
            if task.get('stage')!='stage1' or not isinstance(summary,dict) or summary.get('stream_id')!=task['task_id']:
                continue
            closed=task['status'] in {'COMPLETED','FAILED','CANCELLED','TIMEOUT'} or not summary.get('accepting',True)
            self.journal.register_stream(task['task_id'],dict(product_id=task['product_id'],
                source_id=summary.get('source_id','unattributed'),request_id=task['request_id'],
                owner_device_id=task.get('owner_device_id'),view_types=['UNSPECIFIED'],required_views=['UNSPECIFIED']),closed)
        with self.journal._connect() as db:
            ids=[row[0] for row in db.execute('SELECT DISTINCT stream_id FROM frames')]
        for stream_id in ids:
            metadata=self.journal.stream(stream_id)
            if metadata is None:
                parent=self.store.get(stream_id)
                summary=(parent or {}).get('result') or {}
                trusted=parent and parent['stage']=='stage1' and summary.get('stream_id')==stream_id
                self.journal.register_stream(stream_id,dict(product_id=parent['product_id'] if trusted else 'UNATTRIBUTED',
                    source_id=summary.get('source_id','unattributed'),request_id=(parent or {}).get('request_id'),
                    owner_device_id=(parent or {}).get('owner_device_id'),
                    view_types=['UNSPECIFIED'],required_views=['UNSPECIFIED'],orphaned=not trusted),True)
                metadata=self.journal.stream(stream_id)
            with self.journal._connect() as db:
                product_ids=[row[0] for row in db.execute('SELECT DISTINCT product_id FROM frames WHERE stream_id=?',(stream_id,))]
            for product_id in product_ids:
                if self.journal.product(stream_id,product_id) is None:
                    self.journal.open_product(stream_id,product_id,metadata.get('required_views',['UNSPECIFIED']))
                if metadata['closed']: self.journal.close_product(stream_id,product_id)
        self._recovery_backlog=deque(self.journal.recovery_streams())
        self._launch_recoveries()

    def _launch_recoveries(self):
        with self._lock:
            while self._recovery_backlog and len(self._streams)<self.max_streams:
                stream_id=self._recovery_backlog.popleft()
                metadata=self.journal.stream(stream_id)
                task=self.store.get(stream_id)
                if task is None:
                    task=self.store.create('stage1',metadata.get('product_id','UNATTRIBUTED'),
                        metadata.get('request_id'),task_id=stream_id,source_id=metadata['source_id'],
                        owner_device_id=metadata.get('owner_device_id'),recovered_from_journal=True)
                    self.store.update(stream_id,status='RUNNING',result=dict(stream_id=stream_id,source_id=metadata['source_id']))
                total, processed, failed, last, recent = self.journal.stats(stream_id)
                state = StreamState(task_id=stream_id, product_id=metadata.get('product_id','UNATTRIBUTED'),
                                source_id=metadata['source_id'], queue=Queue(maxsize=self.queue_capacity),
                                recent=deque(recent, maxlen=20), accepted=total, processed=processed,
                                failed=failed, rejected=(task.get('result') or {}).get('rejected',0), last_sequence=last,
                                closed=metadata['closed'] or task['status'] in {'FAILED','COMPLETED','CANCELLED','TIMEOUT'})
                state.recovering=True
                latest=self.journal.get(stream_id,last) if last else None
                if latest: state.latest_frame,state.latest_mime=latest['data'],latest['mime_type']
                self._streams[stream_id]=state
                self._publish(state,'recovered')
                self.store.safe_log(stream_id,'WARNING','recovered','从 AOI journal 恢复已确认帧；原终态保持不变')
                Thread(target=self._worker,args=(state,),name=f'aoi-{stream_id[:8]}',daemon=True).start()

    def health(self) -> dict[str, int]:
        with self._lock:
            depths = []
            ages = []
            rejected = processed = failed = 0
            durations = []
            for state in self._streams.values():
                rejected += state.rejected
                processed += state.processed
                failed += state.failed
                durations.extend(record.get("duration_ms", 0) for record in state.recent)
                with state.queue.mutex:
                    depths.append(len(state.queue.queue))
                    if state.queue.queue:
                        ages.append(max(0.0, time.monotonic() - state.queue.queue[0].enqueued_at))
            return dict(active_streams=len(self._streams), max_streams=self.max_streams,
                        queue_capacity=self.queue_capacity, max_frame_bytes=self.max_frame_bytes,
                        idle_timeout_seconds=self.idle_timeout_seconds,
                        queued_frames=sum(depths), oldest_queued_seconds=round(max(ages, default=0), 2),
                        rejected_frames=rejected, processed_frames=processed, failed_frames=failed,
                        rejection_rate=round(rejected / max(1, rejected + sum(
                            state.accepted for state in self._streams.values())), 4),
                        average_frame_ms=round(sum(durations) / len(durations), 2) if durations else 0,
                        **self.journal.usage())

    def _summary(self, state: StreamState) -> dict[str, Any]:
        elapsed = max(time.monotonic() - state.started_at, 0.001)
        return dict(stream_id=state.task_id, source_id=state.source_id,
                    accepted=state.accepted, processed=state.processed,
                    failed=state.failed, rejected=state.rejected,
                    queue_depth=state.queue.qsize(), queue_capacity=self.queue_capacity,
                    in_flight=state.in_flight, last_sequence=state.last_sequence,
                    latest_sequence=state.last_sequence if state.latest_frame else None,
                    processing_fps=round(state.processed / elapsed, 2),
                    accepting=not state.closed, timed_out=state.timed_out,
                    recent_frames=list(state.recent))

    def _publish(self, state: StreamState, operation: str, **values: Any) -> dict[str, Any]:
        # Caller holds state.lock so counters and persisted snapshots stay ordered.
        summary = self._summary(state)
        now = time.monotonic()
        if operation not in {"queue_frame", "inference"} or state.accepted == 1 or now - state.last_persist_at >= 0.25:
            self.store.update(state.task_id, operation=operation, result=summary, **values)
            state.last_persist_at = now
        return summary

    def start(self, product_id: str, source_id: str, request_id: str | None = None,
              view_types=None,required_views=None) -> dict[str, Any]:
        view_types=view_types if view_types is not None else json.loads(os.getenv('AOI_VIEW_TYPES','["UNSPECIFIED"]'))
        required_views=required_views if required_views is not None else json.loads(os.getenv('AOI_REQUIRED_VIEWS',json.dumps(view_types)))
        self.validate_views(view_types,required_views)
        with self._lock:
            if any(stream.source_id == source_id for stream in self._streams.values()):
                raise StreamError("SOURCE_BUSY", "此设备已有活动 AOI 流", 409)
            if len(self._streams) >= self.max_streams:
                raise StreamError("STREAM_CAPACITY_REACHED", "AOI 流数量已达上限，请稍后重试", 429, 1)
            task = self.store.create("stage1", product_id, request_id,source_id=source_id,owner_device_id=source_id)
            self.journal.register_stream(task['task_id'],dict(product_id=product_id,source_id=source_id,
                owner_device_id=source_id,request_id=task['request_id'],view_types=view_types,required_views=required_views))
            state = StreamState(task_id=task["task_id"], product_id=product_id,
                                source_id=source_id, queue=Queue(maxsize=self.queue_capacity))
            self._streams[state.task_id] = state
            with state.lock:
                summary = self._publish(state, "stream_open", status="RUNNING")
            self.store.safe_log(state.task_id, "INFO", "stream_open", f"AOI 流已建立 source_id={source_id}")
            Thread(target=self._worker, args=(state,), name=f"aoi-{state.task_id[:8]}", daemon=True).start()
            return dict(task_id=state.task_id, status="RUNNING", stream=summary)

    @staticmethod
    def validate_views(view_types,required_views):
        if not isinstance(view_types,list) or not 1<=len(view_types)<=32 or any(
                not isinstance(v,str) or not 1<=len(v)<=64 for v in view_types) or len(set(view_types))!=len(view_types):
            raise StreamError('INVALID_ARGUMENT','view_types 必须是 1..32 个不重复的视角标识，每项 1..64 字符',400)
        if not isinstance(required_views,list) or not required_views or len(required_views)>32 or any(
                not isinstance(v,str) or v not in view_types for v in required_views) or len(set(required_views))!=len(required_views):
            raise StreamError('INVALID_ARGUMENT','required_views 必须是 view_types 的非空子集',400)

    def open_product(self,stream_id,product_id,required_views=None):
        state=self._active(stream_id)
        with state.lock:
            if state.closed: raise StreamError('STREAM_CLOSED','流已关闭',409)
            metadata=self.journal.stream(stream_id)
            required=required_views if required_views is not None else metadata['required_views']
            self.validate_views(metadata['view_types'],required)
            try: result=self.journal.open_product(stream_id,product_id,required)
            except ValueError as exc: raise StreamError('PRODUCT_CONFLICT',str(exc),409) from exc
            self.store.safe_log(stream_id,'INFO','product_open','产品开始接收帧',product_id=product_id,required_views=required)
            return result

    def close_product(self,stream_id,product_id):
        state=self._streams.get(stream_id)
        lock=state.lock if state else self._lock
        with lock:
            try: value=self.journal.close_product(stream_id,product_id)
            except KeyError as exc: raise StreamError('PRODUCT_NOT_FOUND','产品不存在',404) from exc
            self.store.safe_log(stream_id,'INFO','product_close','产品独立关闭，流继续接收其他产品',product_id=product_id,
                                status=value['status'],missing_views=value['missing_views'])
            return value

    def preflight(self, task_id: str) -> None:
        state = self._active(task_id)
        with state.lock:
            if state.recovering:
                raise StreamError('STREAM_RECOVERING','正在恢复已确认帧，请稍后提交新帧',429,1)
            if state.closed:
                raise StreamError("STREAM_CLOSED", "AOI 流已停止接收帧", 409)
            if state.queue.full():
                state.rejected += 1
                if time.monotonic() - state.last_backpressure_log_at >= 1:
                    self._publish(state, "backpressure")
                    self.store.log(task_id, "WARNING", "backpressure", "AOI 队列已满，上传前拒收",
                                   error_code="STREAM_BACKPRESSURE")
                    state.last_backpressure_log_at = time.monotonic()
                raise StreamError("STREAM_BACKPRESSURE", "AOI 队列已满，请稍后重试", 429, 1)

    def _active(self, task_id: str) -> StreamState:
        with self._lock:
            state = self._streams.get(task_id)
        if state is None:
            if self.store.get(task_id) is None:
                raise StreamError("STREAM_NOT_FOUND", "AOI 流不存在", 404)
            raise StreamError("STREAM_CLOSED", "AOI 流已停止", 409)
        return state

    def submit(self, task_id: str, sequence: int, data: bytes,
               product_id: str | None = None,view_type: str | None = None) -> dict[str, Any]:
        if not 1 <= len(data) <= self.max_frame_bytes:
            raise StreamError("INVALID_FRAME", "帧大小无效或超过上限", 413)
        image = next(((mime, suffix) for magic, mime, suffix in IMAGE_HEADERS
                      if data.startswith(magic)), None)
        if image is None:
            raise StreamError("INVALID_FILE_FORMAT", "仅接收 PNG/JPEG/BMP/TIFF 图片帧", 400)
        try:
            with Image.open(io.BytesIO(data)) as decoded:
                if decoded.width * decoded.height > self.max_pixels:
                    raise StreamError("INVALID_FRAME", "图片像素数超过上限", 413)
                expected_format = {".png": "PNG", ".jpg": "JPEG", ".bmp": "BMP", ".tif": "TIFF"}[image[1]]
                if decoded.format != expected_format:
                    raise StreamError("INVALID_FILE_FORMAT", "图片内容与文件头不一致", 400)
                decoded.verify()
        except StreamError:
            raise
        except Exception as exc:
            raise StreamError("INVALID_FRAME", "图片无法完整解码", 400) from exc
        metadata=self.journal.stream(task_id)
        if metadata is None: raise StreamError('STREAM_NOT_FOUND','AOI 流不存在',404)
        frame_product_id = product_id.strip() if isinstance(product_id, str) else metadata.get('product_id','')
        view_type=view_type or (metadata['view_types'][0] if len(metadata['view_types'])==1 else None)
        if view_type not in metadata['view_types']:
            raise StreamError('INVALID_VIEW_TYPE','view_type 必须是流配置的视角之一',400)
        checksum=hashlib.sha256(data).hexdigest()
        prior=self.journal.get(task_id,sequence)
        if prior:
            if prior['checksum']==checksum and prior['product_id']==frame_product_id and prior['view_type']==view_type:
                return dict(task_id=task_id,sequence=sequence,product_id=frame_product_id,view_type=view_type,
                            status=prior['status'],duplicate=True,replayed=True)
            raise StreamError('REQUEST_REPLAY_CONFLICT','相同帧序号的产品、图片校验和或视角发生变化',409)
        if self.capacity_monitor: self.capacity_monitor.admit()
        state = self._active(task_id)
        if not 1<=len(frame_product_id)<=200:
            raise StreamError("INVALID_ARGUMENT", "帧产品编号不能为空", 400)
        with state.lock:
            if state.recovering:
                raise StreamError('STREAM_RECOVERING','正在恢复已确认帧，请稍后提交新帧',429,1)
            if state.closed:
                raise StreamError("STREAM_CLOSED", "AOI 流已停止接收帧", 409)
            expected = state.last_sequence + 1
            if sequence != expected:
                if sequence <= state.last_sequence:
                    prior = self.journal.get(task_id, sequence)
                    if prior and prior["checksum"] == hashlib.sha256(data).hexdigest() and prior["product_id"] == frame_product_id:
                        return dict(task_id=task_id, sequence=sequence, product_id=frame_product_id,
                                    status=prior["status"], duplicate=True, stream=self._summary(state))
                raise StreamError("FRAME_SEQUENCE_ERROR", f"帧序号必须为 {expected}", 409)
            frame = Frame(sequence, frame_product_id, data, image[0], image[1], checksum,view_type)
            try:
                if state.queue.full():
                    raise Full
                self.journal.insert(task_id, frame)
                state.queue.put_nowait(frame)
            except Full as exc:
                state.rejected += 1
                summary = self._publish(state, "backpressure")
                self.store.log(task_id, "WARNING", "backpressure",
                               f"队列满：帧 {sequence} 被拒收，发送方应重试；depth={summary['queue_depth']}",
                               error_code="STREAM_BACKPRESSURE")
                raise StreamError("STREAM_BACKPRESSURE", "AOI 队列已满，请按 Retry-After 重试同一帧序号", 429, 1) from exc
            except (OSError, sqlite3.Error) as exc:
                LOGGER.exception("AOI frame journal unavailable")
                raise StreamError("FRAME_STORAGE_FAILED", "帧无法可靠保存，未接收", 503, 2) from exc
            except FrameJournalFull as exc:
                raise StreamError("FRAME_STORAGE_FULL", "AOI 帧存储达到容量上限，未接收", 507, 60) from exc
            except ValueError as exc:
                raise StreamError('PRODUCT_CLOSED',str(exc),409) from exc
            state.accepted += 1
            state.last_sequence = sequence
            state.last_activity_at = time.monotonic()
            state.latest_frame = data
            state.latest_mime = image[0]
            try: summary = self._publish(state, "queue_frame")
            except Exception:
                LOGGER.exception('Journal ACK durable; task summary unavailable')
                summary=self._summary(state)
            accepted = dict(task_id=task_id, sequence=sequence, product_id=frame.product_id,
                            status="QUEUED", view_type=view_type,sha256=checksum,stream=summary)
        try:
            self.store.log(task_id, "INFO", "queue_frame",
                           f"接收帧 {sequence} product_id={frame.product_id} sha256={frame.checksum}",
                           product_id=frame.product_id, frame_sequence=sequence, sha256=frame.checksum)
        except Exception:
            LOGGER.exception("AOI frame accepted but audit log write failed stream=%s sequence=%s", task_id, sequence)
        return accepted

    @staticmethod
    def folder_frames(directory: Path, max_frame_bytes: int, max_files: int = 2000):
        """Sort at most 2000 filenames, then yield one bounded image body at a time."""
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False) or Path(entry.name).suffix.lower() not in {
                    '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'
                }:
                    continue
                names.append(entry.name)
                if len(names) > max_files:
                    raise StreamError('FOLDER_LIMIT', f'单次最多回放 {max_files} 张图片', 400)
        for name in sorted(names):
            path = directory / name
            if path.is_symlink():
                raise StreamError('PATH_FORBIDDEN', '回放目录不能包含图片符号链接', 403)
            with path.open('rb') as source:
                data = source.read(max_frame_bytes + 1)
            yield name, data

    def start_folder_replay(self, directory: Path, product_id: str, source_id: str,
                            interval_seconds: float = 1.0, request_id: str | None = None) -> dict[str, Any]:
        if not directory.is_dir():
            raise StreamError('FOLDER_NOT_FOUND', '图片文件夹不存在', 404)
        if not 0.1 <= interval_seconds <= 10:
            raise StreamError('INVALID_ARGUMENT', '逐张间隔必须在 0.1–10 秒之间', 400)
        count = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in {
                        '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}:
                    count += 1
                    if count > 2000:
                        raise StreamError('FOLDER_LIMIT', '单次最多回放 2000 张图片', 400)
        if not count:
            raise StreamError('FOLDER_EMPTY', '文件夹中没有支持的图片', 400)
        opened = self.start(product_id, source_id, request_id)
        task_id = opened['task_id']
        with self._lock:
            if len(self._folder_runs) >= 100:
                for old_id, old_run in list(self._folder_runs.items()):
                    if old_run['state'] != 'RUNNING':
                        self._folder_runs.pop(old_id)
                        break
            self._folder_runs[task_id] = dict(directory=str(directory), current_file=None,
                                              submitted=0, state='RUNNING', error=None)
        Thread(target=self._replay_folder, args=(task_id, directory, product_id, interval_seconds),
               name=f'aoi-folder-{task_id[:8]}', daemon=True).start()
        return {**opened, 'folder_run': self.folder_run(task_id)}

    def folder_run(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._folder_runs.get(task_id)
            if run is None:
                raise StreamError('FOLDER_RUN_NOT_FOUND', '文件夹回放任务不存在', 404)
            return dict(run)

    def _replay_folder(self, task_id: str, directory: Path, product_id: str,
                       interval_seconds: float) -> None:
        stopped = False
        try:
            for filename, data in self.folder_frames(directory, self.max_frame_bytes):
                with self._lock:
                    state = self._streams.get(task_id)
                    if state is None or state.closed:
                        stopped = True
                        break
                    sequence = state.last_sequence + 1
                    self._folder_runs[task_id].update(current_file=filename)
                try:
                    self.submit(task_id, sequence, data, product_id)
                except StreamError as exc:
                    if exc.code == 'STREAM_CLOSED':
                        stopped = True
                        break
                    raise
                with self._lock:
                    self._folder_runs[task_id]['submitted'] = sequence
                self.store.safe_log(task_id, 'INFO', 'folder_frame', f'文件夹回放帧 {sequence}: {filename}',
                                    file_name=filename, frame_sequence=sequence)
                while True:
                    with self._lock:
                        state = self._streams.get(task_id)
                        if state is None or state.closed:
                            break
                    rows, _ = self.journal.records(task_id, 1, sequence - 1)
                    record = rows[0] if rows else None
                    if record and record['status'] in {'COMPLETED', 'FAILED'}:
                        break
                    time.sleep(0.05)
                if state is None or state.closed:
                    stopped = True
                    break
                time.sleep(interval_seconds)
            with self._lock:
                self._folder_runs[task_id]['state'] = 'STOPPED' if stopped else 'DONE'
        except Exception as exc:
            LOGGER.exception('AOI folder replay failed stream=%s', task_id)
            with self._lock:
                self._folder_runs[task_id].update(state='FAILED', error=str(exc))
                state = self._streams.get(task_id)
            if state:
                with state.lock:
                    state.folder_error = str(exc)
            self.store.safe_log(task_id, 'ERROR', 'folder_replay_failed', str(exc))
        finally:
            try:
                self.stop(task_id)
            except StreamError:
                pass

    def frame_records(self, task_id: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        if self.store.get(task_id) is None:
            raise StreamError("STREAM_NOT_FOUND", "AOI 流不存在", 404)
        return self.journal.records(task_id, limit, offset)

    def latest_frame(self, task_id: str) -> tuple[bytes, str, int]:
        state = self._active(task_id)
        with state.lock:
            if state.latest_frame is None or state.latest_mime is None:
                raise StreamError("FRAME_NOT_FOUND", "尚未接收图片帧", 404)
            return state.latest_frame, state.latest_mime, state.last_sequence

    def stop(self, task_id: str) -> dict[str, Any]:
        metadata=self.journal.stream(task_id)
        if metadata and metadata['closed'] and task_id not in self._streams:
            return dict(task_id=task_id,status='CLOSED',replayed=True)
        state = self._active(task_id)
        with state.lock:
            self.journal.close_stream(task_id)
            if not state.closed:
                state.closed = True
                summary = self._publish(state, "drain")
                self.store.log(task_id, "INFO", "drain", "停止接收新帧，正在处理队列中的帧")
            else:
                summary = self._summary(state)
        return dict(task_id=task_id, status="DRAINING", stream=summary)

    def _worker(self, state: StreamState) -> None:
        fatal_error = False
        try:
            while True:
                queued_frame=True
                try:
                    if getattr(state,'recovering',False):
                        saved=self.journal.pending(state.task_id,1)
                        if saved:
                            row=saved[0];queued_frame=False
                            frame=Frame(row['sequence'],row['product_id'],row['data'],row['mime_type'],row['suffix'],row['checksum'],row['view_type'])
                        else:
                            state.recovering=False
                            frame=state.queue.get(timeout=0.1)
                    else:
                        frame = state.queue.get(timeout=0.1)
                except Empty:
                    queued_frame=False
                    with state.lock:
                        if not state.queue.empty(): continue
                        saved=self.journal.pending(state.task_id,1)
                        if saved:
                            row=saved[0]
                            frame=Frame(row['sequence'],row['product_id'],row['data'],row['mime_type'],row['suffix'],row['checksum'],row['view_type'])
                        elif state.closed:
                            break
                        elif time.monotonic() - state.last_activity_at >= self.idle_timeout_seconds:
                            state.closed = True
                            state.timed_out = True
                            self._publish(state, "idle_timeout")
                            self.store.log(state.task_id, "WARNING", "idle_timeout",
                                           "AOI 设备流超时：长期没有新帧", error_code="STREAM_IDLE_TIMEOUT")
                            break
                        else: continue
                with state.lock:
                    state.in_flight = True
                    state.current_sequence = frame.sequence
                    self._publish(state, "inference")
                self.journal.mark_running(state.task_id, frame.sequence)
                started = time.monotonic()
                path = self.data_dir / "aoi_stream_work" / state.task_id / f"{frame.sequence}{frame.suffix}"
                def frame_expired() -> None:
                    with state.lock:
                        if state.current_sequence != frame.sequence:
                            return
                        state.frame_timed_out = True
                        state.closed = True
                    self.store.update(state.task_id, status="FAILED", error_code="FRAME_TIMEOUT",
                                      error_message="AOI 模型处理帧超过时间上限")
                    self.store.log(state.task_id, "ERROR", "frame_timeout",
                                   f"帧 {frame.sequence} 模型调用超过时间上限", frame_sequence=frame.sequence,
                                   error_code="FRAME_TIMEOUT")
                watchdog = Timer(self.frame_timeout_seconds, frame_expired)
                watchdog.daemon = True
                watchdog.start()
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(frame.data)
                    if (self.journal.stream(state.task_id) or {}).get('orphaned'):
                        raise InferenceError('STORAGE_PARENT_MISSING','帧来源元数据缺失，记录失败供人工追溯',503)
                    output = self.adapter.predict(dict(product_id=frame.product_id,
                                                       stream_id=state.task_id, source_id=state.source_id,
                                                       frame_sequence=frame.sequence, view_type=frame.view_type,image_path=str(path),
                                                       sha256=frame.checksum))
                    if time.monotonic() - started > self.frame_timeout_seconds:
                        raise InferenceError("FRAME_TIMEOUT", "AOI 模型处理该帧超过时间上限", 504)
                    if not isinstance(output, dict):
                        raise InferenceError("INVALID_MODEL_OUTPUT", "AOI 模型结果必须为对象", 500)
                    model_info = self.adapter.get_model_info() if callable(
                        getattr(self.adapter, "get_model_info", None)) else {}
                    record = dict(sequence=frame.sequence, product_id=frame.product_id,
                                  status="COMPLETED", sha256=frame.checksum,
                                  duration_ms=int((time.monotonic() - started) * 1000), output=output,
                                  model_name=model_info.get("model_name"),
                                  model_version=model_info.get("model_version"),
                                  recipe_version=model_info.get("recipe_version"))
                    level, operation, message = "INFO", "frame_complete", f"帧 {frame.sequence} 识别完成"
                except Exception as exc:
                    code = exc.code if isinstance(exc, InferenceError) else "INFERENCE_FAILED"
                    message = str(exc) if isinstance(exc, InferenceError) else "AOI 帧处理失败，请查看服务日志"
                    if not isinstance(exc, InferenceError):
                        LOGGER.exception("AOI frame failed stream=%s sequence=%s", state.task_id, frame.sequence)
                    record = dict(sequence=frame.sequence, product_id=frame.product_id,
                                  status="FAILED", sha256=frame.checksum,
                                  duration_ms=int((time.monotonic() - started) * 1000),
                                  error_code=code, error_message=message)
                    level, operation = "ERROR", "frame_failed"
                finally:
                    watchdog.cancel()
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        LOGGER.exception("Could not remove transient AOI frame %s", path)
                self.journal.finish(state.task_id, frame.sequence, record)
                with state.lock:
                    state.in_flight = False
                    state.current_sequence = None
                    state.processed += 1
                    state.last_activity_at = time.monotonic()
                    state.failed += int(record["status"] == "FAILED")
                    state.recent.append(record)
                    if queued_frame: state.queue.task_done()
                    self._publish(state, operation)
                self.store.safe_log(state.task_id, level, operation, message,
                               product_id=frame.product_id, frame_sequence=frame.sequence,
                               sha256=frame.checksum, error_code=record.get("error_code"),
                               duration_ms=record["duration_ms"])
        except Exception:
            fatal_error = True
            LOGGER.exception("AOI stream worker stopped unexpectedly stream=%s", state.task_id)
        finally:
            with state.lock:
                with self._lock:
                    self._streams.pop(state.task_id, None)
                if not fatal_error: self.journal.close_stream(state.task_id)
                final_status = "RUNNING" if fatal_error else "FAILED" if state.failed or state.timed_out or state.frame_timed_out or state.folder_error else "COMPLETED"
                final_code = ("FRAME_STORAGE_FAILED" if fatal_error else "FRAME_TIMEOUT" if state.frame_timed_out else "STREAM_IDLE_TIMEOUT" if state.timed_out
                              else "FOLDER_REPLAY_FAILED" if state.folder_error else "STREAM_FRAME_FAILED" if state.failed else None)
                final_message = ("AOI 模型处理帧超过时间上限" if state.frame_timed_out else
                                 "AOI 设备流长时间没有新帧" if state.timed_out else
                                 state.folder_error if state.folder_error else
                                 f"{state.failed} 帧处理失败，请查看帧结果和日志" if state.failed else None)
                if fatal_error:
                    final_message = "AOI 持久化或工作线程故障；重启服务后可恢复未完成帧"
                try:
                    self.store.log(state.task_id, "ERROR" if final_status == "FAILED" else "INFO", "stream_complete",
                                   f"AOI 流结束：接收 {state.accepted} 帧，处理 {state.processed} 帧，失败 {state.failed} 帧，拒收 {state.rejected} 次",
                                   error_code=final_code, status=final_status)
                    self._publish(state, "stream_complete", status=final_status,
                                  progress=100 if not fatal_error else state.processed * 100 // max(state.accepted, 1),
                                  error_code=final_code, error_message=final_message)
                except Exception:
                    LOGGER.exception("Could not persist AOI stream final state stream=%s", state.task_id)
            self._launch_recoveries()
