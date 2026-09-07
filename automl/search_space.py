"""Optuna parameter sampling for MLPs only."""

from __future__ import annotations

from typing import Any

import optuna

from models.mlp import MLPConfig
from schemas.training import SearchSpace


def suggest_parameters(trial: optuna.Trial, space: SearchSpace) -> dict[str, Any]:
    n_layers = trial.suggest_int("n_layers", space.n_layers[0], space.n_layers[1])
    hidden_dims = [
        trial.suggest_categorical(f"hidden_{index}", space.hidden_sizes)
        for index in range(n_layers)
    ]
    optimizer = trial.suggest_categorical("optimizer", space.optimizers)
    parameters: dict[str, Any] = {
        "n_layers": n_layers,
        "hidden_dims": hidden_dims,
        "activation": trial.suggest_categorical("activation", space.activations),
        "dropout": trial.suggest_float("dropout", space.dropout[0], space.dropout[1]),
        "batch_norm": trial.suggest_categorical("batch_norm", space.batch_norm),
        "optimizer": optimizer,
        "learning_rate": trial.suggest_float(
            "learning_rate", space.learning_rate[0], space.learning_rate[1], log=True
        ),
        "weight_decay": trial.suggest_float(
            "weight_decay", space.weight_decay[0], space.weight_decay[1], log=True
        ),
        "batch_size": trial.suggest_categorical("batch_size", space.batch_sizes),
        "scheduler": trial.suggest_categorical("scheduler", space.schedulers),
    }
    if optimizer == "SGD":
        parameters["momentum"] = trial.suggest_float("momentum", 0.0, 0.95)
    return parameters


def params_from_trial(trial: optuna.trial.FrozenTrial) -> dict[str, Any]:
    params = dict(trial.params)
    n_layers = int(params["n_layers"])
    params["hidden_dims"] = [int(params[f"hidden_{index}"]) for index in range(n_layers)]
    return params


def model_config_from_params(params: dict[str, Any]) -> MLPConfig:
    return MLPConfig(
        input_dim=4,
        output_dim=2,
        hidden_dims=tuple(params["hidden_dims"]),
        activation=str(params["activation"]),
        dropout=float(params["dropout"]),
        batch_norm=bool(params["batch_norm"]),
    )

