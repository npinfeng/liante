from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def channel_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "train_data"
    data_dir.mkdir()
    write_channel_csv(data_dir, 0)
    return data_dir


def write_channel_csv(data_dir: Path, channel: int, rows: int = 80) -> Path:
    rng = np.random.default_rng(100 + channel)
    x = rng.normal(size=(rows, 4)).astype(np.float32)
    ea = 1.5 * x[:, 0] - 0.3 * x[:, 1] + 0.2 * x[:, 2] + 0.1
    bias = -0.4 * x[:, 0] + 0.8 * x[:, 2] + 0.5 * x[:, 3] - 0.2
    values = np.zeros((rows, 13), dtype=np.float32)
    values[:, 3:7] = x
    values[:, 11] = ea
    values[:, 12] = bias
    path = data_dir / f"channel_{channel}_ea.csv"
    pd.DataFrame(values, columns=[f"col_{index}" for index in range(13)]).to_csv(path, index=False)
    return path

