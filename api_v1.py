"""Unified v1 HTTP boundary; legacy routes remain in backend_server.py."""

from __future__ import annotations

import logging
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from config import (DATA_DIR, DEBUG, RECIPE_VERSION, ADMIN_TOKEN, OPERATOR_TOKEN,
                    DEVICE_TOKEN, DEVICE_TOKENS, AOI_ALLOWED_DIRS)
from config import AOI_MAX_FRAME_BYTES
from config import (ALERT_AOI_QUEUE_AGE_SECONDS, ALERT_INFERENCE_QUEUE_AGE_SECONDS,
                    ALERT_AOI_REJECTION_RATE, ALERT_AOI_FRAME_MS)
from schemas.inference import BatchPredictRequest, FolderPredictRequest, Stage2SourceRequest, TaskCreate, StreamCreate, AOIFolderReplayRequest
from services.folder_input_service import FolderInputService, FolderInputError
from services.inference_service import InferenceCoordinator, InferenceError
from services.aoi_stream_service import AOIStreamManager, StreamError
from services.admission import CapacityError
from services.prediction_service import PredictionService
from tasks.inference_store import InferenceTaskStore
from tasks.qualification_store import QualificationStore, QualificationError
from tasks.task_manager import TrainingTaskManager
from services.access_policy import actor_identity
from services.pagination import pagination


LOGGER = logging.getLogger(__name__)
VALID_IMAGE_HEADERS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"BM", b"II*\x00", b"MM\x00*")


def ok(data, status=200):
    return jsonify(code=0, message="success", data=data), status


def error(code, message, status, *, task_id=None, request_id=None):
    return jsonify(code=code, message=message,
                   data={"task_id": task_id} if task_id else {},
                   request_id=request_id or getattr(g,'request_id',None) or uuid.uuid4().hex,
                   timestamp=datetime.now(timezone.utc).isoformat()), status


def create_api_blueprint(store: InferenceTaskStore, coordinator: InferenceCoordinator,
                         prediction: PredictionService, streams: AOIStreamManager,
                         qualifications: QualificationStore,
                         training: TrainingTaskManager, folder_inputs: FolderInputService) -> Blueprint:
    api = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    def stream_error(exc: StreamError, task_id: str | None = None):
        response, status = error(exc.code, str(exc), exc.status, task_id=task_id)
        if exc.retry_after:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response, status

    def health_data():
        stage2_channels = prediction.available_channels()
        aoi = streams.health()
        inference_queues = coordinator.executor.stats()
        training_queue = training.capacity()
        disk = shutil.disk_usage(DATA_DIR)
        memory = psutil.virtual_memory()
        gpu = None
        if torch.cuda.is_available():
            free_gpu, total_gpu = torch.cuda.mem_get_info()
            gpu = dict(free_bytes=free_gpu, total_bytes=total_gpu,
                       used_ratio=round(1 - free_gpu / total_gpu, 4))
        resources = dict(disk_free_bytes=disk.free, disk_total_bytes=disk.total,
                         disk_free_ratio=round(disk.free / disk.total, 4),
                         process_rss_bytes=psutil.Process().memory_info().rss,
                         memory_available_bytes=memory.available,
                         memory_total_bytes=memory.total, gpu=gpu)
        disk_capacity=current_app.extensions['capacity'].snapshot()
        resources['disk_capacity']=disk_capacity
        alerts = []
        if aoi["oldest_queued_seconds"] >= ALERT_AOI_QUEUE_AGE_SECONDS:
            alerts.append("AOI_QUEUE_AGE_HIGH")
        if aoi["journal_frame_bytes"] >= aoi["journal_max_bytes"] * 0.9:
            alerts.append("AOI_STORAGE_NEAR_CAPACITY")
        if aoi["rejected_frames"] >= 10 and aoi["rejection_rate"] >= ALERT_AOI_REJECTION_RATE:
            alerts.append("AOI_REJECTION_RATE_HIGH")
        if aoi["processed_frames"] >= 10 and aoi["average_frame_ms"] >= ALERT_AOI_FRAME_MS:
            alerts.append("AOI_PROCESSING_SLOW")
        for stage, capacity in inference_queues.items():
            if capacity["oldest_waiting_seconds"] >= ALERT_INFERENCE_QUEUE_AGE_SECONDS:
                alerts.append(f"{stage.upper()}_QUEUE_AGE_HIGH")
        if resources["disk_free_ratio"] < 0.1:
            alerts.append("DATA_DISK_LOW")
        if disk_capacity['status']!='ok': alerts.append('DISK_CAPACITY_'+disk_capacity['status'].upper())
        if store.snapshot_errors: alerts.append('TASK_SNAPSHOT_DEGRADED')
        if store.audit_mirror_errors: alerts.append('AUDIT_MIRROR_DEGRADED')
        if gpu and gpu["used_ratio"] > 0.95:
            alerts.append("GPU_MEMORY_HIGH")
        return dict(web="ok", stage1=coordinator.stage1.health_check(),
                       stage2="ok" if stage2_channels else "model_missing",
                       stage2_channels=stage2_channels, stage3=coordinator.stage3.health_check(),
                       database="sqlite", gpu="available" if torch.cuda.is_available() else "unavailable",
                       task_store="ok" if store.output_dir.is_dir() else "error",
                       log_dir="ok" if store.log_dir.is_dir() else "error",
                       aoi_streams=aoi, inference_queue=inference_queues,
                       training_queue=training_queue, alerts=alerts, resources=resources,
                       draining=current_app.extensions["draining"].is_set(),
                       model_resources=prediction.resource_gate.stats() if prediction.resource_gate else {},
                       auth="enabled" if any((ADMIN_TOKEN, OPERATOR_TOKEN, DEVICE_TOKEN, DEVICE_TOKENS)) else "disabled")

    @api.get("/health")
    def health():
        return ok(health_data())

    @api.get("/ready")
    def ready():
        data = health_data()
        if all(data[stage] == "ok" for stage in ("stage1", "stage2", "stage3")) and len(data['stage2_channels'])==8 and not data["alerts"] and not data["draining"]:
            return ok(data)
        return jsonify(code="NOT_READY", message="模型或容量尚未达到三阶段运行条件", data=data), 503

    @api.get("/models")
    def models():
        models = [coordinator.stage1.get_model_info()]
        for channel in range(8):
            models.append(dict(stage="stage2", channel=channel, model_name="MLP",
                               model_version="artifact", model_path=str(prediction.artifact_dir(channel)),
                               loaded=channel in prediction._cache,
                               status="ok" if channel in prediction.available_channels() else "model_missing",
                               recipe_version=RECIPE_VERSION))
        models.append(coordinator.stage3.get_model_info())
        return ok([{key:value for key,value in info.items() if key!='model_path'} for info in models])

    @api.post("/tasks")
    def create_task():
        try:
            payload=TaskCreate.model_validate(request.get_json(silent=True)).model_dump()
            task = store.create(payload.get("stage"), payload["product_id"].strip(), payload.get("request_id"))
        except ValidationError as exc:
            return error('INVALID_ARGUMENT',exc.errors(include_url=False)[0]['msg'],400)
        except ValueError as exc:
            return error("INVALID_ARGUMENT", str(exc), 400)
        return ok(task, 201)

    @api.get("/tasks/<task_id>")
    def task(task_id):
        value = store.get(task_id)
        if value is None:
            return error("TASK_NOT_FOUND", "任务不存在", 404)
        if request.args.get("summary") == "1" and value["stage"] == "stage2":
            value["result"] = None
        return ok(value)

    @api.get("/tasks/<task_id>/result")
    def result(task_id):
        value = store.get(task_id)
        if value is None:
            return error("TASK_NOT_FOUND", "任务不存在", 404)
        if value["status"] in {"PENDING", "QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            return ok(dict(task_id=task_id, status=value["status"], result=None), 202)
        if value["status"] in {"FAILED","TIMEOUT","CANCELLED"}:
            if value["stage"] == "stage2" and value.get("result"):
                return jsonify(code=value["error_code"] or "TASK_FAILED", message=value["error_message"],
                               data=dict(task_id=task_id, status="FAILED", result=value["result"]),
                               request_id=value["request_id"]), 409
            return error(value["error_code"] or "TASK_FAILED", value["error_message"] or "任务失败", 409,
                         task_id=task_id, request_id=value["request_id"])
        return ok(dict(task_id=task_id, status=value["status"], result=value["result"]))

    @api.get("/tasks/<task_id>/predictions")
    def paged_predictions(task_id):
        task = store.get(task_id)
        if task is None or task["stage"] != "stage2":
            return error("TASK_NOT_FOUND", "Stage2 任务不存在", 404)
        try:
            file_index = int(request.args.get("file_index", "1"))
            channel = int(request.args.get("channel", "0"))
            limit,offset = pagination(request.args,100)
        except ValueError:
            return error("INVALID_ARGUMENT", "分页参数必须是整数", 400)
        if file_index < 1 or not 0 <= channel <= 7 or not 1 <= limit <= 1000 or offset < 0:
            return error("INVALID_ARGUMENT", "分页参数超出允许范围", 400)
        items, total = store.index.predictions(task_id, file_index, channel, limit, offset)
        return ok(dict(items=items, total=total, file_index=file_index,
                       channel=channel, limit=limit, offset=offset))

    @api.post("/tasks/<task_id>/cancel")
    def cancel_task(task_id):
        task = store.get(task_id)
        if task is None:
            return error("TASK_NOT_FOUND", "任务不存在", 404)
        if not coordinator.cancel(task_id):
            return error("TASK_NOT_CANCELLABLE", "任务已结束或不在推理队列", 409, task_id=task_id)
        return ok(dict(task_id=task_id, status="CANCELLED"))

    @api.get("/tasks/<task_id>/logs")
    def logs(task_id):
        try:
            limit,offset = pagination(request.args,200)
        except ValueError:
            return error("INVALID_ARGUMENT", "limit 和 offset 必须是整数", 400)
        if not 1 <= limit <= 1000 or offset < 0:
            return error("INVALID_ARGUMENT", "limit 必须在 1..1000，offset 不能小于 0", 400)
        try:
            entries, total = store.logs(task_id, request.args.get("stage"), limit, offset)
        except KeyError:
            return error("TASK_NOT_FOUND", "任务不存在", 404)
        if not DEBUG:
            entries = [{**entry, "exception": None} for entry in entries]
        return ok(dict(items=entries, total=total, limit=limit, offset=offset))

    @api.get("/inference/ea-bias/sources")
    def stage2_sources():
        return ok(dict(sources=folder_inputs.sources(getattr(g, "liante_device_id", None)),
                       model_channels=prediction.available_channels()))

    def stage2_actor():
        device_id = getattr(g, "liante_device_id", None)
        return f"device:{device_id}" if device_id else getattr(g, "liante_role", "local")

    @api.after_request
    def stage2_audit(response):
        if request.method != "POST" or request.path not in {
                "/api/v1/inference/ea-bias/sources", "/api/v1/inference/ea-bias/scan",
                "/api/v1/inference/ea-bias/folder"}:
            return response
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        source_id = str(payload.get("source_id", "__invalid__"))[:100]
        body = response.get_json(silent=True) or {}
        details = getattr(g, "stage2_audit_details", {})
        owner = details.pop("owner_device_id", getattr(g, "liante_device_id", None))
        entry = dict(operation=request.path.rsplit("/", 1)[-1], actor=stage2_actor(),
                     request_id=str(payload.get("request_id") or body.get("request_id") or
                                    (body.get("data") or {}).get("request_id") or "")[:200],
                     http_status=response.status_code, code=body.get("code"),
                     message=str(body.get("message", ""))[:1000],
                     level="INFO" if response.status_code < 400 else "WARNING", **details)
        try:
            folder_inputs.registry.log(source_id, owner, **entry)
        except Exception:
            LOGGER.exception("Unable to persist Stage2 source audit event")
        return response

    @api.post("/inference/ea-bias/sources")
    def configure_stage2_source():
        if getattr(g, "liante_role", "admin") == "operator":
            return error("FORBIDDEN", "仅管理员或独立 MES 身份可登记来源", 403)
        try:
            payload = Stage2SourceRequest.model_validate(request.get_json(silent=True))
            if payload.owner_device_id and payload.owner_device_id not in DEVICE_TOKENS:
                return error("UNKNOWN_DEVICE", "owner_device_id 必须是已配置的独立 MES 身份", 400)
            value, created, previous = folder_inputs.configure(
                payload.source_id, payload.directory, device_id=getattr(g, "liante_device_id", None),
                owner_device_id=payload.owner_device_id, expected_revision=payload.expected_revision)
            g.stage2_audit_details = dict(directory=value["directory"], revision=value["revision"],
                                         previous_directory=previous["directory"] if previous else None,
                                         owner_device_id=value["owner_device_id"])
            return ok({**value, "exists": True}, 201 if created else 200)
        except ValidationError as exc:
            return error("INVALID_ARGUMENT", exc.errors(include_url=False)[0]["msg"], 400)
        except FolderInputError as exc:
            return folder_failure(exc)

    @api.get("/inference/ea-bias/sources/<source_id>/logs")
    def stage2_source_logs(source_id):
        try:
            device_id = getattr(g, "liante_device_id", None)
            folder_inputs.source(source_id, device_id)
            limit,offset = pagination(request.args,100)
            if not 1 <= limit <= 1000 or offset < 0:
                raise ValueError("limit 必须在 1..1000，offset 不能小于 0")
            items, total = folder_inputs.registry.logs(source_id, device_id, limit, offset)
            return ok(dict(items=items, total=total, limit=limit, offset=offset, order="newest_first"))
        except FolderInputError as exc:
            return folder_failure(exc)
        except ValueError as exc:
            return error("INVALID_ARGUMENT", str(exc), 400)

    def folder_failure(exc):
        response, status = error(exc.code, str(exc), exc.status)
        if status == 429:
            response.headers["Retry-After"] = "2"
        return response, status

    @api.post("/inference/ea-bias/scan")
    def scan_stage2_folder():
        try:
            payload = FolderPredictRequest.model_validate(request.get_json(silent=True))
            manifest = folder_inputs.scan(payload.source_id, payload.recursive, getattr(g, "liante_device_id", None))
            g.stage2_audit_details = {key: manifest[key] for key in (
                "directory", "revision", "owner_device_id", "total_files", "invalid_files", "missing_model_files")}
            return ok(manifest)
        except ValidationError as exc:
            return error("INVALID_ARGUMENT", exc.errors(include_url=False)[0]["msg"], 400)
        except FolderInputError as exc:
            return folder_failure(exc)

    @api.post("/inference/ea-bias/folder")
    def predict_stage2_folder():
        try:
            payload = FolderPredictRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return error("INVALID_ARGUMENT", exc.errors(include_url=False)[0]["msg"], 400)
        device_id = getattr(g, "liante_device_id", None)
        if payload.request_id is None and request.headers.get('X-Request-ID'):
            payload.request_id=request.headers['X-Request-ID']
        if device_id and (not payload.request_id or not payload.product_id):
            return error("INVALID_ARGUMENT", "MES 预测必须提供 product_id 和 request_id", 400)
        if not folder_inputs.submission_slot.acquire(blocking=False):
            return folder_failure(FolderInputError("SUBMISSION_BUSY", "正在提交任务，请稍后重试", 429))
        reservation, task = None, None
        try:
            folder_inputs.source(payload.source_id, device_id)
            fingerprint = hashlib.sha256(json.dumps(payload.model_dump(exclude={"request_id"}),
                                                     sort_keys=True).encode()).hexdigest()
            actor = stage2_actor()
            if payload.request_id:
                receipt = folder_inputs.registry.receipt(actor, payload.request_id)
                if receipt:
                    if receipt["fingerprint"] != fingerprint:
                        raise FolderInputError("REQUEST_REPLAY_CONFLICT", "同一 request_id 不可用于不同的预测参数", 409)
                    existing = store.get(receipt["task_id"])
                    if existing is None:
                        raise FolderInputError("TASK_HISTORY_MISSING", "原任务记录缺失，请联系管理员", 409)
                    g.stage2_audit_details = dict(task_id=existing["task_id"], replayed=True,
                                                 owner_device_id=existing.get("owner_device_id"))
                    return ok(dict(task_id=existing["task_id"], request_id=existing["request_id"],
                                   status=existing["status"], product_id=existing["product_id"], replayed=True), 200)
            try:
                reservation = coordinator.executor.reserve("stage2")
            except CapacityError:
                raise FolderInputError("QUEUE_FULL", "推理队列已满，请稍后重试", 429)
            manifest = folder_inputs.scan(payload.source_id, payload.recursive, device_id)
            g.stage2_audit_details = {key: manifest[key] for key in (
                "directory", "revision", "owner_device_id", "total_files", "invalid_files", "missing_model_files")}
            if not manifest["total_files"]:
                raise FolderInputError("INPUT_FOLDER_EMPTY", "文件夹中没有 CSV/XLSX 数据文件", 422)
            if manifest["invalid_files"]:
                return jsonify(code="INVALID_INPUT_FOLDER", message="部分文件无法识别，请刷新查看具体原因",
                               data=manifest), 422
            files = [dict(file_path=item["file_path"], channels=[item["channel"]],
                          input_size_bytes=item["size_bytes"], input_mtime_ns=item["mtime_ns"])
                     for item in manifest["files"]]
            prediction.batch_work(files)
            product_id = payload.product_id or "BATCH-" + uuid.uuid4().hex[:12]
            task = store.create("stage2", product_id, payload.request_id, source_id=payload.source_id, source_revision=manifest["revision"],
                         input_directory=manifest["directory"], owner_device_id=manifest["owner_device_id"])
            store.log(task["task_id"], "INFO", "mes_receive", "收到文件夹预测请求",
                      source_id=payload.source_id, source_revision=manifest["revision"],
                      input_directory=manifest["directory"], actor=actor, file_total=manifest["total_files"])
            coordinator.queue_stage2(task["task_id"], files, reservation, continue_on_error=True)
            g.stage2_audit_details.update(task_id=task["task_id"], replayed=False)
            return ok(dict(task_id=task["task_id"], request_id=task["request_id"], status="PENDING",
                           product_id=product_id, source=manifest), 202)
        except FolderInputError as exc:
            return folder_failure(exc)
        except (ValueError, OSError, KeyError, IndexError) as exc:
            if task is not None:
                store.update(task["task_id"], status="FAILED", error_code="QUEUE_SUBMISSION_FAILED",
                             error_message="文件夹预测提交失败")
            return error("INVALID_INPUT_FOLDER", str(exc), 422)
        except Exception:
            if task is not None:
                store.update(task["task_id"], status="FAILED", error_code="QUEUE_SUBMISSION_FAILED",
                             error_message="文件夹预测提交失败")
            LOGGER.exception("Folder inference submission failed")
            return error("QUEUE_SUBMISSION_FAILED", "文件夹预测提交失败，请查看服务日志", 500)
        finally:
            if reservation is not None:
                reservation.release()
            folder_inputs.submission_slot.release()

    @api.post("/inference/ea-bias")
    def ea_bias():
        try:
            payload = BatchPredictRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return error("INVALID_ARGUMENT", exc.errors(include_url=False)[0]["msg"], 400)
        try:
            prediction.batch_work([item.model_dump() for item in payload.files])
        except FileNotFoundError as exc:
            return error("FILE_NOT_FOUND", str(exc), 404)
        except (ValueError, KeyError, IndexError) as exc:
            return error("INVALID_ARGUMENT", str(exc), 400)
        try:
            reservation = coordinator.executor.reserve("stage2")
        except CapacityError:
            response, code = error("QUEUE_FULL", "推理队列已满，请稍后重试", 429)
            response.headers["Retry-After"] = "2"
            return response, code
        task = None
        try:
            task = store.create("stage2", payload.product_id, payload.request_id)
            coordinator.queue_stage2(task["task_id"], [item.model_dump() for item in payload.files], reservation)
        except Exception:
            if task is not None:
                store.update(task["task_id"], status="FAILED", error_code="QUEUE_SUBMISSION_FAILED",
                             error_message="推理任务提交失败")
            raise
        finally:
            reservation.release()
        return ok(dict(task_id=task["task_id"], request_id=task["request_id"], status="PENDING"), 202)

    @api.post("/inference/aoi")
    def aoi():
        if (request.mimetype == "multipart/form-data" and request.content_length is not None
                and request.content_length > AOI_MAX_FRAME_BYTES + 65536):
            return error("INVALID_FRAME", "图片超过 AOI 上限", 413)
        payload = request.form.to_dict() if request.files else request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("product_id"), str) or not payload["product_id"].strip():
            return error("INVALID_ARGUMENT", "product_id 必须是非空字符串", 400)
        task = store.create("stage1", payload["product_id"].strip(), payload.get("request_id"))
        task_id = task["task_id"]
        store.update(task_id, status="RUNNING", operation="load_file")
        store.log(task_id, "INFO", "receive", "收到 AOI 请求")
        upload = request.files.get("image")
        if upload:
            if request.content_length is not None and request.content_length > AOI_MAX_FRAME_BYTES + 65536:
                store.update(task_id, status="FAILED", error_code="INVALID_FRAME", error_message="图片超过上限")
                return error("INVALID_FRAME", "图片超过 AOI 上限", 413, task_id=task_id)
            header = upload.stream.read(8)
            upload.stream.seek(0)
            suffixes = (".png", ".jpg", ".bmp", ".tif", ".tif")
            detected_suffix = next((suffix for magic, suffix in zip(VALID_IMAGE_HEADERS, suffixes)
                                    if header.startswith(magic)), None)
            if detected_suffix is None:
                store.update(task_id, status="FAILED", error_code="INVALID_FILE_FORMAT", error_message="图片格式无效")
                store.log(task_id, "ERROR", "validate", "图片格式无效")
                return error("INVALID_FILE_FORMAT", "图片格式无效", 400, task_id=task_id)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            destination = DATA_DIR / f"{task_id}{detected_suffix}"
            upload.save(destination)
            payload["image_path"] = str(destination)
            store.update(task_id, current_file=upload.filename or destination.name,
                         file_index=1, file_total=1, operation="load_file")
            store.log(task_id, "INFO", "load_file", "图片已读取", file_path=str(destination))
        elif payload.get("image_path"):
            image_path = Path(payload["image_path"]).expanduser().resolve()
            allowed = [Path(value).expanduser().resolve() for value in AOI_ALLOWED_DIRS.split(";") if value.strip()]
            if not allowed and current_app.config['APP_ENV']!='production': allowed=[DATA_DIR]
            if not any(image_path.is_relative_to(root) for root in allowed):
                store.update(task_id, status="FAILED", error_code="PATH_FORBIDDEN", error_message="AOI 文件不在允许目录内")
                return error("PATH_FORBIDDEN", "AOI 文件不在允许目录内", 403, task_id=task_id)
            if not image_path.is_file():
                store.update(task_id, status="FAILED", error_code="FILE_NOT_FOUND", error_message="图片文件不存在")
                store.log(task_id, "ERROR", "load_file", "图片文件不存在")
                return error("FILE_NOT_FOUND", "图片文件不存在", 404, task_id=task_id)
            payload["image_path"] = str(image_path)
            store.update(task_id, current_file=Path(payload["image_path"]).name,
                         file_index=1, file_total=1, operation="load_file")
        else:
            store.update(task_id, status="FAILED", error_code="INVALID_ARGUMENT", error_message="需要 image 或 image_path")
            store.log(task_id, "ERROR", "validate", "缺少图片")
            return error("INVALID_ARGUMENT", "需要 image 或 image_path", 400, task_id=task_id)
        store.log(task_id, "INFO", "validate", "图片格式校验完成")
        try:
            coordinator.stage1.load()
        except InferenceError as exc:
            store.update(task_id, status="FAILED", error_code=exc.code, error_message=str(exc))
            store.log(task_id, "ERROR", "load_model", str(exc), error_code=exc.code)
            return error(exc.code, str(exc), exc.http_status, task_id=task_id)
        try:
            coordinator.queue_adapter(task_id, "stage1", payload)
        except CapacityError:
            store.update(task_id, status="FAILED", error_code="QUEUE_FULL", error_message="推理队列已满")
            response, code = error("QUEUE_FULL", "推理队列已满，请稍后重试", 429, task_id=task_id)
            response.headers["Retry-After"] = "2"
            return response, code
        return ok(dict(task_id=task_id, status="RUNNING"), 202)

    @api.post("/aoi/streams")
    def start_aoi_stream():
        try: payload=StreamCreate.model_validate(request.get_json(silent=True)).model_dump()
        except ValidationError as exc: return error('INVALID_ARGUMENT',exc.errors(include_url=False)[0]['msg'],400)
        product_id = payload.get("product_id")
        source_id = payload.get("source_id")
        if not isinstance(product_id, str) or not product_id.strip():
            return error("INVALID_ARGUMENT", "product_id 必须是非空字符串", 400)
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 128:
            return error("INVALID_ARGUMENT", "source_id 必须是 1–128 字符的设备编号", 400)
        bound_device = getattr(g, "liante_device_id", None)
        if bound_device and bound_device != source_id.strip():
            return error("SOURCE_FORBIDDEN", "设备身份与 source_id 不匹配", 403)
        try:
            return ok(streams.start(product_id.strip(), source_id.strip(), payload.get("request_id"),
                                    payload.get('view_types'),payload.get('required_views')), 201)
        except StreamError as exc:
            return stream_error(exc)

    @api.post('/aoi/folder-runs')
    def start_aoi_folder_run():
        try:
            payload=AOIFolderReplayRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return error('INVALID_ARGUMENT',exc.errors(include_url=False)[0]['msg'],400)
        requested=Path(payload.directory).expanduser()
        directory=(requested if requested.is_absolute() else DATA_DIR.parent / requested).resolve()
        allowed=[Path(value).expanduser().resolve() for value in AOI_ALLOWED_DIRS.split(';') if value.strip()]
        if not allowed and current_app.config['APP_ENV']!='production':
            allowed=[DATA_DIR.resolve()]
        if not any(directory.is_relative_to(root) for root in allowed):
            return error('PATH_FORBIDDEN','图片文件夹不在 AOI_ALLOWED_DIRS 允许目录内',403)
        try:
            return ok(streams.start_folder_replay(directory,payload.product_id,payload.source_id,
                                                  payload.interval_seconds,payload.request_id),201)
        except StreamError as exc:
            return stream_error(exc)

    @api.get('/aoi/folder-runs/<task_id>')
    def aoi_folder_run_status(task_id: str):
        try:
            return ok(streams.folder_run(task_id))
        except StreamError as exc:
            return stream_error(exc,task_id)

    @api.post("/aoi/streams/<task_id>/frames")
    def push_aoi_frame(task_id: str):
        if request.content_length is not None and request.content_length > streams.max_frame_bytes + 65536:
            return error("INVALID_FRAME", "请求体超过 AOI 帧上限", 413, task_id=task_id)
        upload = request.files.get("image")
        if upload is None:
            return error("INVALID_ARGUMENT", "需要 multipart image 图片帧", 400, task_id=task_id)
        try:
            sequence = int(request.form.get('sequence',request.form.get('frame_seq','')))
        except ValueError:
            return error("INVALID_ARGUMENT", "sequence 必须是正整数", 400, task_id=task_id)
        if sequence < 1:
            return error("INVALID_ARGUMENT", "sequence 必须是正整数", 400, task_id=task_id)
        data = upload.stream.read(streams.max_frame_bytes + 1)
        try:
            return ok(streams.submit(task_id, sequence, data, request.form.get("product_id"),request.form.get('view_type')), 202)
        except StreamError as exc:
            return stream_error(exc, task_id)

    @api.get("/aoi/streams/<task_id>")
    def aoi_stream_status(task_id: str):
        task = store.get(task_id)
        if task is None or task["stage"] != "stage1" or not isinstance(task.get("result"), dict) or "stream_id" not in task["result"]:
            return error("STREAM_NOT_FOUND", "AOI 流不存在", 404)
        metadata=streams.journal.stream(task_id)
        total,processed,failed,last,recent=streams.journal.stats(task_id)
        return ok(dict(task_id=task_id,status=task['status'],stream={**task['result'],
            'accepted':total,'processed':processed,'failed':failed,'last_sequence':last,'recent_frames':recent,
            'accepting':not metadata['closed'] if metadata else False,
            'view_types':metadata.get('view_types') if metadata else None,
            'required_views':metadata.get('required_views') if metadata else None}))

    @api.get("/aoi/streams/<task_id>/frames")
    def aoi_frame_records(task_id: str):
        try:
            limit,offset = pagination(request.args,100)
            items, total = streams.frame_records(task_id, limit, offset)
        except ValueError:
            return error("INVALID_ARGUMENT", "limit 和 offset 必须为整数", 400)
        except StreamError as exc:
            return stream_error(exc, task_id)
        return ok(dict(items=items, total=total, limit=limit, offset=offset))

    @api.get("/aoi/streams/<task_id>/frames/<int:sequence>/image")
    def aoi_frame_image(task_id: str, sequence: int):
        task = store.get(task_id)
        if task is None or task["stage"] != "stage1":
            return error("STREAM_NOT_FOUND", "AOI 流不存在", 404)
        frame = streams.journal.get(task_id, sequence)
        if frame is None or not frame["data"]:
            return error("FRAME_NOT_FOUND", "帧不存在或已过保留期", 404)
        response = Response(frame["data"], mimetype=frame["mime_type"])
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Checksum"] = frame["checksum"]
        return response

    @api.get("/aoi/streams/<task_id>/latest-frame")
    def latest_aoi_frame(task_id: str):
        try:
            data, mime_type, sequence = streams.latest_frame(task_id)
        except StreamError as exc:
            return stream_error(exc, task_id)
        response = Response(data, mimetype=mime_type)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Sequence"] = str(sequence)
        return response

    @api.post("/aoi/streams/<task_id>/close")
    def close_aoi_stream(task_id: str):
        try:
            return ok(streams.stop(task_id), 202)
        except StreamError as exc:
            return stream_error(exc, task_id)

    @api.post("/inference/grading")
    def grading():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("product_id"), str) or not payload["product_id"].strip():
            return error("INVALID_ARGUMENT", "product_id 必须是非空字符串", 400)
        if set(payload)-{'product_id','qualification_id','request_id'}:
            return error('INVALID_ARGUMENT','分级只接受产品、服务端证据编号和请求编号',400)
        try:
            evidence=qualifications.active(payload.get('qualification_id',''),payload['product_id'])
        except QualificationError as exc: return error(exc.code,str(exc),exc.status)
        device_id=getattr(g,'liante_device_id',None)
        if device_id and evidence.get('owner_device_id')!=device_id:
            return error('SOURCE_FORBIDDEN','设备无权使用其他来源合格证据',403)
        payload = {**evidence, "request_id": payload.get("request_id")}
        status, result = coordinator.gate_stage3(payload)
        if status:
            task = store.create("stage3", payload["product_id"].strip(), payload.get("request_id"),
                qualification_id=evidence['qualification_id'],owner_device_id=evidence.get('owner_device_id'))
            task_id = task["task_id"]
            store.update(task_id, status="RUNNING", operation="validate")
            store.log(task_id, "INFO", "receive", "收到多模态质量分级请求")
            result.update(task_id=task_id, product_id=payload["product_id"], stage="stage3",
                          model_name=None, model_version=None,
                          recipe_version=payload.get("recipe_version") or RECIPE_VERSION,
                          duration_ms=0)
            code = "NOT_ELIGIBLE_FOR_GRADING" if status == "FAILED" else None
            store.update(task_id, status=status, progress=100, operation="gate", result=result,
                         error_code=code, error_message=result["reason"] if code else None)
            store.log(task_id, "ERROR" if status == "FAILED" else "WARNING", "gate", result["reason"],
                      error_code=code)
            if status == "FAILED":
                return error(code, result["reason"], 422, task_id=task_id)
            return ok(dict(task_id=task_id, status=status, result=result))
        try:
            coordinator.stage3.load()
        except InferenceError as exc:
            return error(exc.code, str(exc), exc.http_status)
        try:
            reservation = coordinator.executor.reserve("stage3")
        except CapacityError:
            response, code = error("QUEUE_FULL", "推理队列已满，请稍后重试", 429)
            response.headers["Retry-After"] = "2"
            return response, code
        task = None
        try:
            task = store.create("stage3", payload["product_id"].strip(), payload.get("request_id"),
                qualification_id=evidence['qualification_id'],owner_device_id=evidence.get('owner_device_id'))
            task_id = task["task_id"]
            store.update(task_id, status="RUNNING", operation="validate")
            store.log(task_id, "INFO", "receive", "收到多模态质量分级请求")
            coordinator.queue_adapter(task_id, "stage3", payload, reservation)
        except Exception:
            if task is not None:
                store.update(task["task_id"], status="FAILED", error_code="QUEUE_SUBMISSION_FAILED",
                             error_message="推理任务提交失败")
            raise
        finally:
            reservation.release()
        return ok(dict(task_id=task_id, status="RUNNING"), 202)

    @api.post("/qualifications")
    def create_qualification():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("INVALID_ARGUMENT", "请求体必须为 JSON 对象", 400)
        try:
            bound_device = getattr(g, "liante_device_id", None)
            if bound_device:
                aoi_task = store.get(payload.get("aoi_task_id", ""))
                source_id = (aoi_task.get("result") or {}).get("source_id") if aoi_task else None
                if source_id != bound_device:
                    return error("SOURCE_FORBIDDEN", "设备身份与 AOI 证据来源不匹配", 403)
                payload["source_id"] = bound_device
            return ok(qualifications.create(payload,actor_identity()), 201)
        except QualificationError as exc:
            return error(exc.code, str(exc), exc.status)

    @api.get("/qualifications/<qualification_id>")
    def get_qualification(qualification_id: str):
        value = qualifications.get(qualification_id)
        return ok(value) if value else error("QUALIFICATION_NOT_FOUND", "合格证据不存在", 404)

    return api
