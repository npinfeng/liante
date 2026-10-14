"""Orchestrate the three stages without changing Stage2's numerical path."""

from __future__ import annotations
from contextlib import nullcontext

import importlib
import logging
import math
import traceback
from concurrent.futures import CancelledError, Future
from threading import RLock, Timer
from pathlib import Path
from time import perf_counter
from typing import Any

from config import (RECIPE_VERSION, STAGE1_ADAPTER, STAGE1_MODEL_PATH, STAGE3_ADAPTER,
                    STAGE3_MODEL_PATH, STAGE2_WORKERS, STAGE3_WORKERS,
                    STAGE2_WAITING, STAGE3_WAITING)
from config import INFERENCE_QUEUE_WAIT_SECONDS, INFERENCE_JOB_TIMEOUT_SECONDS
from config import STAGE3_MIN_CONFIDENCE
from config import MAX_BATCH_RESULT_BYTES
from services.admission import InferencePools, Reservation
from services.resource_gate import ResourceGate
from services.prediction_service import PredictionService, InputFileChangedError
from tasks.inference_store import InferenceTaskStore


LOGGER = logging.getLogger(__name__)


class InferenceError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class ModelAdapterService:
    """Load a configured integration adapter exposing predict/health_check/get_model_info."""

    def __init__(self, stage: str, model_path: str, adapter_ref: str,
                 resource_gate: ResourceGate | None = None) -> None:
        self.stage = stage
        self.model_path = model_path
        self.adapter_ref = adapter_ref
        self._adapter = None
        self._lock = RLock()
        self.resource_gate = resource_gate

    def get_model_info(self) -> dict[str, Any]:
        info = dict(stage=self.stage, model_name=None, model_version=None,
                    model_path=self.model_path or None, loaded=self._adapter is not None,
                    status=self.health_check(), recipe_version=RECIPE_VERSION)
        if self._adapter is not None:
            try:
                reported = self._adapter.get_model_info()
                if isinstance(reported, dict):
                    info.update({key: reported[key] for key in ("model_name", "model_version", "recipe_version")
                                 if key in reported})
            except Exception:
                LOGGER.exception("%s adapter model info failed", self.stage)
        return info

    def health_check(self) -> str:
        if not self.model_path or not self.adapter_ref:
            return "not_configured"
        if not Path(self.model_path).is_file():
            return "model_missing"
        if self._adapter is None:
            return "configured"
        probe = getattr(self._adapter, "health_check", None)
        if not callable(probe):
            return "adapter_unhealthy"
        try:
            return "ok" if probe() in ("ok", True) else "adapter_unhealthy"
        except Exception:
            LOGGER.exception("%s adapter health failed", self.stage)
            return "adapter_unhealthy"

    def load(self):
        if not self.model_path or not self.adapter_ref:
            raise InferenceError("MODEL_NOT_CONFIGURED", f"{self.stage} 真实模型适配器未配置", 503)
        if not Path(self.model_path).is_file():
            raise InferenceError("MODEL_LOAD_FAILED", f"{self.stage} 模型文件不存在", 503)
        with self._lock:
            if self._adapter is None:
                try:
                    module_name, class_name = self.adapter_ref.rsplit(":", 1)
                    adapter = getattr(importlib.import_module(module_name), class_name)(self.model_path)
                    if not all(callable(getattr(adapter, name, None)) for name in ("predict", "health_check", "get_model_info")):
                        raise TypeError("adapter must implement predict, health_check and get_model_info")
                    self._adapter = adapter
                except Exception as exc:
                    LOGGER.exception("%s adapter loading failed", self.stage)
                    raise InferenceError("MODEL_LOAD_FAILED", f"{self.stage} 模型加载失败", 503) from exc
        return self._adapter

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            adapter = self.load()
            if self.resource_gate is None:
                result = adapter.predict(payload)
            else:
                with self.resource_gate.enter():
                    result = adapter.predict(payload)
            if self.stage == "stage1":
                if not isinstance(result, dict) or not isinstance(result.get("qualified"), bool) or not isinstance(result.get("defects"), list):
                    raise InferenceError("INVALID_MODEL_OUTPUT", "Stage1 必须返回 qualified 布尔值和 defects 列表", 500)
                for defect in result["defects"]:
                    if not isinstance(defect, dict) or not isinstance(defect.get("type"), str) or not defect["type"].strip():
                        raise InferenceError("INVALID_MODEL_OUTPUT", "Stage1 缺陷必须包含非空类型", 500)
                    confidence, box = defect.get("confidence"), defect.get("bbox")
                    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or
                            not math.isfinite(confidence) or not 0 <= confidence <= 1 or
                            not isinstance(box, list) or len(box) != 4 or
                            any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                                not math.isfinite(value) or value < 0 for value in box) or
                            box[2] < box[0] or box[3] < box[1]):
                        raise InferenceError("INVALID_MODEL_OUTPUT", "Stage1 缺陷位置或置信度无效", 500)
            return result


class InferenceCoordinator:
    def __init__(self, store: InferenceTaskStore, prediction: PredictionService) -> None:
        self.store = store
        self.prediction = prediction
        self.executor = InferencePools(STAGE2_WORKERS, STAGE3_WORKERS,
                                       STAGE2_WAITING, STAGE3_WAITING)
        self.stage1 = ModelAdapterService("stage1", STAGE1_MODEL_PATH, STAGE1_ADAPTER,
                                          prediction.resource_gate)
        self.stage3 = ModelAdapterService("stage3", STAGE3_MODEL_PATH, STAGE3_ADAPTER,
                                          prediction.resource_gate)
        self._futures: dict[str, Future] = {}
        self._cancelled: set[str] = set()
        self._lock = RLock()

    def _track(self, task_id: str, future: Future) -> None:
        with self._lock:
            self._futures[task_id] = future
        future.add_done_callback(lambda _: self._forget(task_id))

    def _forget(self, task_id: str) -> None:
        with self._lock:
            self._futures.pop(task_id, None)
            self._cancelled.discard(task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            task = self.store.get(task_id)
            if future is None or task is None or task["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                return False
            self._cancelled.add(task_id)
            future.cancel()
        self.store.update(task_id, status="CANCELLED", error_code="TASK_CANCELLED",
                          error_message="任务已取消")
        self.store.log(task_id, "WARNING", "cancel", "任务已取消")
        return True

    def _check_active(self, task_id: str, started: float) -> None:
        with self._lock:
            if task_id in self._cancelled:
                raise CancelledError()
        task=self.store.get(task_id)
        if task is None or task['status'] in {'COMPLETED','FAILED','CANCELLED','TIMEOUT','REVIEW_REQUIRED'}:
            raise CancelledError()
        if perf_counter() - started > INFERENCE_JOB_TIMEOUT_SECONDS:
            raise InferenceError("TASK_TIMEOUT", "推理任务超过执行时间上限", 504)

    def _fail(self, task_id: str, exc: Exception) -> None:
        task = self.store.get(task_id)
        if task is None or task["status"] in {"CANCELLED", "FAILED", "COMPLETED", "REVIEW_REQUIRED", "TIMEOUT"}:
            return
        code = self._failure_code(task, exc)
        message = str(exc) if code != "INFERENCE_FAILED" else "模型推理失败，请查看服务日志"
        self._commit_terminal(task_id,'TIMEOUT' if code.endswith('TIMEOUT') else 'FAILED',
                              'ERROR',message,error_code=code,error_message=message,operation=task['operation'])
        LOGGER.exception("Inference failed task_id=%s", task_id)

    def _commit_terminal(self,task_id,status,level,message,**values):
        with self.store._lock:
            current=self.store.get(task_id)
            if current and current['status'] in {'FAILED','CANCELLED','TIMEOUT','COMPLETED','REVIEW_REQUIRED'}:
                return current
            saved=self.store.update(task_id,status=status,**values)
            if saved['status']==status and saved.get('error_code')==values.get('error_code'):
                self.store.safe_log(task_id,level,values.get('operation','complete'),message)
            return saved

    @staticmethod
    def _failure_code(task, exc):
        if isinstance(exc, InputFileChangedError):
            return "INPUT_FILE_CHANGED"
        return exc.code if hasattr(exc,'code') else (
            ("MODEL_NOT_CONFIGURED" if isinstance(exc, FileNotFoundError) else "MODEL_LOAD_FAILED")
            if task["operation"] == "load_model" else
            "FILE_NOT_FOUND" if isinstance(exc, FileNotFoundError) else
            "INVALID_FILE_FORMAT" if isinstance(exc, (ValueError, KeyError, IndexError)) else "INFERENCE_FAILED")

    def _watchdog(self, task_id: str) -> Timer:
        def expired() -> None:
            with self._lock:
                task = self.store.get(task_id)
                if task is None or task["status"] != "RUNNING":
                    return
                self._cancelled.add(task_id)
            self._commit_terminal(task_id,'TIMEOUT','ERROR','模型调用超过任务时限',
                                  operation='timeout',error_code='TASK_TIMEOUT',error_message='模型调用超过任务时限')
        timer = Timer(INFERENCE_JOB_TIMEOUT_SECONDS, expired)
        timer.daemon = True
        timer.start()
        return timer

    def queue_stage2(self, task_id: str, files: list[dict[str, Any]],
                     reservation: Reservation | None = None, *, continue_on_error: bool = False) -> None:
        self.store.update(task_id,status='QUEUED',input_files=files,continue_on_error=continue_on_error)
        submit = (lambda fn, *args, **kwargs: self.executor.submit_reserved(
            "stage2", reservation, fn, *args, **kwargs)) if reservation else (
            lambda fn, *args, **kwargs: self.executor.submit("stage2", fn, *args, **kwargs))
        future = submit(self._run_stage2, task_id, files, continue_on_error,
                                      max_queue_wait_seconds=INFERENCE_QUEUE_WAIT_SECONDS,
                                      on_expired=lambda: self._fail(task_id, InferenceError(
                                          "QUEUE_TIMEOUT", "推理任务排队超时", 503)))
        self._track(task_id, future)

    def _run_stage2(self, task_id: str, files: list[dict[str, Any]], continue_on_error: bool = False) -> None:
        started = perf_counter()
        watchdog = self._watchdog(task_id)
        results = []
        total = sum(len(item["channels"]) for item in files)
        done = 0
        failed = 0
        try:
            self._check_active(task_id,started)
            self.store.update(task_id, status="RUNNING", file_total=len(files),
                              channel_total=0, operation="load_file")
            for file_index, item in enumerate(files, 1):
                self._check_active(task_id, started)
                path = str(Path(item["file_path"]).expanduser().resolve())
                file_name = Path(path).name
                channels = item["channels"]
                self.store.update(task_id, current_file=file_name, file_index=file_index,
                                  channel_total=len(channels), channel_index=0,
                                  current_channel=None, operation="load_file")
                self.store.log(task_id, "INFO", "load_file", f"开始处理文件 {file_name} ({file_index}/{len(files)})",
                               file_path=path)
                for channel_index, channel in enumerate(channels, 1):
                    self._check_active(task_id, started)
                    self.store.update(task_id, current_channel=f"CH{channel}",
                                      channel_index=channel_index, operation="preprocess")
                    self.store.log(task_id, "INFO", "preprocess", f"开始处理 CH{channel} ({channel_index}/{len(channels)})",
                                   file_path=path)

                    def event(operation: str, message: str) -> None:
                        self.store.update(task_id, operation=operation)
                        self.store.log(task_id, "INFO", operation, message, file_path=path)

                    try:
                        predictions, source = self.prediction.predict_file(
                            channel, path, item.get("sheet_name", 0), item.get("feature_columns"),
                            event_callback=event, collect=False,
                            input_size_bytes=item.get("input_size_bytes"), input_mtime_ns=item.get("input_mtime_ns"),
                            batch_sink=lambda batch: self.store.index.append_predictions(
                                task_id, file_index, channel, batch, MAX_BATCH_RESULT_BYTES))
                    except Exception as exc:
                        if not continue_on_error or isinstance(exc, CancelledError):
                            raise
                        self._check_active(task_id, started)
                        code = self._failure_code(self.store.get(task_id), exc)
                        failed += 1
                        done += 1
                        message = str(exc) if code != "INFERENCE_FAILED" else "模型推理失败，请查看服务日志"
                        results.append(dict(file_path=path, file_index=file_index, channel=channel,
                                            status="FAILED", error_code=code, error_message=message,
                                            prediction_count=0, predictions=[]))
                        self.store.log(task_id, "ERROR", "file_failed", message, error_code=code, file_path=path)
                        self.store.update(task_id, progress=int(done * 100 / total),
                                          result=dict(files=list(results), completed_channels=done-failed,
                                                      failed_channels=failed, total_channels=total, partial=True))
                        continue
                    self._check_active(task_id, started)
                    self.store.update(task_id, operation="save_result")
                    row_count = source["row_count"]
                    predictions, _ = self.store.index.predictions(task_id, file_index, channel,
                                                                   min(row_count, 1000) if row_count <= 1000 else 10, 0)
                    results.append(dict(file_path=path, file_index=file_index, channel=channel, status="COMPLETED", source=source,
                                        predictions=predictions, prediction_count=row_count,
                                        predictions_truncated=row_count > len(predictions),
                                        predictions_endpoint=f"/api/v1/tasks/{task_id}/predictions?file_index={file_index}&channel={channel}",
                                        model_name="MLP",
                                        model_version="artifact", recipe_version=RECIPE_VERSION))
                    done += 1
                    self.store.update(task_id, progress=int(done * 100 / total),
                                      result=dict(files=list(results), completed_channels=done-failed,
                                                  failed_channels=failed, total_channels=total, partial=True))
                    self.store.log(task_id, "INFO", "save_result", f"CH{channel} 结果已保存", file_path=path)
                self.store.log(task_id, "INFO", "file_complete", f"文件 {file_name} 处理完成", file_path=path)
            duration = int((perf_counter() - started) * 1000)
            self._check_active(task_id, started)
            if failed:
                result = dict(files=results, duration_ms=duration, completed_channels=done-failed,
                              failed_channels=failed, total_channels=total, partial=False)
                self._commit_terminal(task_id,'FAILED','ERROR',f'文件夹处理完成：{done-failed} 项成功，{failed} 项失败',progress=100,operation='complete',result=result,
                                  error_code="BATCH_PARTIAL_FAILED", error_message=f"{failed} 项预测失败，请查看逐文件结果")
                return
            self._commit_terminal(task_id,'COMPLETED','INFO','EA/Bias参数推荐完成',progress=100,
                              operation="complete", result=dict(files=results, duration_ms=duration))
        except CancelledError:
            return
        except Exception as exc:
            self._fail(task_id, exc)
        finally:
            watchdog.cancel()

    def queue_adapter(self, task_id: str, stage: str, payload: dict[str, Any],
                      reservation: Reservation | None = None) -> None:
        submit = (lambda fn, *args, **kwargs: self.executor.submit_reserved(
            stage, reservation, fn, *args, **kwargs)) if reservation else (
            lambda fn, *args, **kwargs: self.executor.submit(stage, fn, *args, **kwargs))
        future = submit(self._run_adapter, task_id, stage, payload,
                                      max_queue_wait_seconds=INFERENCE_QUEUE_WAIT_SECONDS,
                                      on_expired=lambda: self._fail(task_id, InferenceError(
                                          "QUEUE_TIMEOUT", "推理任务排队超时", 503)))
        self._track(task_id, future)

    def _run_adapter(self, task_id: str, stage: str, payload: dict[str, Any]) -> None:
        started = perf_counter()
        watchdog = self._watchdog(task_id)
        service = self.stage1 if stage == "stage1" else self.stage3
        try:
            self._check_active(task_id, started)
            if stage=='stage3' and hasattr(self,'qualifications'):
                self.qualifications.active(payload.get('qualification_id'),payload['product_id'])
            self.store.update(task_id, status="RUNNING", operation="load_model")
            self.store.log(task_id, "INFO", "load_model", "加载模型")
            self.store.update(task_id, operation="inference")
            self.store.log(task_id, "INFO", "inference", "开始推理")
            result = service.predict(payload)
            self._check_active(task_id, started)
            if not isinstance(result, dict):
                raise InferenceError("INFERENCE_FAILED", "模型适配器结果必须为对象", 500)
            if stage == "stage3":
                if result.get("grade") not in {"A", "B", "C", "D"} or (
                    "qualified" in result and result["qualified"] is not True):
                    raise InferenceError("INVALID_MODEL_OUTPUT", "Stage3 模型必须为合格产品输出 A/B/C/D 等级", 500)
                for field in ("score", "confidence"):
                    value = result.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                        raise InferenceError("INVALID_MODEL_OUTPUT", f"Stage3 {field} 必须位于 0 到 1", 500)
                if not isinstance(result.get("reason"), str) or not result["reason"].strip():
                    raise InferenceError("INVALID_MODEL_OUTPUT", "Stage3 必须说明分级原因", 500)
                if not isinstance(result.get("rule_version"), str) or not result["rule_version"].strip():
                    raise InferenceError("INVALID_MODEL_OUTPUT", "Stage3 必须提供规则版本", 500)
                result.pop("qualified", None)
                result["eligible_for_grading"] = True
                result["qualification_id"] = payload.get("qualification_id")
            result.update(task_id=task_id, product_id=payload["product_id"], stage=stage,
                          duration_ms=int((perf_counter() - started) * 1000))
            if stage == "stage3" and result["confidence"] < STAGE3_MIN_CONFIDENCE:
                result["suggested_grade"] = result.pop("grade")
                result["grade"] = None
                result["review_reason"] = "分级置信度低于人工复核阈值"
            review=stage=='stage3' and result['confidence']<STAGE3_MIN_CONFIDENCE
            guard=self.qualifications.lock if stage=='stage3' and hasattr(self,'qualifications') else nullcontext()
            with guard:
                if stage=='stage3' and hasattr(self,'qualifications'):
                    self.qualifications.active(payload.get('qualification_id'),payload['product_id'])
                self._commit_terminal(task_id,'REVIEW_REQUIRED' if review else 'COMPLETED',
                                      'WARNING' if review else 'INFO',result.get('review_reason','任务完成'),
                                      operation='review' if review else 'save_result',progress=100,result=result)
        except CancelledError:
            return
        except Exception as exc:
            self._fail(task_id, exc)
        finally:
            watchdog.cancel()

    def gate_stage3(self, payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        required = ("aoi_result", "optical_features", "electrical_features", "tuning_result")
        failed = [key for key in required[:3] if isinstance(payload.get(key), dict)
                  and payload[key].get("qualified") is False]
        if failed:
            return "FAILED", dict(eligible_for_grading=False, grade=None, score=None, confidence=None,
                                  reason="产品未通过前序基础合格检测，不进入质量分级",
                                  triggered_rules=[f"{x.upper()}_FAILED" for x in failed])
        missing = [key for key in required if not isinstance(payload.get(key), dict) or not payload[key]]
        if missing:
            return "REVIEW_REQUIRED", dict(eligible_for_grading=False, grade=None, score=None, confidence=None,
                                           reason="前序合格性数据不完整，暂不进入质量分级",
                                           triggered_rules=[f"MISSING_{x.upper()}" for x in missing])
        unknown = [key for key in required[:3] if payload[key].get("qualified") is not True]
        if unknown:
            return "REVIEW_REQUIRED", dict(eligible_for_grading=False, grade=None, score=None, confidence=None,
                                           reason="前序合格性未确认，暂不进入质量分级",
                                           triggered_rules=[f"{x.upper()}_UNKNOWN" for x in unknown])
        if payload["tuning_result"].get("status") != "complete":
            return "REVIEW_REQUIRED", dict(eligible_for_grading=False, grade=None, score=None, confidence=None,
                                           reason="调测未完成，暂不进入质量分级",
                                           triggered_rules=["TUNING_INCOMPLETE"])
        conflict = [key for key in required[:3] if isinstance(payload[key].get("passed"), bool)
                    and payload[key]["passed"] is not payload[key]["qualified"]]
        if conflict:
            return "REVIEW_REQUIRED", dict(eligible_for_grading=False, grade=None, score=None, confidence=None,
                                           reason="前序检测结果冲突，暂不进入质量分级",
                                           triggered_rules=[f"{x.upper()}_CONFLICT" for x in conflict])
        return None, None
