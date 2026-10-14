import pytest

import api_v1
import backend_server
from services.folder_input_service import FolderInputService
from services.stage2_source_store import Stage2SourceStore
from test_folder_inputs import folder_api, write_input


BASE = "/api/v1/inference/ea-bias"


@pytest.fixture
def mes_api(folder_api, monkeypatch):
    client, root, app = folder_api
    tokens = {"MES-A": "test-a", "MES-B": "test-b"}
    monkeypatch.setattr(backend_server, "ADMIN_TOKEN", "test-admin")
    monkeypatch.setattr(backend_server, "OPERATOR_TOKEN", "test-operator")
    monkeypatch.setattr(backend_server, "DEVICE_TOKEN", "test-shared")
    monkeypatch.setattr(backend_server, "DEVICE_TOKENS", tokens)
    monkeypatch.setattr(api_v1, "DEVICE_TOKENS", tokens)
    return client, root, app


def headers(device="MES-A"):
    return {"Authorization": f"Bearer test-{'a' if device == 'MES-A' else 'b'}",
            "X-Liante-Device-ID": device}


def register(client, directory, source="line-a", **values):
    return client.post(BASE + "/sources", headers=headers(),
                       json={"source_id": source, "directory": str(directory), **values})


def test_mes_directory_is_persistent_versioned_and_confined(mes_api):
    client, root, app = mes_api
    first, second = root / "batch-a", root / "batch-b"
    first.mkdir(); second.mkdir()
    response = register(client, first)
    assert response.status_code == 201 and response.json["data"]["revision"] == 1
    assert register(client, first).json["data"]["revision"] == 1
    assert register(client, second, expected_revision=1).json["data"]["revision"] == 2
    assert register(client, first, expected_revision=1).status_code == 409
    assert register(client, root.parent / "outside", source="outside").status_code == 403
    assert register(client, "relative/path", source="relative").status_code == 400
    original = app.extensions["stage2_inputs"]
    restored = FolderInputService(original.prediction, str(root), stable_seconds=0,
                                  db_path=original.registry.path)
    assert restored.source("line-a", "MES-A")["directory"] == str(second)
    assert restored.source("line-a")["revision"] == 2
    events = client.get(BASE + "/sources/line-a/logs", headers=headers()).json["data"]["items"]
    assert events[0]["code"] == "SOURCE_VERSION_CONFLICT"
    change = next(event for event in events if event.get("revision") == 2)
    assert change["previous_directory"] == str(first) and change["directory"] == str(second)
    assert "test-a" not in str(events)


def test_mes_can_predict_poll_read_rows_and_logs_with_retry_deduplication(mes_api):
    client, root, app = mes_api
    write_input(root / "channel_0.csv")
    assert register(client, root).status_code == 201
    payload = {"source_id": "line-a", "product_id": "P-001", "request_id": "mes-001"}
    response = client.post(BASE + "/folder", headers=headers(), json=payload)
    assert response.status_code == 202
    task_id = response.json["data"]["task_id"]
    # Poll through the same identity; anonymous admin helpers are intentionally unavailable.
    import time
    for _ in range(100):
        task = client.get(f"/api/v1/tasks/{task_id}", headers=headers()).json["data"]
        if task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(.02)
    assert task["status"] == "COMPLETED" and task["source_revision"] == 1
    replay = client.post(BASE + "/folder", headers=headers(), json=payload)
    assert replay.status_code == 200 and replay.json["data"]["replayed"]
    assert replay.json["data"]["task_id"] == task_id
    # The receipt survives store recreation, rather than relying on an in-memory cache.
    source_store = app.extensions["stage2_inputs"].registry
    assert Stage2SourceStore(source_store.path, {}).receipt("device:MES-A", "mes-001")["task_id"] == task_id
    conflict = client.post(BASE + "/folder", headers=headers(), json={**payload, "product_id": "P-002"})
    assert conflict.status_code == 409 and conflict.json["code"] == "IDEMPOTENCY_CONFLICT"
    result = client.get(f"/api/v1/tasks/{task_id}/result", headers=headers())
    assert result.status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/predictions?limit=1", headers=headers()).json["data"]["total"] == 2
    logs = client.get(f"/api/v1/tasks/{task_id}/logs", headers=headers()).json["data"]["items"]
    assert any(event["operation"] == "mes_receive" and event["source_id"] == "line-a" for event in logs)
    assert len(list(app.extensions["inference_store"].output_dir.glob("*.json"))) == 1


def test_mes_ownership_and_roles_cover_sources_tasks_and_results(mes_api):
    client, root, app = mes_api
    write_input(root / "channel_0.csv")
    register(client, root)
    assert [s["source_id"] for s in client.get(BASE + "/sources", headers=headers()).json["data"]["sources"]] == ["line-a"]
    assert client.post(BASE + "/scan", headers=headers("MES-B"), json={"source_id": "line-a"}).status_code == 403
    assert client.post(BASE + "/sources", headers=headers("MES-B"), json={"source_id": "line-a", "directory": str(root)}).status_code == 403
    assert register(client, root, owner_device_id="MES-B").status_code == 403
    response = client.post(BASE + "/folder", headers=headers(), json={"source_id": "line-a", "product_id": "P1", "request_id": "r1"})
    task_id = response.json["data"]["task_id"]
    for suffix in ("", "/result", "/logs", "/predictions"):
        assert client.get(f"/api/v1/tasks/{task_id}{suffix}", headers=headers("MES-B")).status_code == 403
    assert client.get(BASE + "/sources/line-a/logs", headers=headers("MES-B")).status_code == 403
    assert client.get(BASE + "/sources", headers={"Authorization": "Bearer test-shared"}).json["code"] == "DEVICE_ID_REQUIRED"
    assert client.post(BASE + "/sources", headers={"Authorization": "Bearer test-operator", "X-Liante-Role": "operator"},
                       json={"source_id": "operator", "directory": str(root)}).status_code == 403
    assert client.post(BASE + "/folder", headers=headers(), json={"source_id": "line-a"}).status_code == 400
    assert client.post("/api/v1/inference/ea-bias", headers=headers(), json={}).status_code == 403


def test_source_logs_include_rejections_and_are_bounded(folder_api):
    client, root, app = folder_api
    service = app.extensions["stage2_inputs"]
    service.registry.max_events = 3
    for _ in range(5):
        assert client.post(BASE + "/folder", json={"source_id": "input-1"}).status_code == 422
    response = client.get(BASE + "/sources/input-1/logs?limit=2")
    assert response.status_code == 200
    assert response.json["data"]["total"] == 3 and len(response.json["data"]["items"]) == 2
    assert all(item["code"] == "INPUT_FOLDER_EMPTY" for item in response.json["data"]["items"])
    assert client.get(BASE + "/sources/input-1/logs?limit=1001").status_code == 400


def test_busy_submission_is_rejected_and_logged_before_scanning(folder_api):
    client, _, app = folder_api
    service = app.extensions["stage2_inputs"]
    assert service.submission_slot.acquire(blocking=False)
    try:
        response = client.post(BASE + "/folder", json={"source_id": "input-1"})
        assert response.status_code == 429 and response.headers["Retry-After"] == "2"
        events = client.get(BASE + "/sources/input-1/logs").json["data"]["items"]
        assert events[0]["code"] == "SUBMISSION_BUSY"
    finally:
        service.submission_slot.release()
