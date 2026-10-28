"""Percentage-point changes use synthetic observations, not evaluation answers."""
from types import SimpleNamespace

import pytest
from expression_tree_schema import ExpressionPlan
from expression_tree_validator import validate_plan
from dynamic_expression_executor import DynamicExpressionExecutor, Series


def plan():
    sources=[{'alias':a,'table_id':a,'item_id':'i','classifications':{},
              'frequency':'M','required_data':'합성 잔액'} for a in ['a','b']]
    return ExpressionPlan.model_validate({
        'plan_version':'1.0','indicator_name':'합성 비율과 전월 차이',
        'economic_definition':'a/b×100 및 당월 비율−전월 비율',
        'sources':sources,
        'nodes':[{'id':'ratio','operator':'percent_ratio','inputs':['a','b']},
                 {'id':'delta','operator':'difference','inputs':['ratio'],'lag':1}],
        'outputs':[{'node':'a','label':'분자','display':'table'},
                   {'node':'b','label':'분모','display':'table'},
                   {'node':'ratio','label':'비율','display':'both'},
                   {'node':'delta','label':'전월 비율과의 차이','display':'table'}],
        'start_period':'202101','end_period':'202102'})


def inputs(denominator=100.,unit='십억원'):
    return {'a':Series({'202012':20.,'202101':22.,'202102':18.},'십억원','M',['a']),
            'b':Series({'202012':100.,'202101':denominator,'202102':100.},unit,'M',['b'])}


def test_percent_ratio_and_point_change_use_preceding_month():
    p=plan();bounds=validate_plan(p,SimpleNamespace(validate_source_selection=lambda s:None))
    assert bounds['fetch_start']=='202012'
    computed=DynamicExpressionExecutor().execute(p,inputs())
    assert computed['series']['ratio'].unit=='%'
    assert computed['series']['delta'].unit=='%p'
    assert computed['series']['ratio'].values['202101']==pytest.approx(22.)
    assert computed['series']['delta'].values['202101']==pytest.approx(2.)
    assert computed['series']['delta'].values['202102']==pytest.approx(-4.)
    assert not any(r['PRD_DE']=='202012' for r in computed['rows'])


@pytest.mark.parametrize('denominator',[0.,None])
def test_missing_or_zero_denominator_does_not_produce_a_value(denominator):
    p=plan();p.outputs=p.outputs[:3]
    computed=DynamicExpressionExecutor().execute(p,inputs(denominator))
    assert computed['series']['ratio'].values['202101'] is None
    assert computed['series']['delta'].values['202102'] is None
    assert any(i['node']=='ratio' and i['period']=='202101' for i in computed['issues'])


@pytest.mark.parametrize('unit',['','백만원'])
def test_percentage_requires_verified_matching_scale(unit):
    with pytest.raises(ValueError,match='동일하고 확인된 단위'):
        DynamicExpressionExecutor().execute(plan(),inputs(unit=unit))


def test_relative_growth_guard_remains_and_plain_divide_keeps_times_unit():
    p=plan();p.nodes[0].operator='divide'
    computed=DynamicExpressionExecutor().execute(p,inputs())
    assert computed['series']['ratio'].unit=='배'
    assert computed['series']['ratio'].values['202101']==pytest.approx(.22)
    p=plan();p.nodes[1].operator='pct_change'
    with pytest.raises(ValueError,match='비율의 증가율'):
        DynamicExpressionExecutor().execute(p,inputs())


def test_percent_ratio_requires_two_inputs_and_no_unrelated_parameters():
    data=plan().model_dump();data['nodes'][0]['inputs']=['a']
    with pytest.raises(ValueError,match='입력 개수'):
        ExpressionPlan.model_validate(data)
    data=plan().model_dump();data['nodes'][0]['lag']=1
    with pytest.raises(ValueError,match='허용되지 않습니다'):
        ExpressionPlan.model_validate(data)
