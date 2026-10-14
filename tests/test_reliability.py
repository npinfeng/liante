from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from services.admission import BoundedExecutor, CapacityError, InferencePools
from services.aoi_stream_service import AOIStreamManager, Frame, StreamError
from services.frame_journal import FrameJournal, FrameJournalFull
from services.inference_service import InferenceError, ModelAdapterService
from tasks.inference_store import InferenceTaskStore
from deploy.archive_tasks import archive_terminal_tasks


def image_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_executor_bounds_waiting_and_releases_cancelled_job():
    entered, release = Event(), Event()
    executor = BoundedExecutor(1, 1, "test-admission")

    def blocked():
        entered.set()
        assert release.wait(5)

    try:
        first = executor.submit(blocked)
        assert entered.wait(5)
        queued = executor.submit(lambda: None)
        with pytest.raises(CapacityError):
            executor.submit(lambda: None)
        assert queued.cancel()
        third = executor.submit(lambda: 3)
        release.set()
        first.result(timeout=5)
        assert third.result(timeout=5) == 3
        assert executor.stats()["outstanding"] == 0
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_capacity_reservation_precedes_durable_task_creation():
    executor = BoundedExecutor(1, 0, "reserve-test")
    created = []
    try:
        held = executor.reserve()
        with pytest.raises(CapacityError):
            executor.reserve()
        assert created == []
        held.release()
        next_slot = executor.reserve()
        created.append("task")
        assert executor.submit_reserved(next_slot, lambda: "done").result(timeout=2) == "done"
        assert executor.stats()["outstanding"] == 0
    finally:
        executor.shutdown(wait=True)


def test_prediction_result_budget_is_atomic(tmp_path: Path):
    store = InferenceTaskStore(tmp_path / "output", tmp_path / "logs")
    task_id = store.create("stage2", "P")["task_id"]
    row = {"row": 2, "ea": 1.0, "bias": 2.0}
    store.index.append_predictions(task_id, 1, 0, [row], 100)
    with pytest.raises(ValueError):
        store.index.append_predictions(task_id, 1, 0, [{**row, "row": 3}], 40)
    items, total = store.index.predictions(task_id, 1, 0, 10, 0)
    assert total == 1 and items == [row]


def test_archive_requires_apply_and_preserves_verified_task(tmp_path: Path):
    output, logs = tmp_path / "output", tmp_path / "logs"
    store = InferenceTaskStore(output, logs)
    task_id = store.create("stage2", "P")["task_id"]
    store.index.append_predictions(task_id, 1, 0, [{"row": 2, "ea": 1.0, "bias": 2.0}], 100)
    store.update(task_id,status='RUNNING')
    store.update(task_id, status="COMPLETED", result={"files": []})
    with sqlite3.connect(store.index.path) as db:
        db.execute("UPDATE tasks SET updated_at='2020-01-01T00:00:00+00:00' WHERE task_id=?", (task_id,))
    assert archive_terminal_tasks(output, logs, 365) == {"eligible": 1, "archived": 0, "protected": 0}
    assert store.get(task_id) is not None
    assert archive_terminal_tasks(output, logs, 365, apply=True) == {"eligible": 1, "archived": 1, "protected": 0}
    archive = output / "archive" / "2020" / f"{task_id}.zip"
    with zipfile.ZipFile(archive) as saved:
        assert saved.testzip() is None
        assert saved.read("task.json")
        assert b'"row":2' in saved.read("predictions.jsonl")
    assert store.get(task_id) is None


def test_stage3_has_separate_execution_capacity():
    entered, release = Event(), Event()
    pools = InferencePools(1, 1, 0, 0)

    def blocked():
        entered.set()
        assert release.wait(5)

    try:
        first = pools.submit("stage2", blocked)
        assert entered.wait(5)
        assert pools.submit("stage3", lambda: "grade-ready").result(timeout=2) == "grade-ready"
        release.set()
        first.result(timeout=5)
    finally:
        release.set()
        pools.shutdown(wait=True)


def test_aoi_ack_is_durable_and_replayed_after_restart(tmp_path: Path):
    store = InferenceTaskStore(tmp_path / "output", tmp_path / "logs")
    task = store.create("stage1", "PRODUCT-1")
    store.update(task["task_id"], status="RUNNING", result={
        "stream_id": task["task_id"], "source_id": "CAM-1", "accepting": False, "rejected": 0})
    journal = FrameJournal(tmp_path / "data")
    image = image_bytes()
    frame = Frame(1, "PRODUCT-1", image, "image/png", ".png", "checksum")
    journal.insert(task["task_id"], frame)

    class Adapter:
        def predict(self, payload):
            return {"qualified": True, "defects": []}

    restored = InferenceTaskStore(tmp_path / "output", tmp_path / "logs")
    AOIStreamManager(restored, Adapter(), tmp_path / "data", idle_timeout_seconds=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = restored.get(task["task_id"])
        if value["status"] == "COMPLETED":
            break
        time.sleep(0.02)
    assert value["status"] == "COMPLETED"
    assert value["result"]["accepted"] == value["result"]["processed"] == 1
    assert journal.get(task["task_id"], 1)["data"] == image
    records, total = journal.records(task["task_id"])
    assert total == 1 and records[0]["output"]["qualified"] is True


def test_aoi_does_not_ack_if_journal_fails(tmp_path: Path, monkeypatch):
    store = InferenceTaskStore(tmp_path / "output", tmp_path / "logs")

    class Adapter:
        def predict(self, payload):
            return {"qualified": True, "defects": []}

    streams = AOIStreamManager(store, Adapter(), tmp_path / "data")
    task_id = streams.start("PRODUCT-1", "CAM-1")["task_id"]

    def unavailable(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(streams.journal, "insert", unavailable)
    with pytest.raises(StreamError) as failure:
        streams.submit(task_id, 1, image_bytes())
    assert failure.value.code == "FRAME_STORAGE_FAILED"
    assert store.get(task_id)["result"]["accepted"] == 0
    streams.stop(task_id)


def test_aoi_watchdog_marks_stalled_model_as_failed(tmp_path: Path):
    entered, release = Event(), Event()
    store = InferenceTaskStore(tmp_path / "output", tmp_path / "logs")

    class SlowAdapter:
        def predict(self, payload):
            entered.set()
            release.wait(5)
            return {"qualified": True, "defects": []}

    streams = AOIStreamManager(store, SlowAdapter(), tmp_path / "data",
                               frame_timeout_seconds=0.1, idle_timeout_seconds=5)
    task_id = streams.start("PRODUCT-1", "CAM-1")["task_id"]
    streams.submit(task_id, 1, image_bytes())
    try:
        assert entered.wait(5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and store.get(task_id)["status"] != "FAILED":
            time.sleep(0.02)
        assert store.get(task_id)["error_code"] == "FRAME_TIMEOUT"
    finally:
        release.set()


def test_aoi_journal_has_hard_storage_budget(tmp_path: Path):
    image = image_bytes()
    journal = FrameJournal(tmp_path / "data", max_bytes=len(image))
    journal.register_stream('stream',{'product_id':'PRODUCT-1','source_id':'CAM','required_views':['UNSPECIFIED'],'view_types':['UNSPECIFIED']})
    journal.insert("stream", Frame(1, "PRODUCT-1", image, "image/png", ".png", "sum"))
    assert journal.usage()["journal_frame_bytes"] == len(image)
    with pytest.raises(FrameJournalFull):
        journal.insert("stream", Frame(2, "PRODUCT-1", image, "image/png", ".png", "sum"))


def test_aoi_retention_removes_only_original_image(tmp_path: Path):
    journal = FrameJournal(tmp_path / "data", retention_days=1)
    journal.register_stream('stream',{'product_id':'PRODUCT-1','source_id':'CAM','required_views':['UNSPECIFIED'],'view_types':['UNSPECIFIED']})
    image = image_bytes()
    journal.insert("stream", Frame(1, "PRODUCT-1", image, "image/png", ".png", "sum"))
    journal.finish("stream", 1, {"status": "FAILED", "error_code": "MODEL_NOT_CONFIGURED"})
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE frames SET updated_at='2020-01-01T00:00:00+00:00'")
    assert journal.prune() == 1
    assert journal.get("stream", 1)["data"] == b""
    assert journal.qualification_counts("stream", "PRODUCT-1") == (1, 1, 0)
    assert journal.usage()["journal_frame_bytes"] == 0


def test_stage1_adapter_rejects_unqualified_contract_output(tmp_path: Path):
    model_path = tmp_path / "model.bin"
    model_path.write_bytes(b"model")
    service = ModelAdapterService("stage1", str(model_path), "test:Adapter")

    class BadAdapter:
        def predict(self, payload):
            return {"defects": []}

    service._adapter = BadAdapter()
    with pytest.raises(InferenceError) as failure:
        service.predict({"product_id": "P"})
    assert failure.value.code == "INVALID_MODEL_OUTPUT"


def test_authentication_and_roles(tmp_path: Path, monkeypatch):
    import backend_server

    monkeypatch.setattr(backend_server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(backend_server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(backend_server, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend_server, "ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr(backend_server, "OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setattr(backend_server, "DEVICE_TOKEN", "device-secret")
    monkeypatch.setattr(backend_server, "DEVICE_TOKENS", {"CAM-1": "cam-1-secret", "CAM-2": "cam-2-secret"})
    app = backend_server.create_app(model_dir=tmp_path / "models", data_dir=tmp_path / "training")
    client = app.test_client()
    assert client.get("/api/v1/health").status_code == 200
    readiness = client.get("/api/v1/ready")
    assert readiness.status_code == 503
    assert readiness.json["code"] == "NOT_READY"
    assert readiness.json["data"]["stage1"] == "not_configured"
    assert client.get("/gui/index.html").status_code == 401
    assert client.get("/gui/index.html", auth=("admin", "admin-secret")).status_code == 200
    assert client.post("/automl/train", auth=("operator", "operator-secret"),
                       json={"channel": 0}).status_code == 403
    assert client.get("/api/v1/tasks/unknown", auth=("device", "device-secret")).status_code == 403
    assert client.post("/api/v1/qualifications", auth=("device", "device-secret"), json={}).status_code == 403
    cam1 = {"Authorization": "Bearer cam-1-secret", "X-Liante-Device-ID": "CAM-1"}
    cam2 = {"Authorization": "Bearer cam-2-secret", "X-Liante-Device-ID": "CAM-2"}
    opened = client.post("/api/v1/aoi/streams", headers=cam1,
                         json={"product_id": "P", "source_id": "CAM-1"})
    assert opened.status_code == 201
    task_id = opened.json["data"]["task_id"]
    assert client.get(f"/api/v1/aoi/streams/{task_id}", headers=cam2).status_code == 403
    client.post(f"/api/v1/aoi/streams/{task_id}/close", headers=cam1)
    assert client.post("/api/v1/admin/drain", auth=("operator", "operator-secret")).status_code == 403
    assert client.post("/api/v1/admin/drain", auth=("admin", "admin-secret")).status_code == 200
    assert client.get("/api/v1/health").json["data"]["draining"] is True
    assert client.post("/api/v1/aoi/streams", headers=cam1,
                       json={"product_id": "P", "source_id": "CAM-1"}).status_code == 503
    app.extensions["inference"].executor.shutdown(wait=True)
    app.extensions["task_manager"].shutdown(wait=True)


def test_health_alerts_backlog_rejection_and_slow_aoi(tmp_path: Path, monkeypatch):
    import backend_server

    monkeypatch.setattr(backend_server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(backend_server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(backend_server, "DATA_DIR", tmp_path / "data")
    app = backend_server.create_app(model_dir=tmp_path / "models", data_dir=tmp_path / "training")
    streams = app.extensions["aoi_streams"]
    normal = streams.health()
    monkeypatch.setattr(streams, "health", lambda: {**normal, "oldest_queued_seconds": 10,
        "rejected_frames": 10, "rejection_rate": 0.5,
        "processed_frames": 10, "average_frame_ms": 2000})
    alerts = app.test_client().get("/api/v1/health").json["data"]["alerts"]
    assert {"AOI_QUEUE_AGE_HIGH", "AOI_REJECTION_RATE_HIGH", "AOI_PROCESSING_SLOW"} <= set(alerts)
    app.extensions["inference"].executor.shutdown(wait=True)
    app.extensions["task_manager"].shutdown(wait=True)
