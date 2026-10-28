"""Synthetic source/derived output contracts; no golden-set observations."""
import copy
from types import SimpleNamespace

import pytest
from expression_tree_schema import ExpressionPlan
from expression_tree_validator import validate_plan
from dynamic_expression_executor import DynamicExpressionExecutor, Series, source_series
from derived_indicator_runtime import validate_derived_output_row
from output_agent import OutputAgent
from output_schema import ChartSpec


def plan():
    return ExpressionPlan.model_validate({
        'plan_version': '1.0', 'indicator_name': '합성 지수', 'economic_definition': '전월 차이',
        'sources': [{'alias': 'x', 'table_id': 'synthetic', 'item_id': 'i',
                     'classifications': {}, 'frequency': 'M', 'required_data': '합성 원자료'}],
        'nodes': [{'id': 'delta', 'operator': 'difference', 'inputs': ['x'], 'lag': 1}],
        'outputs': [{'node': 'x', 'label': '원자료', 'display': 'table'},
                    {'node': 'delta', 'label': '차이'}],
        'start_period': '202002', 'end_period': '202003'})


def result():
    p = plan()
    computed = DynamicExpressionExecutor().execute(p, {
        'x': Series({'202001': 100., '202002': 110., '202003': 125.}, '지수', 'M', ['x'])})
    return {'query': '합성 지수와 차이를 표에, 차이만 그래프에',
            'api_plans': [{'table_id': 'synthetic', 'frequency': 'M'}],
            'execution': {'rows': computed['rows'], 'sources': [], 'calculations': []},
            'derivedEvidence': {'plan': p.model_dump()},
            '_derived': {'record': {'plan': p.model_dump()},
                         'series': {k: vars(v) for k, v in computed['series'].items()}}}


def test_raw_output_requires_validated_source_or_node():
    catalog = SimpleNamespace(validate_source_selection=lambda s: None)
    p = plan()
    assert validate_plan(p, catalog)['fetch_start'] == '202001'
    p.outputs[0].node = 'unverified'
    with pytest.raises(ValueError, match='검증된 원자료'):
        validate_plan(p, catalog)


def test_raw_and_calculated_table_chart_and_export_contract():
    r = result()
    agent = OutputAgent()
    output = agent.prepare(r, {'quick_output': True})
    assert [s['label'] for s in output['tableSeries']] == ['원자료', '차이']
    assert [s['label'] for s in output['visualization']['series']] == ['차이']
    assert [p['value'] for p in output['tableSeries'][0]['points']] == [110., 125.]
    assert [p['value'] for p in output['visualization']['series'][0]['points']] == [10., 15.]
    assert '원자료' in str(output['tableDocument'])
    assert '원자료' not in [t.get('name') for t in output['plotlyFigure']['data']]
    from report_export import document
    assert [s['label'] for s in document(r, ChartSpec())['series']] == ['차이']
    assert agent.inspect(r)['seriesCount'] == 1


def test_old_plans_default_to_both_and_chart_only_is_excluded_from_table():
    r = result()
    outputs = r['_derived']['record']['plan']['outputs']
    outputs[0].pop('display')
    outputs[1]['display'] = 'chart'
    agent = OutputAgent()
    series = agent._series(r['execution']['rows'])
    assert [s['label'] for s in agent._output_series(r, series)] == ['원자료', '차이']
    assert [s['label'] for s in agent._output_series(r, series, 'table')] == ['원자료']


@pytest.mark.parametrize('key,value', [('DT', 999.), ('UNIT_NM', '%'),
                                      ('_SERIES_LABEL', '위조'), ('_SOURCE_SERIES_ID', 'unknown')])
def test_raw_output_tampering_is_rejected(key, value):
    r = result()
    row = copy.deepcopy(r['execution']['rows'][0])
    validate_derived_output_row(r, row, {'synthetic'})
    row[key] = value
    with pytest.raises(ValueError):
        validate_derived_output_row(r, row, {'synthetic'})


@pytest.mark.parametrize('frequency,code,expected', [('Q', '2024Q4', '202404'),
                                                   ('S', '2024H2', '202402')])
def test_kosis_observation_calendar_codes(frequency, code, expected):
    source = plan().sources[0].model_copy(update={'frequency': frequency})
    row = {'ORG_ID': '301', 'TBL_ID': 'synthetic', 'ITM_ID': 'i',
           'PRD_SE': frequency, 'PRD_DE': code, 'DT': '100', 'UNIT_NM': '지수'}
    response = {'status': 'success', 'source': 'kosis_api', 'table_id': 'synthetic', 'rows': [row]}
    assert source_series(source, response).values == {expected: 100.}
    row['PRD_DE'] = code[:-1] + '9'
    with pytest.raises(ValueError):
        source_series(source, response)


def test_canonical_and_kosis_codes_cannot_duplicate_same_period():
    source = plan().sources[0].model_copy(update={'frequency': 'Q'})
    row = {'ORG_ID': '301', 'TBL_ID': 'synthetic', 'ITM_ID': 'i',
           'PRD_SE': 'Q', 'PRD_DE': '2024Q4', 'DT': '100', 'UNIT_NM': '지수'}
    response = {'status': 'success', 'source': 'kosis_api', 'table_id': 'synthetic',
                'rows': [row, {**row, 'PRD_DE': '202404'}]}
    with pytest.raises(ValueError, match='중복'):
        source_series(source, response)


def test_base_change_uses_fixed_base_and_percent_unit():
    p=plan()
    p.nodes[0]=p.nodes[0].model_copy(update={'operator':'base_change','base_period':'202001','lag':None})
    p=ExpressionPlan.model_validate(p.model_dump())
    assert validate_plan(p,SimpleNamespace(validate_source_selection=lambda s:None))['fetch_start']=='202001'
    computed=DynamicExpressionExecutor().execute(p,{'x':Series({'202001':80.,'202002':100.,'202003':120.},'지수','M',['x'])})
    assert computed['series']['delta'].values['202002']==pytest.approx(25.)
    assert computed['series']['delta'].values['202003']==pytest.approx(50.)
    assert computed['series']['delta'].unit=='%'


@pytest.mark.parametrize('base',[0.,None])
def test_base_change_does_not_invent_missing_or_zero_base(base):
    p=plan()
    p.outputs=p.outputs[1:]
    p.nodes[0]=p.nodes[0].model_copy(update={'operator':'base_change','base_period':'202001','lag':None})
    with pytest.raises(ValueError,match='계산 가능한 관측값이 없습니다'):
        DynamicExpressionExecutor().execute(p,{'x':Series({'202001':base,'202002':100.},'지수','M',['x'])})


def test_base_change_does_not_bypass_rate_growth_ambiguity():
    p=plan()
    p.nodes[0]=p.nodes[0].model_copy(update={'operator':'base_change','base_period':'202001','lag':None})
    with pytest.raises(ValueError,match='비율의 증가율'):
        DynamicExpressionExecutor().execute(p,{'x':Series({'202001':2.,'202002':3.},'연리%','M',['x'])})


def test_stock_and_transaction_default_layout_preserves_different_meaning():
    result={'api_plans':[{'table_name':'잔액표'}, {'table_name':'거래표'}]}
    series=[{'label':'잔액','unit':'십억원'}, {'label':'거래','unit':'십억원'}]
    assert '잔액과 기간 중 거래' in OutputAgent._layout_reason(result,series)
    assert not OutputAgent._layout_reason(result,series[:1])
