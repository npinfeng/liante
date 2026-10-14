"""Project-wide paths and runtime defaults."""

from __future__ import annotations

import os
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
APP_ENV = os.environ.get('APP_ENV','development')
APP_VERSION = '2.0.0'
TRAIN_DATA_DIR = Path(
    os.environ.get(
        "LIANTE_TRAIN_DATA_DIR",
        PROJECT_ROOT / "train_data" / "data_v5" / "split_by_channel",
    )
).resolve()
MODEL_DIR = Path(os.environ.get("LIANTE_MODEL_DIR",
                                os.environ.get("STAGE2_AUTOML_MODEL_DIR", PROJECT_ROOT / "saved_models"))).resolve()
GUI_DIR = PROJECT_ROOT / "gui"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_DIR = Path(os.environ.get("LOG_DIR", PROJECT_ROOT / "logs")).resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data")).resolve()
STAGE2_INPUT_DIRS = os.environ.get("STAGE2_INPUT_DIRS", '' if APP_ENV=='production' else str(DATA_DIR / "stage2_input"))
STAGE2_ALLOWED_DIRS = os.environ.get("STAGE2_ALLOWED_DIRS", "")
STAGE2_SOURCE_MAX_EVENTS = int(os.environ.get("STAGE2_SOURCE_MAX_EVENTS", "20000"))
STAGE2_SOURCE_RETENTION_DAYS = int(os.environ.get("STAGE2_SOURCE_RETENTION_DAYS", "30"))
STAGE2_SCAN_MAX_ENTRIES = int(os.environ.get("STAGE2_SCAN_MAX_ENTRIES", "10000"))
STAGE2_FILE_STABLE_SECONDS = float(os.environ.get("STAGE2_FILE_STABLE_SECONDS", "2"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", PROJECT_ROOT / "output")).resolve()
STAGE1_MODEL_PATH = os.environ.get("STAGE1_MODEL_PATH", "")
STAGE1_ADAPTER = os.environ.get("STAGE1_ADAPTER", "")
STAGE2_AUTOML_MODEL_DIR = MODEL_DIR
STAGE3_MODEL_PATH = os.environ.get("STAGE3_MODEL_PATH", "")
STAGE3_ADAPTER = os.environ.get("STAGE3_ADAPTER", "")
STAGE3_MIN_CONFIDENCE = float(os.environ.get("STAGE3_MIN_CONFIDENCE", "0.8"))
QUALIFICATION_MAX_AGE_HOURS = float(os.environ.get("QUALIFICATION_MAX_AGE_HOURS", "168"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", str(20 * 1024 * 1024)))
AOI_MAX_STREAMS = int(os.environ.get("AOI_MAX_STREAMS", "4"))
AOI_QUEUE_CAPACITY = int(os.environ.get("AOI_QUEUE_CAPACITY", "8"))
AOI_MAX_FRAME_BYTES = int(os.environ.get("AOI_MAX_FRAME_BYTES", str(2 * 1024 * 1024)))
AOI_IDLE_TIMEOUT_SECONDS = int(os.environ.get("AOI_IDLE_TIMEOUT_SECONDS", "300"))
STAGE2_WORKERS = int(os.environ.get("STAGE2_WORKERS", "1"))
STAGE3_WORKERS = int(os.environ.get("STAGE3_WORKERS", "1"))
STAGE2_WAITING = int(os.environ.get("STAGE2_WAITING", "4"))
STAGE3_WAITING = int(os.environ.get("STAGE3_WAITING", "4"))
INFERENCE_RESOURCE_SLOTS = int(os.environ.get("INFERENCE_RESOURCE_SLOTS", "1"))
INFERENCE_RESOURCE_WAIT_SECONDS = float(os.environ.get("INFERENCE_RESOURCE_WAIT_SECONDS", "30"))
INFERENCE_QUEUE_WAIT_SECONDS = float(os.environ.get("INFERENCE_QUEUE_WAIT_SECONDS", "120"))
INFERENCE_JOB_TIMEOUT_SECONDS = float(os.environ.get("INFERENCE_JOB_TIMEOUT_SECONDS", "600"))
AOI_FRAME_TIMEOUT_SECONDS = float(os.environ.get("AOI_FRAME_TIMEOUT_SECONDS", "30"))
WEB_TRAIN_DEVICE = os.environ.get("WEB_TRAIN_DEVICE", "cpu")
AOI_RETENTION_DAYS = int(os.environ.get("AOI_RETENTION_DAYS", "30"))
AOI_MAX_JOURNAL_BYTES = int(os.environ.get("AOI_MAX_JOURNAL_BYTES", str(20 * 1024 * 1024 * 1024)))
ALERT_AOI_QUEUE_AGE_SECONDS = float(os.environ.get("ALERT_AOI_QUEUE_AGE_SECONDS", "5"))
ALERT_INFERENCE_QUEUE_AGE_SECONDS = float(os.environ.get("ALERT_INFERENCE_QUEUE_AGE_SECONDS", "30"))
ALERT_AOI_REJECTION_RATE = float(os.environ.get("ALERT_AOI_REJECTION_RATE", "0.1"))
ALERT_AOI_FRAME_MS = float(os.environ.get("ALERT_AOI_FRAME_MS", "1000"))
AOI_MAX_PIXELS = int(os.environ.get("AOI_MAX_PIXELS", "20000000"))
TRAINING_WAITING = int(os.environ.get("TRAINING_WAITING", "2"))
MAX_TRAINING_WORK = int(os.environ.get("MAX_TRAINING_WORK", "200000"))
MAX_BATCH_PREDICTIONS = int(os.environ.get("MAX_BATCH_PREDICTIONS", "100000"))
MAX_BATCH_RESULT_BYTES = int(os.environ.get("MAX_BATCH_RESULT_BYTES", str(16 * 1024 * 1024)))
PREDICTION_CHUNK_ROWS = int(os.environ.get("PREDICTION_CHUNK_ROWS", "1024"))
MAX_SYNC_PREDICTIONS = int(os.environ.get("MAX_SYNC_PREDICTIONS", "5000"))
MAX_PREDICTION_FILE_BYTES = int(os.environ.get("MAX_PREDICTION_FILE_BYTES", str(32 * 1024 * 1024)))
PREDICTION_ALLOWED_DIRS = os.environ.get("PREDICTION_ALLOWED_DIRS", "")
AOI_ALLOWED_DIRS = os.environ.get("AOI_ALLOWED_DIRS", "")
ADMIN_TOKEN = os.environ.get("LIANTE_ADMIN_TOKEN", "")
OPERATOR_TOKEN = os.environ.get("LIANTE_OPERATOR_TOKEN", "")
DEVICE_TOKEN = os.environ.get("LIANTE_DEVICE_TOKEN", "")
DEVICE_TOKENS = json.loads(os.environ.get("LIANTE_DEVICE_TOKENS", "{}"))
ALLOWED_ORIGINS = tuple(value.strip() for value in os.environ.get("LIANTE_ALLOWED_ORIGINS", "").split(",") if value.strip())
MODEL_VERSION = os.environ.get("MODEL_VERSION", "artifact")
RECIPE_VERSION = os.environ.get("RECIPE_VERSION", "unconfigured")

N_CHANNELS = 8
INPUT_DIM = 4
OUTPUT_DIM = 2
RANDOM_SEED = 42

