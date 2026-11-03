"""Synthetic medians and ranges; missing periods never become partial windows."""
import pytest
from test_dynamic_derived_indicators import catalog, plan, inputs
from dynamic_expression_executor import DynamicExpressionExecutor
from expression_tree_schema import ExpressionNode
from expression_tree_validator import validate_plan, result_unit


@pytest.mark.parametrize('operator,values,expected', [
    ('rolling_sum', [7, -2, 4], 9),
    ('rolling_median', [0, 100, 1, 2], 1.5),
    ('rolling_median', [8, -3, 2], 2),
    ('rolling_range', [-5, 4, 1], 9),
])
def test_period_statistics_have_their_own_arithmetic(catalog, operator, values, expected):
    p = plan(catalog, operator, window=len(values))
    p.start_period = p.end_period = f'2020{len(values):02d}'
    bounds = validate_plan(p, catalog)
    assert bounds['fetch_start'] == '202001'
    data = inputs({f'2020{i:02d}': v for i, v in enumerate(values, 1)}, unit='지수')
    result = DynamicExpressionExecutor().execute(p, data)
    assert len(result['rows']) == 1
    assert result['rows'][0]['DT'] == pytest.approx(expected)
    assert result['rows'][0]['UNIT_NM'] == ('포인트' if operator == 'rolling_range' else '지수')


@pytest.mark.parametrize('operator', ['rolling_sum', 'rolling_median', 'rolling_range'])
def test_missing_calendar_point_blocks_entire_window(catalog, operator):
    p = plan(catalog, operator, window=3)
    p.start_period = p.end_period = '202003'
    with pytest.raises(ValueError, match='계산 가능한 관측값'):
        DynamicExpressionExecutor().execute(p, inputs({'202001': 1, '202003': 100}))
    with pytest.raises(ValueError):
        ExpressionNode(id='out', operator=operator, inputs=['x'])


def test_range_is_a_difference_unit_not_a_relative_change():
    assert result_unit('rolling_range', ['연리%']) == '%p'
    assert result_unit('rolling_median', ['연리%']) == '연리%'
