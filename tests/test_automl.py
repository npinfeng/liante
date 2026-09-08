from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pytest
import torch

from automl.mlp_trainer import MLPAutoMLTrainer, regression_metrics
from schemas.training import SearchSpace
from services.prediction_service import PredictionService
from training.data_loader import load_channel_data


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


def test_default_search_space_is_conservative_for_small_channel_data():
    space = SearchSpace()
    assert space.n_layers == (1, 3)
    assert space.hidden_sizes == [32, 64, 128]
    assert space.dropout == (0.05, 0.30)
    assert space.batch_norm == [False]
    assert space.optimizers == ["Adam", "AdamW"]
    assert space.learning_rate == (1e-4, 3e-3)
    assert space.batch_sizes == [16, 32, 64]


def test_overall_r2_averages_outputs_without_flattening_levels():
    actual = np.array([[1000.0, 0.0], [1100.0, 1.0], [1200.0, 2.0]])
    predicted = np.array([[1010.0, 0.5], [1090.0, 0.5], [1190.0, 2.5]])
    metrics = regression_metrics(actual, predicted)
    assert metrics["overall"]["r2"] == pytest.approx(
        (metrics["ea"]["r2"] + metrics["bias"]["r2"]) / 2
    )
    assert metrics["overall"]["r2"] < 0.99


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
    assert metadata["format_version"] == 3
    assert metadata["training_loss"] == "huber_scaled_delta_1"
    assert metadata["final_model_strategy"] == "best_trial_checkpoint"
    assert set(metadata["validation_metrics"]) == {"ea", "bias", "overall"}
    assert metadata["best_trial_seed"] >= 11
    assert metadata["model_parameter_count"] > 0
    assert metadata["scaler_fit"] == "train_only"
    assert metadata["sample_counts"] == {"train": 56, "validation": 12, "test": 12}
    assert joblib.load(directory / "scaler_x.joblib").n_features_in_ == 4
    assert result["model_path"] == str(directory / "model.pth")

    prepared = load_channel_data(0, channel_data_dir)
    artifact = PredictionService(model_dir, device="cpu").load(0)
    with torch.inference_mode():
        validation_predictions = artifact.model(
            torch.as_tensor(prepared.x_val, dtype=torch.float32)
        ).numpy()
    observed_validation_mse = float(
        np.mean(np.square(validation_predictions - prepared.y_val))
    )
    assert observed_validation_mse == pytest.approx(result["validation_mse"], rel=1e-6)
