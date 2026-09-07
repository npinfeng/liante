from __future__ import annotations

import json
from pathlib import Path

import joblib

from automl.mlp_trainer import MLPAutoMLTrainer
from schemas.training import SearchSpace


FAST_SPACE = SearchSpace(
    n_layers=(1, 2),
    hidden_sizes=[32, 64],
    activations=["relu", "tanh"],
    dropout=(0.0, 0.1),
    batch_norm=[False],
    optimizers=["Adam"],
    learning_rate=(1e-3, 3e-3),
    weight_decay=(1e-6, 1e-4),
    batch_sizes=[16, 32],
    schedulers=["none"],
)


def test_single_optuna_trial(channel_data_dir: Path, tmp_path: Path):
    result = MLPAutoMLTrainer(channel_data_dir, tmp_path / "models", device="cpu").train_channel(
        0,
        n_trials=1,
        max_epochs=2,
        patience=2,
        random_seed=7,
        search_space=FAST_SPACE,
    )
    assert result["channel"] == 0
    assert result["model_config"]["model_type"] == "MLP"
    assert result["validation_mse"] >= 0


def test_small_automl_smoke_creates_complete_artifact(channel_data_dir: Path, tmp_path: Path):
    model_dir = tmp_path / "models"
    result = MLPAutoMLTrainer(channel_data_dir, model_dir, device="cpu").train_channel(
        0,
        n_trials=2,
        max_epochs=5,
        patience=3,
        random_seed=11,
        search_space=FAST_SPACE,
    )
    directory = model_dir / "channel_0"
    assert {path.name for path in directory.iterdir()} == {
        "model.pth",
        "config.json",
        "scaler_x.joblib",
        "scaler_y.joblib",
        "metrics.json",
        "training_metadata.json",
    }
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics) == {"ea", "bias", "overall"}
    assert set(metrics["ea"]) == {"mse", "rmse", "mae", "r2"}
    metadata = json.loads((directory / "training_metadata.json").read_text(encoding="utf-8"))
    assert metadata["scaler_fit"] == "train_only"
    assert metadata["sample_counts"] == {"train": 56, "validation": 12, "test": 12}
    assert joblib.load(directory / "scaler_x.joblib").n_features_in_ == 4
    assert result["model_path"] == str(directory / "model.pth")

