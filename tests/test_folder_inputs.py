from pathlib import Path
from threading import Event

import pytest

import backend_server
from services.folder_input_service import FolderInputService, FolderInputError
from services.prediction_service import PredictionService, InputFileChangedError
from test_flask_api import write_zero_artifact
from test_inference_api import wait_task


@pytest.fixture
def folder_api(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    monkeypatch.setattr(backend_server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(backend_server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(backend_server, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend_server, "STAGE2_INPUT_DIRS", str(inputs))
    monkeypatch.setattr(backend_server, "STAGE2_FILE_STABLE_SECONDS", 0)
    models = tmp_path / "models"
    write_zero_artifact(models, 0)
    write_zero_artifact(models, 1)
    app = backend_server.create_app(model_dir=models, data_dir=tmp_path / "training")
    yield app.test_client(), inputs, app
    app.extensions["inference"].executor.shutdown(wait=True)
    app.extensions["task_manager"].shutdown(wait=True)


def write_input(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a,b,c,d\n1,2,3,4\n2,3,4,5\n", encoding="utf-8")


def test_folder_discovery_and_partial_results_process_every_file(folder_api):
    client, root, app = folder_api
    for channel in (0, 1, 2):
        write_input(root / f"channel_{channel}_ea.csv")
    (root / "notes.txt").write_text("ignored")
    sources = client.get("/api/v1/inference/ea-bias/sources").json["data"]
    assert sources["sources"][0]["directory"] == str(root)
    scan = client.post("/api/v1/inference/ea-bias/scan", json={"source_id": "input-1"}).json["data"]
    assert (scan["total_files"], scan["ready_files"], scan["missing_model_files"]) == (3, 2, 1)
    response = client.post("/api/v1/inference/ea-bias/folder", json={"source_id": "input-1"})
    assert response.status_code == 202
    task_id = response.json["data"]["task_id"]
    task = wait_task(client, task_id)
    assert task["status"] == "FAILED" and task["error_code"] == "BATCH_PARTIAL_FAILED"
    results = task["result"]["files"]
    assert [item["channel"] for item in results] == [0, 1, 2]
    assert [item["status"] for item in results] == ["COMPLETED", "COMPLETED", "FAILED"]
    assert results[2]["error_code"] == "MODEL_NOT_CONFIGURED"
    assert task["result"]["completed_channels"] == 2 and task["result"]["failed_channels"] == 1
    result = client.get(f"/api/v1/tasks/{task_id}/result")
    assert result.status_code == 409 and len(result.json["data"]["result"]["files"]) == 3
    page = client.get(results[0]["predictions_endpoint"] + "&limit=1&offset=1").json["data"]
    assert page["total"] == 2 and page["items"][0]["row"] == 3
    assert app.extensions["inference"].executor.stage2.stats()["outstanding"] == 0


def test_folder_with_all_models_completes_and_parent_channel_is_recognized(folder_api):
    client, root, _ = folder_api
    write_input(root / "CH0" / "measurement.csv")
    response = client.post("/api/v1/inference/ea-bias/folder", json={"source_id": "input-1"})
    task = wait_task(client, response.json["data"]["task_id"])
    assert task["status"] == "COMPLETED"
    assert task["result"]["files"][0]["channel"] == 0


def test_unknown_conflicting_channels_and_untrusted_sources_are_rejected(folder_api):
    client, root, app = folder_api
    write_input(root / "measurement.csv")
    write_input(root / "CH1" / "channel_0.csv")
    scan = client.post("/api/v1/inference/ea-bias/scan", json={"source_id": "input-1"}).json["data"]
    assert scan["invalid_files"] == 2
    before = list(app.extensions["inference_store"].output_dir.glob("*.json"))
    assert client.post("/api/v1/inference/ea-bias/folder", json={"source_id": "input-1"}).status_code == 422
    assert client.post("/api/v1/inference/ea-bias/scan", json={"source_id": "../../.secrets"}).status_code == 404
    assert client.post("/api/v1/inference/ea-bias/scan", json={"source_id": "input-1", "directory": str(root.parent)}).status_code == 400
    assert list(app.extensions["inference_store"].output_dir.glob("*.json")) == before


def test_full_queue_rejects_before_folder_scan(folder_api, monkeypatch):
    client, _, app = folder_api
    pool = app.extensions["inference"].executor.stage2
    entered, release = Event(), Event()
    def blocked():
        entered.set()
        assert release.wait(5)
    first = pool.submit(blocked)
    assert entered.wait(5)
    queued = [pool.submit(lambda: None) for _ in range(pool.waiting_capacity)]
    monkeypatch.setattr(app.extensions["stage2_inputs"], "scan", lambda *args: pytest.fail("Queue must reject before scanning"))
    try:
        response = client.post("/api/v1/inference/ea-bias/folder", json={"source_id": "input-1"})
        assert response.status_code == 429 and response.headers["Retry-After"] == "2"
    finally:
        release.set()
        first.result(timeout=5)
        for future in queued:
            future.result(timeout=5)


def test_scan_is_bounded_and_detects_unstable_files(tmp_path):
    for i in range(3):
        write_input(tmp_path / f"channel_0_{i}.csv")
    service = FolderInputService(PredictionService(tmp_path / "models"), str(tmp_path), max_entries=2)
    with pytest.raises(FolderInputError, match="扫描上限"):
        service.scan("input-1")
    service.max_entries = 100
    service.stable_seconds = 30
    assert service.scan("input-1")["invalid_files"] == 3


def test_changed_input_is_rejected_before_model_inference(tmp_path):
    source = tmp_path / "channel_0.csv"
    write_input(source)
    stat = source.stat()
    source.write_text("a,b,c,d\n9,8,7,6\n", encoding="utf-8")
    predictor = PredictionService(tmp_path / "models")
    with pytest.raises(InputFileChangedError):
        predictor.predict_file(0, str(source), input_size_bytes=stat.st_size, input_mtime_ns=stat.st_mtime_ns)
