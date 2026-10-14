"""Flask server for MLP-only AutoML training and prediction."""

from __future__ import annotations

import argparse
import hmac
import logging
import re
import os
import time
import uuid
import hashlib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event

import torch
from flask import Flask, g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from automl.mlp_trainer import MLPAutoMLTrainer
from config import (GUI_DIR, MODEL_DIR, TRAIN_DATA_DIR, HOST, PORT, DEBUG,
                    LOG_LEVEL, LOG_DIR, OUTPUT_DIR, MAX_UPLOAD_SIZE,
                    DATA_DIR, AOI_MAX_STREAMS, AOI_QUEUE_CAPACITY, AOI_MAX_FRAME_BYTES,
                    AOI_IDLE_TIMEOUT_SECONDS, ADMIN_TOKEN, OPERATOR_TOKEN,
                    DEVICE_TOKEN, DEVICE_TOKENS, ALLOWED_ORIGINS, INFERENCE_RESOURCE_SLOTS,
                    INFERENCE_RESOURCE_WAIT_SECONDS, WEB_TRAIN_DEVICE, AOI_RETENTION_DAYS,
                    AOI_MAX_PIXELS, AOI_FRAME_TIMEOUT_SECONDS, AOI_MAX_JOURNAL_BYTES)
from config import MAX_SYNC_PREDICTIONS
from config import STAGE2_INPUT_DIRS, STAGE2_SCAN_MAX_ENTRIES, STAGE2_FILE_STABLE_SECONDS
from config import STAGE2_ALLOWED_DIRS, STAGE2_SOURCE_MAX_EVENTS, STAGE2_SOURCE_RETENTION_DAYS
from services.folder_input_service import FolderInputService
from api_v1 import create_api_blueprint, error as api_error
from services.inference_service import InferenceCoordinator
from services.aoi_stream_service import AOIStreamManager
from tasks.inference_store import InferenceTaskStore
from tasks.qualification_store import QualificationStore
from schemas.prediction import FilePredictRequest, PredictRequest
from schemas.training import TrainAllRequest, TrainRequest
from services.prediction_service import PredictionService
from services.training_service import TrainingService
from tasks.task_manager import TrainingTaskManager
from services.admission import CapacityError
from services.resource_gate import ResourceGate
from services.capacity import CapacityMonitor, DiskCapacityError
from services.idempotency import IdempotencyStore, normalized, RETRYABLE
from services.access_policy import PUBLIC, roles_for, actor_identity
from api.operations import create_operations_blueprint


LOGGER = logging.getLogger(__name__)


def create_app(
    model_dir: str | Path = MODEL_DIR,
    data_dir: str | Path = TRAIN_DATA_DIR,
    max_concurrent_jobs: int = 1,
) -> Flask:
    environment=os.getenv('APP_ENV','development')
    if environment not in {'development','production'}:
        raise ValueError('APP_ENV 必须为 development 或 production')
    if environment=='production':
        if DEBUG or os.getenv('DEBUG','false').lower()=='true':
            raise ValueError('production 禁止 debug')
        if not ADMIN_TOKEN:
            raise ValueError('production 必须配置管理员认证凭据')
    application = Flask(
        __name__,
        static_folder=str(GUI_DIR),
        static_url_path="/gui",
    )
    application.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    if not any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(LOG_DIR / "server.log")
               for handler in root_logger.handlers):
        handler = RotatingFileHandler(LOG_DIR / "server.log", maxBytes=10 * 1024 * 1024,
                                      backupCount=10, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(handler)
    prediction_service = PredictionService(model_dir)
    prediction_service.resource_gate = ResourceGate(INFERENCE_RESOURCE_SLOTS,
                                                   INFERENCE_RESOURCE_WAIT_SECONDS)
    inference_store = InferenceTaskStore(OUTPUT_DIR, LOG_DIR)
    task_manager = TrainingTaskManager(max_concurrent_jobs=max_concurrent_jobs, store=inference_store)
    trainer = MLPAutoMLTrainer(data_dir=data_dir, model_dir=model_dir, device=WEB_TRAIN_DEVICE)
    training_service = TrainingService(trainer, task_manager, prediction_service)
    application.extensions["prediction_service"] = prediction_service
    application.extensions["training_service"] = training_service
    application.extensions["task_manager"] = task_manager
    inference = InferenceCoordinator(inference_store, prediction_service)
    aoi_streams = AOIStreamManager(inference_store, inference.stage1, DATA_DIR,
                                   AOI_MAX_STREAMS, AOI_QUEUE_CAPACITY, AOI_MAX_FRAME_BYTES,
                                   AOI_IDLE_TIMEOUT_SECONDS, AOI_RETENTION_DAYS,
                                   AOI_MAX_PIXELS, AOI_FRAME_TIMEOUT_SECONDS,
                                   AOI_MAX_JOURNAL_BYTES)
    qualification_store = QualificationStore(OUTPUT_DIR, inference_store, aoi_streams.journal)
    inference.qualifications=qualification_store
    folder_inputs = FolderInputService(prediction_service, STAGE2_INPUT_DIRS,
                                      STAGE2_SCAN_MAX_ENTRIES, STAGE2_FILE_STABLE_SECONDS,
                                      db_path=OUTPUT_DIR / "stage2_inputs.sqlite3",
                                      allowed_directories=STAGE2_ALLOWED_DIRS,
                                      max_events=STAGE2_SOURCE_MAX_EVENTS,
                                      retention_days=STAGE2_SOURCE_RETENTION_DAYS)
    application.extensions["stage2_inputs"] = folder_inputs
    application.extensions["inference_store"] = inference_store
    application.extensions["inference"] = inference
    application.extensions["aoi_streams"] = aoi_streams
    application.extensions["qualifications"] = qualification_store
    application.extensions['capacity']=CapacityMonitor([DATA_DIR,OUTPUT_DIR,LOG_DIR])
    aoi_streams.capacity_monitor=application.extensions['capacity']
    application.extensions['idempotency']=IdempotencyStore(inference_store.index)
    application.extensions['idempotency'].attach_domain(qualification_store.path)
    application.extensions['idempotency'].attach_domain(folder_inputs.registry.path)
    application.extensions['started_at']=datetime.now(timezone.utc).isoformat()
    application.extensions['started_monotonic']=time.monotonic()
    application.config['APP_ENV']=environment
    draining = Event()
    application.extensions["draining"] = draining
    application.register_blueprint(create_api_blueprint(inference_store, inference, prediction_service,
                                                        aoi_streams, qualification_store, task_manager, folder_inputs))
    operations=create_operations_blueprint(inference_store,aoi_streams,inference,qualification_store,folder_inputs,task_manager)
    application.register_blueprint(operations)
    application.extensions['reviews']=operations.review_store

    @application.before_request
    def enforce_access():
        g.request_id=request.headers.get('X-Request-ID','')[:200] or uuid.uuid4().hex
        origin = request.headers.get("Origin")
        same_origin = origin == request.host_url.rstrip("/")
        if origin and not same_origin and origin not in ALLOWED_ORIGINS:
            return api_error('ORIGIN_DENIED','来源未授权',403)
        if request.method == "OPTIONS":
            return "", 204
        permitted=roles_for(request.method,request.url_rule.rule if request.url_rule else request.path)
        if permitted==['public']:
            return None
        credentials = {"admin": ADMIN_TOKEN, "operator": OPERATOR_TOKEN, "device": DEVICE_TOKEN}
        if not any(credentials.values()) and not DEVICE_TOKENS:
            g.liante_role='local'
            g.liante_device_id=None
            return None  # Explicitly supported for local development only.
        role = ""
        supplied = ""
        basic = request.authorization
        if basic and basic.type.lower() == "basic":
            role, supplied = basic.username or "", basic.password or ""
        elif request.headers.get("Authorization", "").startswith("Bearer "):
            supplied = request.headers["Authorization"][7:]
            role = request.headers.get("X-Liante-Role", "device")
        device_id = None
        if role.startswith("device:"):
            device_id = role[7:]
            role = "device"
            secret = DEVICE_TOKENS.get(device_id, "")
        else:
            secret = credentials.get(role, "")
            if role == "device" and request.headers.get("X-Liante-Device-ID"):
                device_id = request.headers["X-Liante-Device-ID"]
                secret = DEVICE_TOKENS.get(device_id, "")
        if not secret or not hmac.compare_digest(supplied, secret):
            response,status=api_error('UNAUTHORIZED','需要有效身份凭据',401)
            response.headers['WWW-Authenticate']='Basic realm="Liante"'
            return response,status
        g.liante_role = role
        g.liante_device_id = device_id
        if role not in permitted:
            return api_error('FORBIDDEN','当前身份无权执行此操作',403)
        if role=='device':
            if not device_id:
                return api_error('DEVICE_ID_REQUIRED','需要独立设备身份与凭据',403)
            args=request.view_args or {}
            task_id=args.get('task_id')
            if task_id:
                value=inference_store.get(task_id)
                if value is None: return api_error('TASK_NOT_FOUND','任务不存在',404)
                owner=value.get('owner_device_id') or (value.get('result') or {}).get('source_id')
                if owner!=device_id: return api_error('SOURCE_FORBIDDEN','设备无权访问其他来源任务或流',403)
            qualification_id=args.get('qualification_id')
            if qualification_id:
                value=qualification_store.get(qualification_id)
                if value is None: return api_error('QUALIFICATION_NOT_FOUND','合格证据不存在',404)
                if value.get('owner_device_id')!=device_id:
                    return api_error('SOURCE_FORBIDDEN','设备无权访问其他来源证据',403)
        return None

    @application.before_request
    def admit_request():
        if request.method!='POST': return None
        payload=request.get_json(silent=True) if request.is_json else request.form.to_dict()
        payload=payload if isinstance(payload,dict) else {}
        if payload.get('request_id') and request.headers.get('X-Request-ID') and payload['request_id']!=request.headers['X-Request-ID']:
            return api_error('INVALID_ARGUMENT','JSON request_id 与 X-Request-ID 不一致',400)
        request_id=payload.get('request_id') or request.headers.get('X-Request-ID')
        # Frames have journal-level sequence/checksum idempotency, including after close.
        use_receipt=request_id is not None and not request.path.endswith('/frames')
        if use_receipt:
            if not isinstance(request_id,str) or not 1 <= len(request_id.strip()) <= 200:
                return api_error('INVALID_ARGUMENT','request_id 必须是 1..200 字符',400)
            receipts=application.extensions['idempotency']
            if not receipts.lock.acquire(timeout=0.25):
                response,status=api_error('SUBMISSION_BUSY','正在确认提交，请稍后重试同一 request_id',429)
                response.headers['Retry-After']='1';return response,status
            g.receipt_lock=receipts.lock
            actor=actor_identity(); operation=request.method+' '+request.path
            try:
                body=dict(payload)
                for name,upload in request.files.items():
                    data=upload.stream.read(MAX_UPLOAD_SIZE+1);upload.stream.seek(0)
                    body[name]=hashlib.sha256(data).hexdigest()
                fingerprint=normalized(body)
            except (ValueError,TypeError): return api_error('INVALID_ARGUMENT','请求参数不能包含非有限数值',400)
            prior=receipts.get(actor,operation,request_id)
            if prior:
                if prior['fingerprint']!=fingerprint:
                    return api_error('REQUEST_REPLAY_CONFLICT','同一身份、操作与 request_id 不可用于不同参数',409)
                replay=prior['body'];replay['replayed']=True
                if isinstance(replay.get('data'),dict): replay['data']['replayed']=True
                return jsonify(replay),200 if prior['status']<300 else prior['status']
            if not receipts.capacity(): return api_error('REQUEST_HISTORY_FULL','请求回执容量已满，请备份归档',429)
            g.idempotency_context=(actor,operation,request_id,fingerprint,receipts)
            g.request_id=request_id
        if draining.is_set() and request.path!='/api/v1/admin/drain' and not request.path.endswith(('/close','/cancel','/revoke')):
            response,status=api_error('SERVICE_DRAINING','服务正在排空，暂不接收新任务',503)
            response.headers['Retry-After']='5';return response,status
        if request.path in {'/api/v1/tasks','/api/v1/aoi/streams','/api/v1/aoi/folder-runs',
                '/api/v1/inference/ea-bias','/api/v1/inference/ea-bias/folder','/api/v1/inference/aoi',
                '/api/v1/inference/grading','/automl/train','/automl/train-all'} or request.path.endswith('/retry'):
            try: application.extensions['capacity'].admit()
            except DiskCapacityError as exc:
                response,status=api_error(exc.code,str(exc),507)
                response.headers['Retry-After']='60';return response,status

    @application.after_request
    def capture_receipt(response):
        try:
            context=getattr(g,'idempotency_context',None)
            body=response.get_json(silent=True)
            if request.path.startswith('/api/v1/') and isinstance(body,dict):
                body.setdefault('request_id',getattr(g,'request_id',uuid.uuid4().hex))
                body.setdefault('timestamp',datetime.now(timezone.utc).isoformat())
                response.set_data(application.json.dumps(body))
            linked=False
            if context:
                actor,operation,request_id,fingerprint,receipts=context
                linked=receipts.get(actor,operation,request_id) is not None
            if context and body is not None and (response.status_code not in RETRYABLE or linked):
                actor,operation,request_id,fingerprint,receipts=context
                receipts.save(actor,operation,request_id,fingerprint,body,response.status_code,
                              (body.get('data') or {}).get('task_id') if isinstance(body.get('data'),dict) else body.get('task_id'))
        except Exception:
            LOGGER.exception('Response receipt persistence failed; task link remains authoritative')
        finally:
            if getattr(g,'receipt_lock',None): g.receipt_lock.release();g.receipt_lock=None
        response.headers['X-Request-ID']=getattr(g,'request_id','')
        return response

    @application.post("/api/v1/admin/drain")
    def begin_drain():
        draining.set()
        with aoi_streams._lock:
            stream_ids = list(aoi_streams._streams)
        for stream_id in stream_ids:
            try:
                aoi_streams.stop(stream_id)
            except Exception:
                LOGGER.exception("Unable to drain AOI stream %s", stream_id)
        return jsonify(code=0, message="success", data={"draining": True,
            "aoi_streams": len(stream_ids), "inference_queue": inference.executor.stats(),
            "training_queue": task_manager.capacity()})

    @application.errorhandler(Exception)
    def handle_unexpected(exc):
        if isinstance(exc,DiskCapacityError):
            response,status=api_error(exc.code,str(exc),507);response.headers['Retry-After']='60';return response,status
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/v1/"):
                return api_error({413:'INVALID_ARGUMENT',404:'NOT_FOUND',405:'METHOD_NOT_ALLOWED'}.get(exc.code,'HTTP_ERROR'),
                                 exc.description, exc.code or 500)
            return exc
        LOGGER.exception("Unhandled request error path=%s", request.path)
        if request.path.startswith("/api/v1/"):
            return api_error("INTERNAL_ERROR", "服务器内部错误，请查看服务日志", 500)
        return jsonify(status="error", error="服务器内部错误，请查看服务日志"), 500

    @application.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Liante-Role, X-Liante-Device-ID"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @application.get("/")
    def root():
        return jsonify(
            status="ok",
            service="Liante MLP AutoML",
            prediction_gui="/gui/index.html",
            automl_gui="/gui/train.html",
        )

    @application.get("/health")
    def health():
        return jsonify(
            status="ok",
            device=str(prediction_service.device),
            available_channels=prediction_service.available_channels(),
        )

    @application.get("/info")
    def info():
        return jsonify(
            models=["MLP"],
            channels=list(range(8)),
            trained_channels=prediction_service.available_channels(),
        )

    @application.route("/predict", methods=["POST", "OPTIONS"])
    def predict():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = PredictRequest.model_validate(request.get_json(silent=True))
            if len(payload.data) > MAX_SYNC_PREDICTIONS:
                return jsonify(status="error", error="同步预测条数超过上限，请使用批量任务"), 413
            predictions = prediction_service.predict(payload.channel, payload.data)
            return jsonify(
                status="success",
                channel=payload.channel,
                predictions=predictions,
            )
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        except FileNotFoundError as exc:
            return jsonify(status="error", error=str(exc)), 404
        except ValueError as exc:
            return jsonify(status="error", error=str(exc)), 400
        except Exception:
            LOGGER.exception("prediction failed")
            return jsonify(status="error", error="模型推理失败，请查看服务日志"), 500

    @application.route("/predict-file", methods=["POST", "OPTIONS"])
    def predict_file():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = FilePredictRequest.model_validate(request.get_json(silent=True))
            count = prediction_service.batch_work([{"file_path": payload.file_path,
                                                    "sheet_name": payload.sheet_name,
                                                    "channels": [payload.channel]}])
            if count > MAX_SYNC_PREDICTIONS:
                return jsonify(status="error", error="同步文件预测条数超过上限，请使用批量任务"), 413
            predictions, source = prediction_service.predict_file(
                payload.channel,
                payload.file_path,
                payload.sheet_name,
                payload.feature_columns,
            )
            return jsonify(
                status="success",
                channel=payload.channel,
                source=source,
                predictions=predictions,
            )
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        except (FileNotFoundError, ValueError, KeyError, IndexError) as exc:
            return jsonify(status="error", error=str(exc)), 400
        except Exception:
            LOGGER.exception("file prediction failed")
            return jsonify(status="error", error="文件预测失败，请查看服务日志"), 500

    @application.route("/automl/train", methods=["POST", "OPTIONS"])
    def automl_train():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = TrainRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        try:
            task_id = training_service.queue_channel(payload)
        except CapacityError:
            return jsonify(status="busy", error="训练队列已满，请稍后重试"), 429, {"Retry-After": "5"}
        except ValueError as exc:
            return jsonify(status="error", error=str(exc)), 400
        return jsonify(task_id=task_id, status="queued"), 202

    @application.route("/automl/train-all", methods=["POST", "OPTIONS"])
    def automl_train_all():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = TrainAllRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        try:
            task_id = training_service.queue_all(payload)
        except CapacityError:
            return jsonify(status="busy", error="训练队列已满，请稍后重试"), 429, {"Retry-After": "5"}
        except ValueError as exc:
            return jsonify(status="error", error=str(exc)), 400
        return jsonify(task_id=task_id, status="queued", channels=payload.channels), 202

    @application.get("/automl/tasks/<task_id>")
    def automl_task(task_id: str):
        task = task_manager.get(task_id)
        if task is None:
            return jsonify(status="error", error="Training task not found"), 404
        return jsonify(task)

    @application.get("/automl/tasks/<task_id>/result")
    def automl_result(task_id: str):
        task_result = task_manager.get_result(task_id)
        if task_result is None:
            return jsonify(status="error", error="Training task not found"), 404
        task_status, result, error = task_result
        if task_status == "failed":
            return jsonify(status="failed", error=error or "Training failed"), 500
        if task_status != "completed":
            return jsonify(
                task_id=task_id,
                status=task_status,
                message="Training result is not ready",
            ), 202
        return jsonify(result)

    @application.post("/automl/tasks/<task_id>/cancel")
    def automl_cancel(task_id: str):
        if not task_manager.cancel(task_id):
            return jsonify(status="error", error="Task not cancellable"), 409
        return jsonify(task_id=task_id, status="cancelled")

    return application


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False)
    if not errors:
        return "请求参数无效"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', '请求参数无效')}" if location else str(first.get("msg"))


app = None if __name__=='__main__' or os.getenv('LIANTE_FACTORY_ONLY')=='1' else create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Liante MLP AutoML Flask server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--debug", action="store_true", default=DEBUG)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    args = parser.parse_args()
    if os.getenv('APP_ENV','development')=='production' and args.debug:
        parser.error('production 禁止 --debug')
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper(), logging.INFO))
    LOGGER.info(
        "starting Flask host=%s port=%s device=%s",
        args.host,
        args.port,
        "cuda" if torch.cuda.is_available() else "cpu",
    )
    from deploy.runtime import acquire_server_lock
    acquire_server_lock(Path(__file__).resolve().parent,args.host,args.port)
    (app or create_app()).run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()

