"""Prediction API schemas."""

from pydantic import BaseModel, Field, field_validator


class PredictRequest(BaseModel):
    channel: int = Field(ge=0, le=7)
    data: list[list[float]] = Field(min_length=1, max_length=100_000)

    @field_validator("data")
    @classmethod
    def validate_rows(cls, rows: list[list[float]]) -> list[list[float]]:
        if any(len(row) != 4 for row in rows):
            raise ValueError("每一行必须恰好包含 4 个特征")
        return rows


class FilePredictRequest(BaseModel):
    channel: int = Field(ge=0, le=7)
    file_path: str = Field(min_length=1)
    sheet_name: str | int = 0
    feature_columns: list[str | int] | None = None

    @field_validator("feature_columns")
    @classmethod
    def validate_columns(cls, columns: list[str | int] | None):
        if columns is not None and len(columns) != 4:
            raise ValueError("feature_columns 必须恰好包含 4 项")
        return columns


class Prediction(BaseModel):
    ea: float
    bias: float


class PredictResponse(BaseModel):
    status: str = "success"
    channel: int
    predictions: list[Prediction]
