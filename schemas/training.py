"""Validated MLP AutoML request schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SUPPORTED_ACTIVATIONS = {"relu", "gelu", "silu", "tanh"}
SUPPORTED_OPTIMIZERS = {"Adam", "AdamW", "SGD"}
SUPPORTED_SCHEDULERS = {"none", "cosine", "plateau"}
SUPPORTED_BATCH_SIZES = {16, 32, 64, 128, 256}
SUPPORTED_HIDDEN_SIZES = {32, 64, 128, 256, 512}


class SearchSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_layers: tuple[int, int] = (1, 5)
    hidden_sizes: list[int] = [32, 64, 128, 256, 512]
    activations: list[str] = ["relu", "gelu", "silu", "tanh"]
    dropout: tuple[float, float] = (0.0, 0.5)
    batch_norm: list[bool] = [True, False]
    optimizers: list[str] = ["Adam", "AdamW", "SGD"]
    learning_rate: tuple[float, float] = (1e-5, 1e-2)
    weight_decay: tuple[float, float] = (1e-6, 1e-2)
    batch_sizes: list[int] = [16, 32, 64, 128, 256]
    schedulers: list[str] = ["none", "cosine", "plateau"]

    @model_validator(mode="after")
    def validate_ranges_and_choices(self) -> "SearchSpace":
        low_layers, high_layers = self.n_layers
        if low_layers < 1 or high_layers > 5 or low_layers > high_layers:
            raise ValueError("n_layers 必须是 [1, 5] 范围内的递增区间")
        if not self.hidden_sizes or not set(self.hidden_sizes) <= SUPPORTED_HIDDEN_SIZES:
            raise ValueError("hidden_sizes 只允许 32/64/128/256/512")
        if not self.activations or not set(self.activations) <= SUPPORTED_ACTIVATIONS:
            raise ValueError("activations 包含不支持的值")
        if not self.optimizers or not set(self.optimizers) <= SUPPORTED_OPTIMIZERS:
            raise ValueError("optimizers 包含不支持的值")
        if not self.schedulers or not set(self.schedulers) <= SUPPORTED_SCHEDULERS:
            raise ValueError("schedulers 包含不支持的值")
        if not self.batch_sizes or not set(self.batch_sizes) <= SUPPORTED_BATCH_SIZES:
            raise ValueError("batch_sizes 只允许 16/32/64/128/256")
        if not self.batch_norm:
            raise ValueError("batch_norm 不能为空")
        dropout_low, dropout_high = self.dropout
        if not 0 <= dropout_low <= dropout_high <= 0.5:
            raise ValueError("dropout 必须是 [0.0, 0.5] 内的递增区间")
        for name, (low, high) in {
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
        }.items():
            if low <= 0 or high <= 0 or low > high:
                raise ValueError(f"{name} 必须是正数递增区间")
        if self.learning_rate[1] > 1e-2:
            raise ValueError("learning_rate 最大值不能超过 1e-2")
        if self.weight_decay[1] > 1e-2:
            raise ValueError("weight_decay 最大值不能超过 1e-2")
        return self


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: int = Field(ge=0, le=7)
    n_trials: int = Field(default=30, ge=1, le=500)
    max_epochs: int = Field(default=500, ge=1, le=10_000)
    patience: int = Field(default=40, ge=1, le=1_000)
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    search_space: SearchSpace = Field(default_factory=SearchSpace)

    @field_validator("patience")
    @classmethod
    def patience_not_above_epochs(cls, value: int, info):
        max_epochs = info.data.get("max_epochs")
        if max_epochs is not None and value > max_epochs:
            return max_epochs
        return value


class TrainAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[int] = Field(default_factory=lambda: list(range(8)), min_length=1, max_length=8)
    n_trials: int = Field(default=30, ge=1, le=500)
    max_epochs: int = Field(default=500, ge=1, le=10_000)
    patience: int = Field(default=40, ge=1, le=1_000)
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    search_space: SearchSpace = Field(default_factory=SearchSpace)

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, channels: list[int]) -> list[int]:
        if any(isinstance(channel, bool) or channel < 0 or channel > 7 for channel in channels):
            raise ValueError("channels 只允许 0 到 7")
        if len(set(channels)) != len(channels):
            raise ValueError("channels 不能重复")
        return channels

    @field_validator("patience")
    @classmethod
    def patience_not_above_epochs(cls, value: int, info):
        max_epochs = info.data.get("max_epochs")
        if max_epochs is not None and value > max_epochs:
            return max_epochs
        return value

