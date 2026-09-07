"""Project-wide paths and runtime defaults."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TRAIN_DATA_DIR = Path(
    os.environ.get(
        "LIANTE_TRAIN_DATA_DIR",
        PROJECT_ROOT / "train_data" / "data_v5" / "split_by_channel",
    )
).resolve()
MODEL_DIR = Path(os.environ.get("LIANTE_MODEL_DIR", PROJECT_ROOT / "saved_models")).resolve()
GUI_DIR = PROJECT_ROOT / "gui"

N_CHANNELS = 8
INPUT_DIM = 4
OUTPUT_DIM = 2
RANDOM_SEED = 42

