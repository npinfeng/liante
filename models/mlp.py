"""The single authoritative dynamic MLP implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import torch
from torch import nn


@dataclass(frozen=True)
class MLPConfig:
    input_dim: int = 4
    output_dim: int = 2
    hidden_dims: tuple[int, ...] = (64, 32)
    activation: str = "relu"
    dropout: float = 0.0
    batch_norm: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "hidden_dims", tuple(int(size) for size in self.hidden_dims))
        if self.input_dim < 1 or self.output_dim < 1:
            raise ValueError("input_dim 和 output_dim 必须大于 0")
        if not self.hidden_dims or any(size < 1 for size in self.hidden_dims):
            raise ValueError("hidden_dims 必须至少包含一个正整数")
        if self.activation.lower() not in DynamicMLP.ACTIVATIONS:
            raise ValueError(f"不支持的激活函数: {self.activation}")
        if not 0.0 <= self.dropout <= 0.5:
            raise ValueError("dropout 必须在 [0.0, 0.5] 范围内")

    def to_dict(self, include_model_type: bool = False) -> dict[str, Any]:
        values = asdict(self)
        values["hidden_dims"] = list(self.hidden_dims)
        if include_model_type:
            values = {"model_type": "MLP", **values}
        return values

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "MLPConfig":
        if values.get("model_type", "MLP") != "MLP":
            raise ValueError("model_type 必须是 MLP")
        return cls(
            input_dim=int(values.get("input_dim", 4)),
            output_dim=int(values.get("output_dim", 2)),
            hidden_dims=tuple(int(size) for size in values["hidden_dims"]),
            activation=str(values.get("activation", "relu")).lower(),
            dropout=float(values.get("dropout", 0.0)),
            batch_norm=bool(values.get("batch_norm", False)),
        )


class DynamicMLP(nn.Module):
    """Multi-output regression MLP built from :class:`MLPConfig`."""

    ACTIVATIONS: dict[str, type[nn.Module]] = {
        "relu": nn.ReLU,
        "gelu": nn.GELU,
        "silu": nn.SiLU,
        "tanh": nn.Tanh,
    }

    def __init__(
        self,
        input_dim: int | MLPConfig = 4,
        output_dim: int = 2,
        hidden_dims: Sequence[int] = (64, 32),
        activation: str = "relu",
        dropout: float = 0.0,
        batch_norm: bool = False,
    ) -> None:
        super().__init__()
        if isinstance(input_dim, MLPConfig):
            config = input_dim
        else:
            config = MLPConfig(
                input_dim=input_dim,
                output_dim=output_dim,
                hidden_dims=tuple(hidden_dims),
                activation=activation.lower(),
                dropout=dropout,
                batch_norm=batch_norm,
            )

        layers: list[nn.Module] = []
        previous_dim = config.input_dim
        activation_class = self.ACTIVATIONS[config.activation]
        for hidden_dim in config.hidden_dims:
            layers.append(nn.Linear(previous_dim, hidden_dim))
            if config.batch_norm:
                layers.append(nn.BatchNorm1d(hidden_dim))
            layers.append(activation_class())
            if config.dropout > 0:
                layers.append(nn.Dropout(config.dropout))
            previous_dim = hidden_dim
        layers.append(nn.Linear(previous_dim, config.output_dim))

        self.config = config
        self.network = nn.Sequential(*layers)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.network(inputs)


# Compatibility name used by the project's first MLP-only refactor.
ConfigurableMLP = DynamicMLP

