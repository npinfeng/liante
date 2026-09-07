from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np
import torch
from sklearn.preprocessing import StandardScaler

from backend_server import create_app
from models.mlp import DynamicMLP, MLPConfig
from conftest import write_channel_csv


def write_zero_artifact(model_dir: Path, channel: int = 0) -> None:
    directory = model_dir / f"channel_{channel}"
    directory.mkdir(parents=True)
    config = MLPConfig(hidden_dims=(8,), activation="relu")
    model = DynamicMLP(config)
    for parameter in model.parameters():
        parameter.data.zero_()
    x_scaler = StandardScaler().fit(np.array([[0, 0, 0, 0], [2, 4, 6, 8]], dtype=np.float32))
    y_scaler = StandardScaler().fit(np.array([[8, -5], [12, 1]], dtype=np.float32))
    torch.save(model.state_dict(), directory / "model.pth")
    (directory / "config.json").write_text(
        json.dumps(config.to_dict(include_model_type=True)), encoding="utf-8"
    )
    joblib.dump(x_scaler, directory / "scaler_x.joblib")
    joblib.dump(y_scaler, directory / "scaler_y.joblib")
    (directory / "metrics.json").write_text("{}", encoding="utf-8")


def wait_for_terminal_status(client, task_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/automl/tasks/{task_id}").get_json()
        if task["status"] in {"completed", "failed"}:
            return task
        time.sleep(0.03)
    raise AssertionError("task did not finish")


def test_predict_uses_channel_artifact(tmp_path: Path):
    model_dir = tmp_path / "models"
    write_zero_artifact(model_dir)
    app = create_app(model_dir=model_dir, data_dir=tmp_path / "data")
    client = app.test_client()
    response = client.post("/predict", json={"channel": 0, "data": [[1, 2, 3, 4]]})
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "channel": 0,
        "predictions": [{"ea": 10.0, "bias": -2.0}],
    }
    app.extensions["task_manager"].shutdown()


def test_automl_train_is_async_and_exposes_status(channel_data_dir: Path, tmp_path: Path):
    app = create_app(model_dir=tmp_path / "models", data_dir=channel_data_dir)
    client = app.test_client()
    response = client.post(
        "/automl/train",
        json={
            "channel": 0,
            "n_trials": 1,
            "max_epochs": 2,
            "patience": 2,
            "search_space": {
                "n_layers": [1, 1], "hidden_sizes": [32], "activations": ["relu"],
                "dropout": [0, 0], "batch_norm": [False], "optimizers": ["Adam"],
                "learning_rate": [0.001, 0.001], "weight_decay": [0.000001, 0.000001],
                "batch_sizes": [16], "schedulers": ["none"]
            },
        },
    )
    assert response.status_code == 202
    task_id = response.get_json()["task_id"]
    task = wait_for_terminal_status(client, task_id)
    assert task["status"] == "completed"
    result = client.get(f"/automl/tasks/{task_id}/result")
    assert result.status_code == 200
    assert result.get_json()["channel"] == 0
    app.extensions["task_manager"].shutdown()


def test_invalid_request_and_exception_task_do_not_crash_server(tmp_path: Path):
    app = create_app(model_dir=tmp_path / "models", data_dir=tmp_path / "missing")
    client = app.test_client()
    assert client.post("/automl/train", json={"channel": 9}).status_code == 400
    queued = client.post(
        "/automl/train", json={"channel": 0, "n_trials": 1, "max_epochs": 1}
    )
    task = wait_for_terminal_status(client, queued.get_json()["task_id"])
    assert task["status"] == "failed"
    assert client.get("/health").status_code == 200
    app.extensions["task_manager"].shutdown()


def test_train_all_runs_channels_serially(channel_data_dir: Path, tmp_path: Path):
    write_channel_csv(channel_data_dir, 1)
    app = create_app(model_dir=tmp_path / "models", data_dir=channel_data_dir)
    client = app.test_client()
    response = client.post(
        "/automl/train-all",
        json={"channels": [0, 1], "n_trials": 1, "max_epochs": 1, "patience": 1},
    )
    assert response.status_code == 202
    task_id = response.get_json()["task_id"]
    task = wait_for_terminal_status(client, task_id, timeout=30)
    assert task["status"] == "completed"
    result = client.get(f"/automl/tasks/{task_id}/result").get_json()
    assert result["trained_channels"] == [0, 1]
    assert [item["channel"] for item in result["channels"]] == [0, 1]
    app.extensions["task_manager"].shutdown()
