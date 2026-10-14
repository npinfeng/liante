"""Bounded discovery of Stage2 input files in configured server directories."""

from __future__ import annotations

import os
import re
import time
from collections import deque
from pathlib import Path
from threading import BoundedSemaphore, RLock

from services.prediction_service import PredictionService
from services.stage2_source_store import Stage2SourceStore


CHANNEL_PATTERN = re.compile(r"(?:^|[^a-z0-9])(?:channel|ch)[_-]?([0-7])(?=$|[^a-z0-9])", re.I)


class FolderInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class FolderInputService:
    def __init__(self, prediction: PredictionService, directories: str,
                 max_entries: int = 10000, stable_seconds: float = 2, *,
                 db_path: Path | None = None, allowed_directories: str = "",
                 max_events: int = 20000, retention_days: int = 30):
        self.prediction = prediction
        self.roots = dict((f"input-{i}", root) for i, root in enumerate(dict.fromkeys(
            Path(value.strip()).expanduser().resolve() for value in directories.split(";") if value.strip()), 1))
        self.max_entries = max(1, max_entries)
        self.stable_seconds = max(0, stable_seconds)
        self._scan_slot = BoundedSemaphore(1)
        self.submission_slot = BoundedSemaphore(1)
        self._config_lock = RLock()
        self.allowed_roots = list(dict.fromkeys([*self.roots.values(), *(
            Path(value.strip()).expanduser().resolve() for value in allowed_directories.split(";") if value.strip())]))
        self.registry = Stage2SourceStore(db_path, self.roots, max_events, retention_days) if db_path else None
        for root in self.allowed_roots:
            if root not in prediction.allowed_dirs:
                prediction.allowed_dirs.append(root)

    def sources(self, device_id: str | None = None) -> list[dict]:
        values = self.registry.sources() if self.registry else [
            dict(source_id=key, directory=str(root), revision=1, owner_device_id=None)
            for key, root in self.roots.items()]
        return [{**value, "exists": Path(value["directory"]).is_dir()} for value in values
                if device_id is None or value["owner_device_id"] == device_id]

    def source(self, source_id: str, device_id: str | None = None) -> dict:
        value = self.registry.get(source_id) if self.registry else next(
            (item for item in self.sources() if item["source_id"] == source_id), None)
        if value is None:
            raise FolderInputError("SOURCE_NOT_FOUND", "数据源不存在，请先登记文件夹", 404)
        if device_id is not None and value["owner_device_id"] != device_id:
            raise FolderInputError("SOURCE_FORBIDDEN", "MES 无权访问其他来源", 403)
        return value

    def checked_directory(self, directory: str) -> Path:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise FolderInputError("INVALID_DIRECTORY", "请使用 C209 可访问的绝对路径或共享目录路径")
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise FolderInputError("PATH_FORBIDDEN", "目录不在 Stage2 允许范围内，请由管理员配置 STAGE2_ALLOWED_DIRS", 403)
        if not path.is_dir():
            raise FolderInputError("DIRECTORY_NOT_FOUND", "输入目录不存在或服务账号无法访问", 404)
        return path

    def configure(self, source_id: str, directory: str, *, device_id: str | None = None,
                  owner_device_id: str | None = None, expected_revision: int | None = None) -> tuple[dict, bool, dict | None]:
        with self._config_lock:
            previous = self.registry.get(source_id)
            if previous and device_id is not None and previous["owner_device_id"] != device_id:
                raise FolderInputError("SOURCE_FORBIDDEN", "MES 无权修改其他来源", 403)
            if device_id is not None and owner_device_id not in (None, device_id):
                raise FolderInputError("SOURCE_FORBIDDEN", "MES 只能登记自己的来源", 403)
            if expected_revision is not None and expected_revision != (previous["revision"] if previous else 0):
                raise FolderInputError("SOURCE_VERSION_CONFLICT", "来源配置已变化，请读取最新 revision 后重试", 409)
            root = self.checked_directory(directory)
            owner = device_id or owner_device_id or (previous["owner_device_id"] if previous else None)
            if previous and previous["directory"] == str(root) and previous["owner_device_id"] == owner:
                return previous, False, previous
            if previous is None and len(self.registry.sources()) >= 100:
                raise FolderInputError("SOURCE_CAPACITY_REACHED", "最多登记 100 个数据源", 429)
            value = self.registry.save(source_id, root, owner, previous["revision"] + 1 if previous else 1)
            return value, previous is None, previous

    @staticmethod
    def infer_channel(relative: Path) -> int:
        channels = {int(match.group(1)) for part in relative.parts
                    for match in CHANNEL_PATTERN.finditer(part)}
        if len(channels) != 1:
            raise FolderInputError("CHANNEL_NOT_IDENTIFIED", "文件名或子目录需包含唯一的 channel_0–channel_7 / CH0–CH7")
        return channels.pop()

    def scan(self, source_id: str, recursive: bool = True, device_id: str | None = None) -> dict:
        source = self.source(source_id, device_id)
        # Recheck after every restart and configuration change; old permissions are not retained.
        root = self.checked_directory(source["directory"])
        if not root.is_dir():
            raise FolderInputError("DIRECTORY_NOT_FOUND", f"输入文件夹不存在：{root}", 404)
        if not self._scan_slot.acquire(blocking=False):
            raise FolderInputError("SCAN_BUSY", "正在识别文件夹，请稍后重试", 429)
        try:
            files, ignored, visited = [], 0, 0
            folders = deque([(root, 0)])
            models = set(self.prediction.available_channels())
            while folders:
                folder, depth = folders.popleft()
                with os.scandir(folder) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > self.max_entries:
                            raise FolderInputError("SCAN_LIMIT_EXCEEDED", "文件夹条目超过扫描上限，请归档旧文件或拆分输入文件夹", 413)
                        stat = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                            ignored += 1
                            continue
                        path = Path(entry.path).resolve()
                        if not path.is_relative_to(root):
                            ignored += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                if depth >= 8:
                                    raise FolderInputError("SCAN_LIMIT_EXCEEDED", "子目录层级超过 8 层", 413)
                                folders.append((path, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in {".csv", ".xlsx"} or entry.name.startswith(("~$", ".")):
                            ignored += 1
                            continue
                        if len(files) >= 100:
                            raise FolderInputError("SCAN_LIMIT_EXCEEDED", "一次最多识别 100 个数据文件，请拆分文件夹", 413)
                        relative = path.relative_to(root)
                        item = dict(file_name=str(relative), file_path=str(path), size_bytes=stat.st_size,
                                    mtime_ns=stat.st_mtime_ns, channel=None, status="ready", message="可预测")
                        try:
                            item["channel"] = self.infer_channel(relative)
                            self.prediction.checked_path(str(path))
                            if time.time() - stat.st_mtime < self.stable_seconds:
                                raise FolderInputError("INPUT_FILE_WRITING", "文件可能仍在写入，请稍后刷新")
                            if not stat.st_size:
                                raise FolderInputError("EMPTY_INPUT_FILE", "文件为空")
                            if item["channel"] not in models:
                                item.update(status="model_missing", message=f"CH{item['channel']} 模型未就绪")
                        except (ValueError, OSError) as exc:
                            item.update(status="invalid", message=str(exc))
                        files.append(item)
            files.sort(key=lambda item: item["file_name"].casefold())
            return dict(source_id=source_id, directory=str(root), revision=source["revision"],
                        owner_device_id=source["owner_device_id"], recursive=recursive, files=files,
                        total_files=len(files), ready_files=sum(x["status"] == "ready" for x in files),
                        missing_model_files=sum(x["status"] == "model_missing" for x in files),
                        invalid_files=sum(x["status"] == "invalid" for x in files), ignored_entries=ignored)
        except OSError as exc:
            raise FolderInputError("DIRECTORY_UNREADABLE", "无法读取输入文件夹，请检查权限或文件是否正在移动", 422) from exc
        finally:
            self._scan_slot.release()
