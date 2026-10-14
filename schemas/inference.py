"""Validated requests for asynchronous Stage2 inference."""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal


class TaskCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    stage: Literal['stage1','stage2','stage3','automl']
    product_id: str=Field(min_length=1,max_length=200)
    request_id: str | None=Field(default=None,min_length=1,max_length=200)


class StreamCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    product_id: str=Field(min_length=1,max_length=200)
    source_id: str=Field(min_length=1,max_length=128)
    request_id: str | None=Field(default=None,min_length=1,max_length=200)
    view_types: list[str] | None=Field(default=None,min_length=1,max_length=32)
    required_views: list[str] | None=Field(default=None,min_length=1,max_length=32)


class AOIFolderReplayRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    directory: str=Field(min_length=1,max_length=1024)
    product_id: str=Field(min_length=1,max_length=200)
    source_id: str=Field(default='AOI-FOLDER-DEMO',min_length=1,max_length=128)
    interval_seconds: float=Field(default=1.0,ge=0.1,le=10)
    request_id: str | None=Field(default=None,min_length=1,max_length=200)


class BatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(min_length=1)
    channels: list[int] = Field(min_length=1, max_length=8)
    sheet_name: str | int = 0
    feature_columns: list[str | int] | None = None

    @field_validator("channels")
    @classmethod
    def channels_valid(cls, values: list[int]) -> list[int]:
        if any(isinstance(value, bool) or value < 0 or value > 7 for value in values) or len(set(values)) != len(values):
            raise ValueError("channels 必须是不重复的 0 到 7")
        return values

    @field_validator("feature_columns")
    @classmethod
    def columns_valid(cls, values):
        if values is not None and len(values) != 4:
            raise ValueError("feature_columns 必须有 4 项")
        return values


class BatchPredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1)
    request_id: str | None = None
    files: list[BatchFile] = Field(min_length=1, max_length=100)


class FolderPredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=100)
    recursive: bool = True
    product_id: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)

    @field_validator("product_id")
    @classmethod
    def trim_product(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator("request_id")
    @classmethod
    def request_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("request_id 不能是空白字符串")
        return value


class Stage2SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    directory: str = Field(min_length=1, max_length=1000)
    owner_device_id: str | None = Field(default=None, min_length=1, max_length=128,
                                       pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
