"""Bounded manual evidence; measurements carry explicit physical units."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)

class Measurement(Strict):
    name: str=Field(min_length=1,max_length=80)
    value: float
    unit: str=Field(min_length=1,max_length=32)
    lower: float | None=None
    upper: float | None=None
    @model_validator(mode='after')
    def bounds(self):
        if self.lower is not None and self.upper is not None and self.lower>self.upper:
            raise ValueError('测量阈值上下限颠倒')
        if self.lower is not None and self.value<self.lower or self.upper is not None and self.value>self.upper:
            raise ValueError('测量不在声明的合格范围内')
        return self

class Optical(Strict):
    qualified: Literal[True]
    power_dbm: float | None=None  # Existing unit-specific field remains compatible.
    measurements: list[Measurement]=Field(default_factory=list,max_length=32)
    @model_validator(mode='after')
    def evidence(self):
        if self.power_dbm is None and not self.measurements: raise ValueError('光学测量不能为空')
        return self

class Electrical(Strict):
    qualified: Literal[True]
    voltage_v: float | None=None
    measurements: list[Measurement]=Field(default_factory=list,max_length=32)
    @model_validator(mode='after')
    def evidence(self):
        if self.voltage_v is None and not self.measurements: raise ValueError('电学测量不能为空')
        return self

class Tuning(Strict):
    status: Literal['complete']
    notes: str=Field(default='',max_length=2000)

class TuningParameters(Strict):
    silicon_power_dbm: float
    silicon_extinction_ratio_db: float
    non_silicon_power_dbm: float
    non_silicon_extinction_ratio_db: float

class Manual(Strict):
    decision: Literal['qualified']
    reviewer: str=Field(default='',max_length=128)
    measured_at: datetime
    notes: str=Field(min_length=1,max_length=2000)
    grading_basis: str=Field(default='',max_length=2000)
    customer_grade: Literal['A','B','C'] | None=None

    @model_validator(mode='after')
    def customer_basis(self):
        if self.customer_grade and not self.grading_basis.strip():
            raise ValueError('填写客户分级时必须提供分级依据')
        return self

class QualificationRequest(Strict):
    product_id: str=Field(min_length=1,max_length=200)
    aoi_task_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    stage2_task_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    optical_features: Optical
    electrical_features: Electrical
    tuning_result: Tuning
    tuning_parameters: TuningParameters | None=None
    manual_review: Manual
    recipe_id: str=Field(min_length=1,max_length=128)
    recipe_version: str=Field(min_length=1,max_length=128)
    source_id: str | None=Field(default=None,max_length=128)
    request_id: str | None=Field(default=None,min_length=1,max_length=200)
    notes: str=Field(default='',max_length=2000)

class ReasonRequest(Strict):
    reason: str=Field(min_length=1,max_length=2000)
    request_id: str | None=Field(default=None,min_length=1,max_length=200)

class ReviewRequest(Strict):
    product_id: str=Field(min_length=1,max_length=200)
    qualification_id: str | None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    stage3_task_id: str | None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    decision: Literal['APPROVE','REJECT']
    comment: str=Field(min_length=1,max_length=2000)
    final_grade: Literal['A','B','C','D'] | None=None
    request_id: str | None=Field(default=None,min_length=1,max_length=200)
