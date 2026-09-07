"""End-to-end Optuna search, final retraining, evaluation, and persistence."""

from __future__ import annotations

import gc
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import optuna
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from torch import nn

from automl.search_space import (
    model_config_from_params,
    params_from_trial,
    suggest_parameters,
)
from models.mlp import DynamicMLP
from schemas.training import SearchSpace
from training.data_loader import PreparedChannelData, load_channel_data
from training.trainer import fit_fixed_epochs, train_with_early_stopping


LOGGER = logging.getLogger(__name__)
StatusCallback = Callable[..., None]


def set_random_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def build_optimizer(model: nn.Module, params: dict[str, Any]) -> torch.optim.Optimizer:
    common = {
        "lr": float(params["learning_rate"]),
        "weight_decay": float(params["weight_decay"]),
    }
    if params["optimizer"] == "Adam":
        return torch.optim.Adam(model.parameters(), **common)
    if params["optimizer"] == "AdamW":
        return torch.optim.AdamW(model.parameters(), **common)
    if params["optimizer"] == "SGD":
        return torch.optim.SGD(
            model.parameters(), momentum=float(params.get("momentum", 0.0)), **common
        )
    raise ValueError(f"不支持的 optimizer: {params['optimizer']}")


def _metric_block(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    mse = float(mean_squared_error(actual, predicted))
    return {
        "mse": mse,
        "rmse": float(np.sqrt(mse)),
        "mae": float(mean_absolute_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)) if len(actual) >= 2 else 0.0,
    }


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    return {
        "ea": _metric_block(actual[:, 0], predicted[:, 0]),
        "bias": _metric_block(actual[:, 1], predicted[:, 1]),
        "overall": _metric_block(actual.reshape(-1), predicted.reshape(-1)),
    }


def _write_json(path: Path, values: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class MLPAutoMLTrainer:
    """Optimize one independent DynamicMLP per Channel."""

    def __init__(
        self,
        data_dir: str | Path,
        model_dir: str | Path,
        device: str | torch.device | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.model_dir = Path(model_dir)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

    def train_channel(
        self,
        channel: int,
        *,
        n_trials: int = 30,
        max_epochs: int = 500,
        patience: int = 40,
        random_seed: int = 42,
        search_space: SearchSpace | None = None,
        status_callback: StatusCallback | None = None,
    ) -> dict[str, Any]:
        space = search_space or SearchSpace()
        callback = status_callback or (lambda **_: None)
        started = time.perf_counter()
        callback(status="preparing", message=f"正在准备 Channel {channel} 数据")
        data = load_channel_data(channel, self.data_dir)
        set_random_seed(random_seed)

        sampler = optuna.samplers.TPESampler(seed=random_seed)
        pruner = optuna.pruners.MedianPruner(
            n_startup_trials=min(5, n_trials), n_warmup_steps=min(10, max_epochs // 2)
        )
        study = optuna.create_study(direction="minimize", sampler=sampler, pruner=pruner)

        def objective(trial: optuna.Trial) -> float:
            trial_seed = random_seed + trial.number
            set_random_seed(trial_seed)
            params = suggest_parameters(trial, space)
            model = DynamicMLP(model_config_from_params(params)).to(self.device)
            optimizer = build_optimizer(model, params)
            callback(
                status="training",
                current_trial=trial.number + 1,
                total_trials=n_trials,
                progress=int(trial.number * 85 / n_trials),
                message=f"Training trial {trial.number + 1}/{n_trials}",
            )

            def report_epoch(**values: Any) -> None:
                epoch_fraction = values["epoch"] / max_epochs
                callback(
                    status="training",
                    current_trial=trial.number + 1,
                    total_trials=n_trials,
                    progress=min(85, int((trial.number + epoch_fraction) * 85 / n_trials)),
                    best_score=min(
                        float(values["best_loss"]),
                        study.best_value if len(study.trials) and any(
                            item.state == optuna.trial.TrialState.COMPLETE for item in study.trials
                        ) else float(values["best_loss"]),
                    ),
                    message=(
                        f"Training trial {trial.number + 1}/{n_trials}, "
                        f"epoch {values['epoch']}/{max_epochs}"
                    ),
                )

            try:
                outcome = train_with_early_stopping(
                    model,
                    optimizer,
                    str(params["scheduler"]),
                    data.x_train,
                    data.y_train,
                    data.x_val,
                    data.y_val,
                    batch_size=int(params["batch_size"]),
                    max_epochs=max_epochs,
                    patience=min(patience, max_epochs),
                    seed=trial_seed,
                    device=self.device,
                    trial=trial,
                    progress_callback=report_epoch,
                )
                trial.set_user_attr("best_epoch", outcome.best_epoch)
                LOGGER.info(
                    "channel=%s trial=%s validation_loss=%.8f params=%s",
                    channel,
                    trial.number,
                    outcome.best_validation_loss,
                    params,
                )
                return outcome.best_validation_loss
            finally:
                del optimizer
                del model
                gc.collect()
                if self.device.type == "cuda":
                    torch.cuda.empty_cache()

        study.optimize(objective, n_trials=n_trials, gc_after_trial=True, show_progress_bar=False)
        if not study.trials or not any(
            trial.state == optuna.trial.TrialState.COMPLETE for trial in study.trials
        ):
            raise RuntimeError("所有 Optuna trials 均失败或被剪枝")

        best_trial = study.best_trial
        best_params = params_from_trial(best_trial)
        model_config = model_config_from_params(best_params)
        best_epoch = max(1, int(best_trial.user_attrs.get("best_epoch", max_epochs)))
        callback(
            status="retraining",
            current_trial=n_trials,
            total_trials=n_trials,
            progress=90,
            best_score=float(best_trial.value),
            message=f"使用最佳配置重训练 Channel {channel}",
        )

        set_random_seed(random_seed)
        final_model = DynamicMLP(model_config).to(self.device)
        optimizer = build_optimizer(final_model, best_params)
        x_train_val = np.concatenate([data.x_train, data.x_val], axis=0)
        y_train_val = np.concatenate([data.y_train, data.y_val], axis=0)
        fit_fixed_epochs(
            final_model,
            optimizer,
            str(best_params["scheduler"]),
            x_train_val,
            y_train_val,
            batch_size=int(best_params["batch_size"]),
            epochs=best_epoch,
            seed=random_seed,
            device=self.device,
        )

        callback(status="evaluating", progress=95, message=f"评估 Channel {channel} 独立测试集")
        final_model.eval()
        with torch.inference_mode():
            scaled_predictions = final_model(
                torch.as_tensor(data.x_test, dtype=torch.float32, device=self.device)
            ).cpu().numpy()
        predictions = data.scaler_y.inverse_transform(scaled_predictions)
        metrics = regression_metrics(data.y_test_raw, predictions)

        training_time = time.perf_counter() - started
        callback(status="saving", progress=98, message=f"保存 Channel {channel} 模型 artifact")
        artifact_dir = self._save_artifact(
            channel=channel,
            model=final_model,
            data=data,
            model_config=model_config.to_dict(include_model_type=True),
            best_params=best_params,
            metrics=metrics,
            validation_mse=float(best_trial.value),
            best_epoch=best_epoch,
            n_trials=n_trials,
            max_epochs=max_epochs,
            patience=patience,
            random_seed=random_seed,
            training_time=training_time,
            study=study,
        )
        del optimizer
        del final_model
        gc.collect()
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

        result = {
            "channel": channel,
            "best_params": best_params,
            "model_config": model_config.to_dict(include_model_type=True),
            "metrics": metrics,
            "validation_mse": float(best_trial.value),
            "best_epoch": best_epoch,
            "training_time": training_time,
            "model_path": str(artifact_dir / "model.pth"),
            "artifact_dir": str(artifact_dir),
        }
        LOGGER.info(
            "channel=%s training_time=%.3fs model_saved=%s best_params=%s",
            channel,
            training_time,
            artifact_dir,
            best_params,
        )
        return result

    def _save_artifact(
        self,
        *,
        channel: int,
        model: DynamicMLP,
        data: PreparedChannelData,
        model_config: dict[str, Any],
        best_params: dict[str, Any],
        metrics: dict[str, Any],
        validation_mse: float,
        best_epoch: int,
        n_trials: int,
        max_epochs: int,
        patience: int,
        random_seed: int,
        training_time: float,
        study: optuna.Study,
    ) -> Path:
        artifact_dir = self.model_dir / f"channel_{channel}"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        temporary_model = artifact_dir / "model.pth.tmp"
        torch.save({key: value.detach().cpu() for key, value in model.state_dict().items()}, temporary_model)
        temporary_model.replace(artifact_dir / "model.pth")
        _write_json(artifact_dir / "config.json", model_config)

        scaler_x_temp = artifact_dir / "scaler_x.joblib.tmp"
        scaler_y_temp = artifact_dir / "scaler_y.joblib.tmp"
        joblib.dump(data.scaler_x, scaler_x_temp)
        joblib.dump(data.scaler_y, scaler_y_temp)
        scaler_x_temp.replace(artifact_dir / "scaler_x.joblib")
        scaler_y_temp.replace(artifact_dir / "scaler_y.joblib")
        _write_json(artifact_dir / "metrics.json", metrics)
        _write_json(
            artifact_dir / "training_metadata.json",
            {
                "format_version": 2,
                "model_type": "MLP",
                "channel": channel,
                "objective": "validation_mse_scaled",
                "validation_mse": validation_mse,
                "best_params": best_params,
                "best_epoch": best_epoch,
                "n_trials": n_trials,
                "completed_trials": sum(
                    trial.state == optuna.trial.TrialState.COMPLETE for trial in study.trials
                ),
                "max_epochs": max_epochs,
                "patience": patience,
                "random_seed": random_seed,
                "device": str(self.device),
                "training_time_seconds": training_time,
                "sample_counts": data.sample_counts,
                "split_strategy": data.split_strategy,
                "scaler_fit": "train_only",
                "source_file": str(data.source_path),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return artifact_dir
