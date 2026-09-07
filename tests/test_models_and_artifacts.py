from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pytest
import torch
from sklearn.preprocessing import StandardScaler

from models.mlp import DynamicMLP, MLPConfig
from services.prediction_service import PredictionService


@pytest.mark.parametrize("hidden_dims", [[64], [128, 64], [256, 128, 64, 32]])
def test_dynamic_mlp_forward_shapes(hidden_dims):
    model = DynamicMLP(
        input_dim=4,
        output_dim=2,
        hidden_dims=hidden_dims,
        activation="silu",
        dropout=0.2,
        batch_norm=True,
    )
    model.eval()
    assert model(torch.randn(7, 4)).shape == (7, 2)


def test_config_round_trip_rebuilds_same_topology():
    config = MLPConfig(
        hidden_dims=(256, 64, 128), activation="gelu", dropout=0.15, batch_norm=True
    )
    rebuilt = MLPConfig.from_dict(config.to_dict(include_model_type=True))
    assert rebuilt == config
    assert list(DynamicMLP(rebuilt).state_dict()) == list(DynamicMLP(config).state_dict())


def test_state_dict_and_scalers_save_and_load(tmp_path: Path):
    directory = tmp_path / "channel_0"
    directory.mkdir()
    config = MLPConfig(hidden_dims=(8,), activation="tanh")
    model = DynamicMLP(config)
    x_scaler = StandardScaler().fit(np.arange(40, dtype=np.float32).reshape(10, 4))
    y_scaler = StandardScaler().fit(np.arange(20, dtype=np.float32).reshape(10, 2))
    torch.save(model.state_dict(), directory / "model.pth")
    (directory / "config.json").write_text(
        json.dumps(config.to_dict(include_model_type=True)), encoding="utf-8"
    )
    joblib.dump(x_scaler, directory / "scaler_x.joblib")
    joblib.dump(y_scaler, directory / "scaler_y.joblib")
    (directory / "metrics.json").write_text("{}", encoding="utf-8")

    loaded = PredictionService(tmp_path, device="cpu").load(0)
    assert loaded.config == config
    assert np.allclose(loaded.scaler_x.mean_, x_scaler.mean_)
    for expected, actual in zip(model.state_dict().values(), loaded.model.state_dict().values()):
        assert torch.equal(expected, actual)

