"""Load versioned channel artifacts and execute MLP prediction."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler

from models.mlp import DynamicMLP, MLPConfig


@dataclass
class LoadedArtifact:
    model: DynamicMLP
    scaler_x: StandardScaler
    scaler_y: StandardScaler
    config: MLPConfig
    metrics: dict


class PredictionService:
    def __init__(
        self,
        model_dir: str | Path,
        device: str | torch.device | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._cache: dict[int, LoadedArtifact] = {}
        self._lock = threading.RLock()

    def artifact_dir(self, channel: int) -> Path:
        return self.model_dir / f"channel_{channel}"

    def available_channels(self) -> list[int]:
        channels: list[int] = []
        for channel in range(8):
            if self._has_complete_artifact(channel):
                channels.append(channel)
        return channels

    def invalidate(self, channel: int) -> None:
        with self._lock:
            self._cache.pop(channel, None)

    def predict(self, channel: int, rows: list[list[float]] | np.ndarray) -> list[dict[str, float]]:
        values = np.asarray(rows, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] != 4:
            raise ValueError("data 必须是非空的 N x 4 数值数组")
        if not np.isfinite(values).all():
            raise ValueError("data 不能包含 NaN 或无穷大")
        artifact = self.load(channel)
        transformed = artifact.scaler_x.transform(values).astype(np.float32)
        tensor = torch.as_tensor(transformed, dtype=torch.float32, device=self.device)
        with torch.inference_mode():
            scaled_predictions = artifact.model(tensor).cpu().numpy()
        predictions = artifact.scaler_y.inverse_transform(scaled_predictions)
        return [
            {"ea": float(prediction[0]), "bias": float(prediction[1])}
            for prediction in predictions
        ]

    def predict_file(
        self,
        channel: int,
        file_path: str,
        sheet_name: str | int = 0,
        feature_columns: list[str | int] | None = None,
    ) -> tuple[list[dict[str, float]], dict]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"预测文件不存在: {path}")
        if path.suffix.lower() == ".csv":
            frame = None
            for encoding in ("utf-8-sig", "gb18030", "latin1"):
                try:
                    frame = pd.read_csv(path, encoding=encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if frame is None:
                raise ValueError("无法识别 CSV 编码")
        elif path.suffix.lower() == ".xlsx":
            frame = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
        else:
            raise ValueError("预测文件只支持 .csv 和 .xlsx")
        if frame.empty or len(frame) > 100_000:
            raise ValueError("预测文件必须包含 1 到 100000 行")
        if feature_columns is None:
            if frame.shape[1] == 4:
                selected = frame.iloc[:, :4]
            elif frame.shape[1] >= 7:
                selected = frame.iloc[:, [3, 4, 5, 6]]
            else:
                raise ValueError("文件需要恰好 4 列或至少 7 列")
        elif all(isinstance(column, str) for column in feature_columns):
            selected = frame.loc[:, feature_columns]
        elif all(isinstance(column, int) and not isinstance(column, bool) for column in feature_columns):
            selected = frame.iloc[:, feature_columns]
        else:
            raise ValueError("feature_columns 必须全部是列名或全部是列序号")
        numeric = selected.apply(pd.to_numeric, errors="coerce")
        if numeric.isna().any().any():
            raise ValueError("预测特征包含空值或非数值")
        predictions = self.predict(channel, numeric.to_numpy(dtype=np.float32))
        with_rows = [
            {"row": index, **prediction}
            for index, prediction in enumerate(predictions, start=2)
        ]
        return with_rows, {
            "file_name": path.name,
            "feature_columns": [str(column) for column in selected.columns],
            "row_count": len(frame),
        }

    def load(self, channel: int) -> LoadedArtifact:
        if isinstance(channel, bool) or not 0 <= channel <= 7:
            raise ValueError("channel 必须是 0 到 7 的整数")
        directory = self.artifact_dir(channel)
        files = self._artifact_files(directory)
        missing = [path.name for path in files if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"Channel {channel} 模型 artifact 不完整，缺少: {', '.join(missing)}"
            )
        with self._lock:
            cached = self._cache.get(channel)
            if cached is not None:
                return cached
            loaded = self._load(directory)
            self._cache[channel] = loaded
            return loaded

    def _has_complete_artifact(self, channel: int) -> bool:
        return all(path.is_file() for path in self._artifact_files(self.artifact_dir(channel)))

    @staticmethod
    def _artifact_files(directory: Path) -> tuple[Path, ...]:
        return (
            directory / "model.pth",
            directory / "config.json",
            directory / "scaler_x.joblib",
            directory / "scaler_y.joblib",
            directory / "metrics.json",
        )

    def _load(self, directory: Path) -> LoadedArtifact:
        config_values = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        config = MLPConfig.from_dict(config_values)
        if config.input_dim != 4 or config.output_dim != 2:
            raise ValueError("当前 API 只支持 4 维输入、2 维输出")
        model = DynamicMLP(config)
        try:
            state_dict = torch.load(directory / "model.pth", map_location="cpu", weights_only=True)
        except TypeError:  # PyTorch 1.x compatibility
            state_dict = torch.load(directory / "model.pth", map_location="cpu")
        model.load_state_dict(state_dict)
        model.to(self.device).eval()
        scaler_x = joblib.load(directory / "scaler_x.joblib")
        scaler_y = joblib.load(directory / "scaler_y.joblib")
        if getattr(scaler_x, "n_features_in_", None) != 4:
            raise ValueError("scaler_x 与 4 维输入不兼容")
        if getattr(scaler_y, "n_features_in_", None) != 2:
            raise ValueError("scaler_y 与 2 维输出不兼容")
        metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        return LoadedArtifact(model, scaler_x, scaler_y, config, metrics)
