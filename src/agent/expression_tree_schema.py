"""Closed, bounded calculation DAG. No code or free-form formula input."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SOURCES = 12
MAX_OUTPUTS = 12

Operator = Literal['add', 'subtract', 'multiply', 'divide', 'percent_ratio', 'pct_change', 'yoy_pct',
                   'difference', 'rolling_mean', 'cumulative_sum', 'rebase_100', 'base_change',
                   'lag', 'inverse', 'log', 'power', 'period_change']


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class SourceSelection(ClosedModel):
    alias: str = Field(pattern=r'^[a-z][a-z0-9_]{0,47}$')
    table_id: str = Field(max_length=64)
    item_id: str = Field(max_length=128)
    classifications: dict[str, str]
    frequency: Literal['D', 'M', 'Q', 'S', 'Y']
    required_data: str = Field(min_length=1, max_length=500)


class ComparisonPeriod(ClosedModel):
    label: str = Field(min_length=1,max_length=80)
    start: str = Field(pattern=r'^\d{8}$')
    end: str = Field(pattern=r'^\d{8}$')
    basis: Literal['exact', 'monthly_mean']


class CategoryComparison(ClosedModel):
    average_nodes: list[str] = Field(min_length=1,max_length=MAX_OUTPUTS)


class ExpressionNode(ClosedModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,47}$')
    operator: Operator
    inputs: list[str] = Field(min_length=1, max_length=2)
    lag: int | None = Field(default=None, ge=1, le=120)
    window: int | None = Field(default=None, ge=2, le=120)
    base_period: str | None = Field(default=None, pattern=r'^\d{4}(?:\d{2}){0,2}$')
    exponent: int | float | None = Field(default=None, ge=-4, le=4)
    periods: list[ComparisonPeriod] | None = Field(default=None,min_length=2,max_length=4)
    direction: Literal['direct','inverse'] | None = None

    @model_validator(mode='after')
    def parameters(self):
        binary = self.operator in {'add', 'subtract', 'multiply', 'divide', 'percent_ratio'}
        if len(self.inputs) != (2 if binary else 1):
            raise ValueError('연산 입력 개수가 맞지 않습니다.')
        allowed = {'pct_change': 'lag', 'difference': 'lag', 'lag': 'lag',
                   'rolling_mean': 'window', 'rebase_100': 'base_period', 'base_change': 'base_period', 'power': 'exponent'}
        parameter = allowed.get(self.operator)
        for key in ('lag', 'window', 'base_period', 'exponent'):
            value = getattr(self, key)
            if key == parameter and value is None:
                raise ValueError(f'{self.operator}: {key}가 필요합니다.')
            if key != parameter and value is not None:
                raise ValueError(f'{self.operator}: {key}는 허용되지 않습니다.')
        if self.operator == 'period_change':
            if not self.periods or not self.direction:
                raise ValueError('period_change에는 periods와 direction이 필요합니다.')
        elif self.periods is not None or self.direction is not None:
            raise ValueError('기간 비교 매개변수는 period_change에만 허용됩니다.')
        return self


class ExpressionOutput(ClosedModel):
    node: str
    label: str = Field(min_length=1, max_length=150)
    display: Literal['both', 'table', 'chart'] = 'both'


class ExpressionPlan(ClosedModel):
    plan_version: Literal['1.0']
    indicator_name: str = Field(min_length=1, max_length=150)
    economic_definition: str = Field(min_length=1, max_length=1500)
    sources: list[SourceSelection] = Field(min_length=1, max_length=MAX_SOURCES)
    nodes: list[ExpressionNode] = Field(min_length=1, max_length=48)
    outputs: list[ExpressionOutput] = Field(min_length=1, max_length=MAX_OUTPUTS)
    start_period: str = Field(pattern=r'^\d{4}(?:\d{2}){0,2}$')
    end_period: str = Field(pattern=r'^\d{4}(?:\d{2}){0,2}$')
    category_comparison: CategoryComparison | None = None
