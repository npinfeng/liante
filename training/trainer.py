"""Reusable mini-batch MLP training loops."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
import optuna
import torch
from torch import nn
from torch.optim import Optimizer
from torch.utils.data import DataLoader, TensorDataset


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[..., None]


@dataclass
class TrainingOutcome:
    best_validation_loss: float
    best_epoch: int
    epochs_trained: int
    state_dict: dict[str, torch.Tensor]


def make_data_loader(
    features: np.ndarray,
    targets: np.ndarray,
    batch_size: int,
    seed: int,
) -> DataLoader:
    dataset = TensorDataset(
        torch.as_tensor(features, dtype=torch.float32),
        torch.as_tensor(targets, dtype=torch.float32),
    )
    generator = torch.Generator().manual_seed(seed)
    # BatchNorm cannot train on a final batch containing only one row.
    drop_last = len(dataset) > 1 and len(dataset) % min(batch_size, len(dataset)) == 1
    return DataLoader(
        dataset,
        batch_size=min(batch_size, len(dataset)),
        shuffle=True,
        drop_last=drop_last,
        generator=generator,
    )


def make_scheduler(name: str, optimizer: Optimizer, max_epochs: int):
    if name == "none":
        return None
    if name == "cosine":
        return torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max_epochs)
    if name == "plateau":
        return torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=max(2, max_epochs // 10)
        )
    raise ValueError(f"不支持的 scheduler: {name}")


def _validation_loss(
    model: nn.Module,
    features: np.ndarray,
    targets: np.ndarray,
    device: torch.device,
) -> float:
    model.eval()
    with torch.inference_mode():
        predictions = model(torch.as_tensor(features, dtype=torch.float32, device=device))
        expected = torch.as_tensor(targets, dtype=torch.float32, device=device)
        return float(nn.functional.mse_loss(predictions, expected).item())


def train_with_early_stopping(
    model: nn.Module,
    optimizer: Optimizer,
    scheduler_name: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    *,
    batch_size: int,
    max_epochs: int,
    patience: int,
    seed: int,
    device: torch.device,
    trial: optuna.Trial | None = None,
    progress_callback: ProgressCallback | None = None,
) -> TrainingOutcome:
    model.to(device)
    loader = make_data_loader(x_train, y_train, batch_size, seed)
    scheduler = make_scheduler(scheduler_name, optimizer, max_epochs)
    criterion = nn.MSELoss()
    best_loss = float("inf")
    best_epoch = 0
    best_state: dict[str, torch.Tensor] | None = None
    epochs_without_improvement = 0

    for epoch in range(1, max_epochs + 1):
        model.train()
        for features, targets in loader:
            features = features.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(features), targets)
            loss.backward()
            optimizer.step()

        validation_loss = _validation_loss(model, x_val, y_val, device)
        if scheduler_name == "plateau":
            scheduler.step(validation_loss)
        elif scheduler is not None:
            scheduler.step()

        if validation_loss < best_loss - 1e-10:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if trial is not None:
            trial.report(validation_loss, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()
        if progress_callback is not None:
            progress_callback(epoch=epoch, validation_loss=validation_loss, best_loss=best_loss)
        if epoch == 1 or epoch % 25 == 0 or epoch == max_epochs:
            LOGGER.info(
                "epoch=%s validation_loss=%.8f best_loss=%.8f",
                epoch,
                validation_loss,
                best_loss,
            )
        if epochs_without_improvement >= patience:
            LOGGER.info("early_stopping epoch=%s patience=%s", epoch, patience)
            break

    if best_state is None:
        raise RuntimeError("训练未产生有效模型")
    model.load_state_dict(best_state)
    return TrainingOutcome(best_loss, best_epoch, epoch, best_state)


def fit_fixed_epochs(
    model: nn.Module,
    optimizer: Optimizer,
    scheduler_name: str,
    features: np.ndarray,
    targets: np.ndarray,
    *,
    batch_size: int,
    epochs: int,
    seed: int,
    device: torch.device,
) -> None:
    model.to(device)
    loader = make_data_loader(features, targets, batch_size, seed)
    scheduler = make_scheduler(scheduler_name, optimizer, epochs)
    criterion = nn.MSELoss()
    for _ in range(epochs):
        model.train()
        running_loss = 0.0
        batch_count = 0
        for batch_features, batch_targets in loader:
            batch_features = batch_features.to(device)
            batch_targets = batch_targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_features), batch_targets)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.detach().item())
            batch_count += 1
        if scheduler is not None:
            if scheduler_name == "plateau":
                scheduler.step(running_loss / max(1, batch_count))
            else:
                scheduler.step()
