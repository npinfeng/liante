from __future__ import annotations

import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pandas as pd
import pytest
from PIL import Image

import backend_server
from test_flask_api import write_zero_artifact


@pytest.fixture
def api(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(backend_server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(backend_server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(backend_server, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(__import__("api_v1"), "DATA_DIR", tmp_path / "data")
    model_dir = tmp_path / "models"
    write_zero_artifact(model_dir, 0)
    write_zero_artifact(model_dir, 1)
    app = backend_server.create_app(model_dir=model_dir, data_dir=tmp_path / "training")
    app.extensions["inference"].stage1.model_path = ""
    app.extensions["inference"].stage1.adapter_ref = ""
    app.extensions["inference"].stage3.model_path = ""
    app.extensions["inference"].stage3.adapter_ref = ""
    yield app.test_client(), tmp_path, app
    app.extensions["inference"].executor.shutdown(wait=True)
    app.extensions["task_manager"].shutdown(wait=True)


def wait_task(client, task_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").get_json()["data"]
        if task["status"] in {"COMPLETED", "FAILED", "REVIEW_REQUIRED"}:
            return task
        time.sleep(0.02)
    raise AssertionError("task timed out")


def qualified_evidence(client, app, product_id="P"):
    store = app.extensions["inference_store"]
    aoi = store.create("stage1", product_id)
    store.update(aoi["task_id"], status="COMPLETED", result={"qualified": True})
    stage2 = store.create("stage2", product_id)
    store.update(stage2["task_id"], status="COMPLETED", result={"files": [
        {"channel": 0, "source": {"row_count": 1}, "predictions": [{"row": 2, "ea": 1, "bias": 2}],
         "recipe_version": "test-v1"}]})
    response = client.post("/api/v1/qualifications", json={
        "product_id": product_id, "aoi_task_id": aoi["task_id"], "stage2_task_id": stage2["task_id"],
        "optical_features": {"qualified": True, "power_dbm": 2.1},
        "electrical_features": {"qualified": True, "voltage_v": 3.3},
        "tuning_result": {"status": "complete", "ea": 100, "bias": 80},
        "manual_review": {"decision": "qualified", "reviewer": "test-reviewer",
                          "measured_at": datetime.now(timezone.utc).isoformat(),
                          "notes": "test measured record"}})
    assert response.status_code == 201
    return response.get_json()["data"]["qualification_id"]


def test_manual_qualification_requires_named_review_and_time(api):
    client, _, app = api
    store = app.extensions["inference_store"]
    aoi = store.create("stage1", "P")
    store.update(aoi["task_id"], status="COMPLETED", result={"qualified": True})
    stage2 = store.create("stage2", "P")
    store.update(stage2["task_id"], status="COMPLETED", result={"files": [
        {"source": {"row_count": 1}, "recipe_version": "test-v1"}]})
    body = {"product_id": "P", "aoi_task_id": aoi["task_id"], "stage2_task_id": stage2["task_id"],
            "optical_features": {"qualified": True, "power_dbm": 2.1},
            "electrical_features": {"qualified": True, "voltage_v": 3.3},
            "tuning_result": {"status": "complete"}}
    response = client.post("/api/v1/qualifications", json=body)
    assert response.status_code == 422
    body["manual_review"] = {"decision": "qualified", "reviewer": "EMP-001",
                             "measured_at": "2020-01-01T00:00:00+00:00", "notes": "工单 123"}
    assert client.post("/api/v1/qualifications", json=body).status_code == 422
    body["manual_review"]["measured_at"] = datetime.now(timezone.utc).isoformat()
    store.update(aoi["task_id"], result={"qualified": True, "recipe_version": "other-recipe"})
    mismatch = client.post("/api/v1/qualifications", json=body)
    assert mismatch.status_code == 422 and "配方版本" in mismatch.json["message"]


def test_health_create_query_and_missing(api):
    client, _, _ = api
    health = client.get("/api/v1/health").get_json()["data"]
    assert health["web"] == "ok" and health["stage2_channels"] == [0, 1]
    assert health["stage1"] == "not_configured"
    task = client.post("/api/v1/tasks", json={"stage": "stage2", "product_id": "P-1"})
    assert task.status_code == 201
    task_id = task.get_json()["data"]["task_id"]
    assert client.get(f"/api/v1/tasks/{task_id}").get_json()["data"]["product_id"] == "P-1"
    assert client.get(f"/api/v1/tasks/{task_id}/result").status_code == 202
    assert client.get(f"/api/v1/tasks/{task_id}/logs").get_json()["data"]["total"] == 1
    assert client.get("/api/v1/tasks/no-such-task").status_code == 404
    assert client.post("/api/v1/tasks", json={"stage": "bad", "product_id": "P"}).status_code == 400


def test_stage2_multifile_multichannel_real_events_and_legacy_regression(api):
    client, root, app = api
    first, second = root / "first.csv", root / "second.csv"
    pd.DataFrame([[1, 2, 3, 4], [2, 3, 4, 5]], columns=list("abcd")).to_csv(first, index=False)
    pd.DataFrame([[3, 4, 5, 6]], columns=list("abcd")).to_csv(second, index=False)
    legacy = client.post("/predict-file", json={"channel": 0, "file_path": str(first)}).get_json()
    response = client.post("/api/v1/inference/ea-bias", json={"product_id": "P-2", "files": [
        {"file_path": str(first), "channels": [0, 1]},
        {"file_path": str(second), "channels": [1, 0]},
    ]})
    assert response.status_code == 202
    task_id = response.get_json()["data"]["task_id"]
    task = wait_task(client, task_id)
    assert task["status"] == "COMPLETED" and task["progress"] == 100
    assert (task["current_file"], task["file_index"], task["file_total"]) == ("second.csv", 2, 2)
    assert (task["current_channel"], task["channel_index"], task["channel_total"]) == ("CH0", 2, 2)
    result = client.get(f"/api/v1/tasks/{task_id}/result").get_json()["data"]["result"]
    assert result["files"][0]["predictions"] == legacy["predictions"]
    page = client.get(f"/api/v1/tasks/{task_id}/predictions?file_index=1&channel=0&limit=1")
    assert page.status_code == 200
    assert page.json["data"]["total"] == 2
    assert page.json["data"]["items"] == legacy["predictions"][:1]
    assert [(v["source"]["file_name"], v["channel"]) for v in result["files"]] == [
        ("first.csv", 0), ("first.csv", 1), ("second.csv", 1), ("second.csv", 0)]
    logs = client.get(f"/api/v1/tasks/{task_id}/logs?stage=stage2&limit=200").get_json()["data"]["items"]
    assert [(e["file_name"], e["channel"]) for e in logs if e["operation"] == "save_result"] == [
        ("first.csv", "CH0"), ("first.csv", "CH1"), ("second.csv", "CH1"), ("second.csv", "CH0")]
    assert {"load_file", "validate", "preprocess", "load_model", "inference", "postprocess", "save_result"} <= {
        e["operation"] for e in logs}
    assert (app.extensions["inference_store"].log_dir / f"{task_id}.jsonl").is_file()


def test_stage2_large_result_is_paged_and_task_snapshot_stays_small(api):
    client, root, app = api
    source = root / "many.csv"
    pd.DataFrame([[1, 2, 3, 4]] * 1001, columns=list("abcd")).to_csv(source, index=False)
    response = client.post("/api/v1/inference/ea-bias", json={
        "product_id": "P-LARGE", "files": [{"file_path": str(source), "channels": [0]}]})
    task_id = response.json["data"]["task_id"]
    task = wait_task(client, task_id)
    assert task["status"] == "COMPLETED"
    item = task["result"]["files"][0]
    assert item["prediction_count"] == 1001 and item["predictions_truncated"] is True
    assert len(item["predictions"]) == 10
    assert (app.extensions["inference_store"].output_dir / f"{task_id}.json").stat().st_size < 10000
    page = client.get(f"/api/v1/tasks/{task_id}/predictions?file_index=1&channel=0&limit=3&offset=998")
    assert page.json["data"]["total"] == 1001
    assert [row["row"] for row in page.json["data"]["items"]] == [1000, 1001, 1002]


def test_stage2_full_queue_rejects_before_creating_task(api):
    client, root, app = api
    source = root / "one.csv"
    pd.DataFrame([[1, 2, 3, 4]], columns=list("abcd")).to_csv(source, index=False)
    pool = app.extensions["inference"].executor.stage2
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        assert release.wait(5)

    first = pool.submit(blocked)
    assert entered.wait(5)
    waiting = [pool.submit(lambda: None) for _ in range(pool.waiting_capacity)]
    try:
        task_dir = app.extensions["inference_store"].output_dir
        before = len(list(task_dir.glob("*.json")))
        response = client.post("/api/v1/inference/ea-bias", json={
            "product_id": "P", "files": [{"file_path": str(source), "channels": [0]}]})
        assert response.status_code == 429 and response.headers["Retry-After"] == "2"
        assert len(list(task_dir.glob("*.json"))) == before
    finally:
        release.set()
        first.result(timeout=5)
        for future in waiting:
            future.result(timeout=5)


@pytest.mark.parametrize("kind,expected", [("missing", "FILE_NOT_FOUND"),
                                              ("format", "INVALID_FILE_FORMAT"),
                                              ("model", "MODEL_NOT_CONFIGURED")])
def test_stage2_failures_keep_file_channel_operation(api, kind, expected):
    client, root, _ = api
    path = root / ("bad.txt" if kind == "format" else "input.csv")
    if kind != "missing":
        path.write_text("a,b,c,d\n1,2,3,4\n", encoding="utf-8")
    channel = 2 if kind == "model" else 0
    response = client.post("/api/v1/inference/ea-bias", json={"product_id": "P", "files": [
        {"file_path": str(path), "channels": [channel]}]})
    task_id = response.get_json()["data"]["task_id"]
    task = wait_task(client, task_id)
    assert task["status"] == "FAILED" and task["error_code"] == expected
    assert task["current_file"] == path.name and task["current_channel"] == f"CH{channel}"
    assert task["operation"] in {"load_file", "load_model"}
    failure = client.get(f"/api/v1/tasks/{task_id}/result")
    assert failure.status_code == 409 and failure.get_json()["code"] == expected
    logs = client.get(f"/api/v1/tasks/{task_id}/logs").get_json()["data"]["items"]
    assert logs[-1]["level"] == "ERROR" and logs[-1]["exception"] is None


def test_stage1_no_model_and_bad_image(api):
    client, _, _ = api
    valid = client.post("/api/v1/inference/aoi", data={"product_id": "P", "image":
        (io.BytesIO(b"\x89PNG\r\n\x1a\ncontent"), "a.png")}, content_type="multipart/form-data")
    assert valid.status_code == 503 and valid.get_json()["code"] == "MODEL_NOT_CONFIGURED"
    task_id = valid.get_json()["data"]["task_id"]
    assert client.get(f"/api/v1/tasks/{task_id}").get_json()["data"]["status"] == "FAILED"
    bad = client.post("/api/v1/inference/aoi", data={"product_id": "P", "image":
        (io.BytesIO(b"not an image"), "bad.png")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and bad.get_json()["code"] == "INVALID_FILE_FORMAT"


def test_stage3_gate_review_and_model_missing(api):
    client, _, app = api
    base = {"product_id": "P", "aoi_result": {"qualified": True},
            "optical_features": {"qualified": True}, "electrical_features": {"qualified": True},
            "tuning_result": {"status": "complete"}}
    rejected = client.post("/api/v1/inference/grading", json={**base, "aoi_result": {"qualified": False}})
    assert rejected.status_code == 422 and rejected.get_json()["code"] == "QUALIFICATION_REQUIRED"
    review = client.post("/api/v1/inference/grading", json={**base, "optical_features": {}})
    assert review.status_code == 422
    qualification_id = qualified_evidence(client, app)
    missing_model = client.post("/api/v1/inference/grading", json={"product_id": "P", "qualification_id": qualification_id})
    assert missing_model.status_code == 503 and missing_model.get_json()["code"] == "MODEL_NOT_CONFIGURED"


def test_aoi_stream_bounded_queue_retries_same_sequence_and_drains(api):
    client, root, app = api
    entered, release = Event(), Event()
    model_file = root / "aoi.bin"
    model_file.write_bytes(b"model")
    adapter = app.extensions["inference"].stage1
    adapter.model_path, adapter.adapter_ref = str(model_file), "test:Adapter"

    class SlowAdapter:
        def predict(self, payload):
            if payload["frame_sequence"] == 1:
                entered.set()
                assert release.wait(5)
            return {"qualified": True, "defects": [], "frame_sequence": payload["frame_sequence"],
                    "product_id": payload["product_id"]}

    adapter._adapter = SlowAdapter()
    app.extensions["aoi_streams"].queue_capacity = 1
    image_stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_stream, format="PNG")
    image = image_stream.getvalue()
    opened = client.post("/api/v1/aoi/streams", json={"product_id": "P-STREAM", "source_id": "CAM-1"})
    assert opened.status_code == 201
    task_id = opened.get_json()["data"]["task_id"]

    def push(sequence, product_id=None):
        form = {"sequence": str(sequence)}
        if product_id:
            form["product_id"] = product_id
        return client.post(f"/api/v1/aoi/streams/{task_id}/frames",
                           data={**form, "image": (io.BytesIO(image), f"{sequence}.png")},
                           content_type="multipart/form-data")

    assert push(1).status_code == 202
    assert entered.wait(5)
    assert push(2, "P-NEXT").status_code == 202
    congestion = push(3)
    assert congestion.status_code == 429 and congestion.get_json()["code"] == "STREAM_BACKPRESSURE"
    assert congestion.headers["Retry-After"] == "1"
    stats = client.get(f"/api/v1/aoi/streams/{task_id}").get_json()["data"]["stream"]
    assert (stats["accepted"], stats["rejected"], stats["queue_depth"]) == (2, 1, 1)
    assert client.get(f"/api/v1/aoi/streams/{task_id}/latest-frame").data == image
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stats = client.get(f"/api/v1/aoi/streams/{task_id}").get_json()["data"]["stream"]
        if stats["processed"] == 2:
            break
        time.sleep(0.02)
    assert stats["processed"] == 2
    assert push(3).status_code == 202
    assert push(3).status_code == 202  # Lost ACK can safely be retried.
    assert client.post(f"/api/v1/aoi/streams/{task_id}/close").status_code == 202
    task = wait_task(client, task_id)
    assert task["status"] == "COMPLETED"
    assert (task["result"]["accepted"], task["result"]["processed"], task["result"]["rejected"]) == (3, 3, 1)
    assert [item["sequence"] for item in task["result"]["recent_frames"]] == [1, 2, 3]
    assert [item["product_id"] for item in task["result"]["recent_frames"]] == [
        "P-STREAM", "P-NEXT", "P-STREAM"]
    assert client.post(f"/api/v1/aoi/streams/{task_id}/frames", data={}).status_code == 409


def test_aoi_stream_reports_missing_model_per_frame(api):
    client, _, app = api
    app.extensions["aoi_streams"].max_streams = 1
    opened = client.post("/api/v1/aoi/streams", json={"product_id": "P", "source_id": "CAM-2"})
    task_id = opened.get_json()["data"]["task_id"]
    assert client.post("/api/v1/aoi/streams", json={"product_id": "P", "source_id": "CAM-3"}).status_code == 429
    image_stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_stream, format="JPEG")
    pushed = client.post(f"/api/v1/aoi/streams/{task_id}/frames",
                         data={"sequence": "1", "image": (io.BytesIO(image_stream.getvalue()), "one.jpg")},
                         content_type="multipart/form-data")
    assert pushed.status_code == 202
    client.post(f"/api/v1/aoi/streams/{task_id}/close")
    task = wait_task(client, task_id)
    assert task["status"] == "FAILED" and task["result"]["failed"] == 1
    assert task["result"]["recent_frames"][0]["error_code"] == "MODEL_NOT_CONFIGURED"
    assert "output" not in task["result"]["recent_frames"][0]


def test_aoi_stream_idle_timeout_releases_capacity(api):
    client, _, app = api
    streams = app.extensions["aoi_streams"]
    streams.max_streams = 1
    streams.idle_timeout_seconds = 1
    opened = client.post("/api/v1/aoi/streams", json={"product_id": "P", "source_id": "IDLE-CAM"})
    task = wait_task(client, opened.get_json()["data"]["task_id"])
    assert task["status"] == "FAILED" and task["error_code"] == "STREAM_IDLE_TIMEOUT"
    assert task["result"]["timed_out"] is True
    assert client.post("/api/v1/aoi/streams", json={"product_id": "P", "source_id": "NEXT-CAM"}).status_code == 201
    next_id = client.get("/api/v1/health").get_json()["data"]["aoi_streams"]["active_streams"]
    assert next_id == 1
    # The new stream is intentionally closed to leave no test worker running.
    active_id = next(iter(streams._streams))
    client.post(f"/api/v1/aoi/streams/{active_id}/close")
    wait_task(client, active_id)


def test_stage3_model_can_only_return_qualified_product_grade(api):
    client, root, app = api
    model_file = root / "grading.bin"
    model_file.write_bytes(b"model")
    service = app.extensions["inference"].stage3
    service.model_path, service.adapter_ref = str(model_file), "test:Adapter"

    class GradeAdapter:
        def __init__(self, grade, confidence=0.95):
            self.grade = grade
            self.confidence = confidence

        def predict(self, payload):
            return {"grade": self.grade, "score": 0.9, "confidence": self.confidence,
                    "reason": "合格品内部质量差异", "rule_version": "test-v1"}

    base = {"product_id": "P", "qualification_id": qualified_evidence(client, app)}
    service._adapter = GradeAdapter("B")
    response = client.post("/api/v1/inference/grading", json=base)
    task = wait_task(client, response.get_json()["data"]["task_id"])
    assert task["status"] == "COMPLETED" and task["result"]["grade"] == "B"
    assert task["result"]["eligible_for_grading"] is True
    service._adapter = GradeAdapter("A", 0.2)
    uncertain = client.post("/api/v1/inference/grading", json=base)
    review = wait_task(client, uncertain.get_json()["data"]["task_id"])
    assert review["status"] == "REVIEW_REQUIRED"
    assert review["result"]["grade"] is None and review["result"]["suggested_grade"] == "A"
    service._adapter = GradeAdapter("REJECT")
    invalid = client.post("/api/v1/inference/grading", json=base)
    failed = wait_task(client, invalid.get_json()["data"]["task_id"])
    assert failed["status"] == "FAILED" and failed["error_code"] == "INVALID_MODEL_OUTPUT"


def test_stage3_watchdog_finishes_task_when_model_stalls(api, monkeypatch):
    client, root, app = api
    import services.inference_service as inference_module
    monkeypatch.setattr(inference_module, "INFERENCE_JOB_TIMEOUT_SECONDS", 0.1)
    model_file = root / "slow-grading.bin"
    model_file.write_bytes(b"model")
    service = app.extensions["inference"].stage3
    service.model_path, service.adapter_ref = str(model_file), "test:Adapter"
    entered, release = Event(), Event()

    class SlowAdapter:
        def health_check(self):
            return "ok"

        def get_model_info(self):
            return {"model_version": "test-v1"}

        def predict(self, payload):
            entered.set()
            release.wait(5)
            return {"grade": "A", "score": 0.9, "confidence": 0.95,
                    "reason": "合格品", "rule_version": "test-v1"}

    service._adapter = SlowAdapter()
    try:
        evidence = qualified_evidence(client, app)
        response = client.post("/api/v1/inference/grading", json={
            "product_id": "P", "qualification_id": evidence})
        task_id = response.json["data"]["task_id"]
        assert entered.wait(5)
        timed_out = wait_task(client, task_id)
        assert timed_out["status"] == "FAILED" and timed_out["error_code"] == "TASK_TIMEOUT"
    finally:
        release.set()
    app.extensions["inference"].executor.stage3.shutdown(wait=True)
    assert client.get(f"/api/v1/tasks/{task_id}").json["data"]["error_code"] == "TASK_TIMEOUT"


def test_bad_parameters_and_task_restore(api):
    client, _, app = api
    assert client.post("/api/v1/inference/ea-bias", json={"product_id": "P", "files": []}).status_code == 400
    assert client.get("/api/v1/tasks/x/logs?limit=0").status_code == 400
    task_id = client.post("/api/v1/tasks", json={"stage": "stage1", "product_id": "P"}).get_json()["data"]["task_id"]
    from tasks.inference_store import InferenceTaskStore
    restored = InferenceTaskStore(app.extensions["inference_store"].output_dir.parent,
                                  app.extensions["inference_store"].log_dir.parent)
    assert restored.get(task_id)["status"] == "FAILED"
    assert restored.get(task_id)["error_code"] == "TASK_FAILED"


def test_model_load_error_and_inference_exception(api):
    client, root, app = api
    model_file = root / "aoi.bin"
    model_file.write_bytes(b"model")
    service = app.extensions["inference"].stage1
    service.model_path = str(model_file)
    service.adapter_ref = "no_such_module:Adapter"
    load_error = client.post("/api/v1/inference/aoi", data={"product_id": "P", "image":
        (io.BytesIO(b"\x89PNG\r\n\x1a\ncontent"), "a.png")}, content_type="multipart/form-data")
    assert load_error.status_code == 503 and load_error.get_json()["code"] == "MODEL_LOAD_FAILED"

    class BrokenAdapter:
        def predict(self, payload):
            raise RuntimeError("sensitive internal failure")

        def health_check(self):
            return "ok"

        def get_model_info(self):
            return {}

    service._adapter = BrokenAdapter()
    service.adapter_ref = "configured:Adapter"
    inference = client.post("/api/v1/inference/aoi", data={"product_id": "P", "image":
        (io.BytesIO(b"\x89PNG\r\n\x1a\ncontent"), "a.png")}, content_type="multipart/form-data")
    task_id = inference.get_json()["data"]["task_id"]
    task = wait_task(client, task_id)
    assert task["error_code"] == "INFERENCE_FAILED"
    assert "sensitive" not in client.get(f"/api/v1/tasks/{task_id}/result").get_json()["message"]
