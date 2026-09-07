"""Leak-free, reproducible Channel CSV preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


FEATURE_COLUMN_INDICES = (3, 4, 5, 6)
TARGET_COLUMN_INDICES = (11, 12)


@dataclass(frozen=True)
class PreparedChannelData:
    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    y_test_raw: np.ndarray
    scaler_x: StandardScaler
    scaler_y: StandardScaler
    source_path: Path
    split_strategy: str

    @property
    def sample_counts(self) -> dict[str, int]:
        return {
            "train": len(self.x_train),
            "validation": len(self.x_val),
            "test": len(self.x_test),
        }


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"无法识别 CSV 编码: {path}") from last_error


def load_channel_data(
    channel_id: int,
    data_dir: str | Path,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> PreparedChannelData:
    """Load one channel using the original row order and fit scalers on train only.

    The legacy project always split rows sequentially. Because the repository does
    not carry a data dictionary proving that row order is exchangeable, this keeps
    that conservative chronological behavior and holds the final rows out as test.
    """

    if isinstance(channel_id, bool) or not 0 <= channel_id <= 7:
        raise ValueError("channel_id 必须是 0 到 7 的整数")
    if train_fraction <= 0 or validation_fraction <= 0:
        raise ValueError("训练集和验证集比例必须大于 0")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("必须为独立测试集预留数据")

    source_path = Path(data_dir) / f"channel_{channel_id}_ea.csv"
    if not source_path.is_file():
        raise FileNotFoundError(f"Channel {channel_id} 数据不存在: {source_path}")

    frame = _read_csv(source_path)
    required_columns = max(*FEATURE_COLUMN_INDICES, *TARGET_COLUMN_INDICES) + 1
    if frame.shape[1] < required_columns:
        raise ValueError(f"{source_path.name} 至少需要 {required_columns} 列")

    positions = [*FEATURE_COLUMN_INDICES, *TARGET_COLUMN_INDICES]
    selected = frame.iloc[:, positions].apply(pd.to_numeric, errors="coerce").dropna()
    if len(selected) < 10:
        raise ValueError(f"{source_path.name} 至少需要 10 条有效数据")

    features = selected.iloc[:, :4].to_numpy(dtype=np.float32)
    targets = selected.iloc[:, 4:].to_numpy(dtype=np.float32)
    train_end = max(1, int(len(features) * train_fraction))
    validation_end = max(train_end + 1, int(len(features) * (train_fraction + validation_fraction)))
    validation_end = min(validation_end, len(features) - 1)
    if train_end >= validation_end or validation_end >= len(features):
        raise ValueError("数据不足以划分独立 train/validation/test")

    x_train_raw = features[:train_end]
    x_val_raw = features[train_end:validation_end]
    x_test_raw = features[validation_end:]
    y_train_raw = targets[:train_end]
    y_val_raw = targets[train_end:validation_end]
    y_test_raw = targets[validation_end:]

    scaler_x = StandardScaler().fit(x_train_raw)
    scaler_y = StandardScaler().fit(y_train_raw)

    return PreparedChannelData(
        x_train=scaler_x.transform(x_train_raw).astype(np.float32),
        y_train=scaler_y.transform(y_train_raw).astype(np.float32),
        x_val=scaler_x.transform(x_val_raw).astype(np.float32),
        y_val=scaler_y.transform(y_val_raw).astype(np.float32),
        x_test=scaler_x.transform(x_test_raw).astype(np.float32),
        y_test=scaler_y.transform(y_test_raw).astype(np.float32),
        y_test_raw=y_test_raw,
        scaler_x=scaler_x,
        scaler_y=scaler_y,
        source_path=source_path,
        split_strategy="sequential_70_15_15",
    )

