"""Flask server for MLP-only AutoML training and prediction."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import torch
from flask import Flask, jsonify, request
from pydantic import ValidationError

from automl.mlp_trainer import MLPAutoMLTrainer
from config import GUI_DIR, MODEL_DIR, TRAIN_DATA_DIR
from schemas.prediction import FilePredictRequest, PredictRequest
from schemas.training import TrainAllRequest, TrainRequest
from services.prediction_service import PredictionService
from services.training_service import TrainingService
from tasks.task_manager import TrainingTaskManager


LOGGER = logging.getLogger(__name__)


def create_app(
    model_dir: str | Path = MODEL_DIR,
    data_dir: str | Path = TRAIN_DATA_DIR,
    max_concurrent_jobs: int = 1,
) -> Flask:
    application = Flask(
        __name__,
        static_folder=str(GUI_DIR),
        static_url_path="/gui",
    )
    prediction_service = PredictionService(model_dir)
    task_manager = TrainingTaskManager(max_concurrent_jobs=max_concurrent_jobs)
    trainer = MLPAutoMLTrainer(data_dir=data_dir, model_dir=model_dir)
    training_service = TrainingService(trainer, task_manager, prediction_service)
    application.extensions["prediction_service"] = prediction_service
    application.extensions["training_service"] = training_service
    application.extensions["task_manager"] = task_manager

    @application.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
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

    @application.route("/automl/train", methods=["POST", "OPTIONS"])
    def automl_train():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = TrainRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        task_id = training_service.queue_channel(payload)
        return jsonify(task_id=task_id, status="queued"), 202

    @application.route("/automl/train-all", methods=["POST", "OPTIONS"])
    def automl_train_all():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            payload = TrainAllRequest.model_validate(request.get_json(silent=True))
        except ValidationError as exc:
            return jsonify(status="error", error=_validation_message(exc)), 400
        task_id = training_service.queue_all(payload)
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

    return application


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False)
    if not errors:
        return "请求参数无效"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', '请求参数无效')}" if location else str(first.get("msg"))


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Liante MLP AutoML Flask server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    LOGGER.info(
        "starting Flask host=%s port=%s device=%s",
        args.host,
        args.port,
        "cuda" if torch.cuda.is_available() else "cpu",
    )
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)


if __name__ == "__main__":
    main()
