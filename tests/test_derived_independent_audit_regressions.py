"""Reproductions of audit defects, all observations synthetic."""
import copy
import json
from types import SimpleNamespace
import pytest
from test_dynamic_derived_indicators import catalog,plan,inputs,response,Model
from derived_indicator_planner import DerivedIndicatorPlanner
from derived_indicator_runtime import DerivedIndicatorRuntime,validate_derived_output_row
from dynamic_expression_executor import DynamicExpressionExecutor,source_series
from expression_tree_validator import result_unit


def test_missing_first_period_cannot_start_partial_cumulative(catalog):
    with pytest.raises(ValueError,match='계산 가능한 관측값'):
        DynamicExpressionExecutor().execute(plan(catalog,'cumulative_sum'),inputs({'202002':10.,'202003':20.}))


def test_trailing_periods_are_missing_evidence(catalog):
    result=DynamicExpressionExecutor().execute(plan(catalog,'inverse'),inputs({'202001':10.}))
    assert {i['period'] for i in result['issues']}=={'202002','202003','202004'}


@pytest.mark.parametrize('unit',['100원','100달러','foo100','원(2020=100)','2025.13=100','2025.00=100'])
def test_currency_is_not_dimensionless(unit):
    with pytest.raises(ValueError):result_unit('log',[unit])


@pytest.mark.parametrize('unit',['2020=100','2020＝100','(2020=100)','2025.12＝100','지수(기준=100)','배'])
def test_known_index_logs(unit):assert result_unit('log',[unit])=='로그'


@pytest.mark.parametrize('op',['subtract','difference'])
def test_rebased_index_difference_has_point_unit(op):
    assert result_unit(op,['지수(기준=100)']*(2 if op=='subtract' else 1))=='포인트'


@pytest.mark.parametrize('action',[[],42,None])
def test_nonobject_model_response_is_recoverable(catalog,action):
    with pytest.raises(ValueError,match='객체'):
        DerivedIndicatorPlanner(Model([action]),catalog).plan('경제심리지수 역수 2020년 계산')


def test_missing_source_frequency_is_rejected(catalog):
    p=plan(catalog,'inverse');data=response(p)
    for row in data['rows']:row.pop('PRD_SE',None)
    with pytest.raises(ValueError,match='주기'):source_series(p.sources[0],data)


@pytest.mark.parametrize('boundary',['fetch','calculate'])
def test_runtime_validates_before_io_or_evidence(catalog,tmp_path,boundary):
    calls=[];p=plan(catalog,'inverse');p.sources[0].table_id='DT_UNAPPROVED'
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store),get_statistics=lambda **kw:calls.append(kw)),Model([]),tmp_path)
    with pytest.raises(ValueError):
        if boundary=='fetch':runtime.fetch(p,{'fetch_start':'202001','fetch_end':'202004'})
        else:runtime.calculate('invalid',{'plan':p,'bounds':{}},{})
    assert calls==[] and list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('field,value',[('_FREQUENCY','Y'),('_SERIES_LABEL','forged'),('ITM_NM','forged')])
def test_output_labels_and_frequency_bound_to_evidence(catalog,tmp_path,field,value):
    p=plan(catalog,'inverse')
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store)),Model([]),tmp_path)
    calculated=runtime.calculate('synthetic',{'plan':p,'bounds':runtime.validate(p)},{'x':response(p)})
    row=copy.deepcopy(calculated['execution']['rows'][0]);row[field]=value
    with pytest.raises(ValueError):validate_derived_output_row(calculated,row,{p.sources[0].table_id})


def test_review_receives_server_warmup_and_object_required(catalog):
    p=plan(catalog,'rolling_mean',window=3);messages=[]
    actions=iter([{'action':'search','query':'경제심리지수'},{'action':'metadata','table_id':'DT_513Y001'},
                  {'action':'plan','plan':p.model_dump()},[]])
    class Client:
        def request(self,system,user,**kw):messages.append(json.loads(user));return json.dumps(next(actions)),{}
    with pytest.raises(ValueError,match='의미 검토.*객체'):DerivedIndicatorPlanner(Client(),catalog).plan('경제심리지수 이동평균')
    assert messages[-1]['validated_bounds']['fetch_start']=='201911'


def test_source_failure_kinds(catalog):
    p=plan(catalog,'inverse')
    for payload,kind in [({'status':'failed'},'source_api_error'),
                         ({'status':'success','source':'kosis_api','table_id':p.sources[0].table_id,'rows':[]},'source_data_absent')]:
        with pytest.raises(ValueError) as caught:source_series(p.sources[0],payload)
        assert caught.value.failure_kind==kind


def test_missing_unit_has_precise_source_failure(catalog):
    p=plan(catalog,'inverse')
    # A generic unverified source must still fail closed. The exact official
    # ESI selection now has a separately tested, source-backed unit rule.
    p.sources[0].table_id='DT_SYNTHETIC_UNVERIFIED'
    data=response(p)
    for row in data['rows']:row['UNIT_NM']=''
    with pytest.raises(ValueError,match='단위') as caught:source_series(p.sources[0],data)
    assert caught.value.failure_kind=='source_metadata_incomplete'


@pytest.mark.parametrize('kind',['source_metadata_incomplete','source_api_error','validation_error'])
def test_source_failure_does_not_replan_or_offer_unverified_upload(monkeypatch,kind):
    import bridge_api as api
    context={'_derived_query':'합성 지수 증감률','_derived_start':'2024-01-01'}
    calls=[]
    def invoke(**kwargs):
        calls.append(kwargs)
        return {'status':'data_unavailable','failure_stage':'execute_derived_sources',
                'failure_kind':kind,'question':'합성 원자료 단위를 확인해 주세요.','state':context}
    monkeypatch.setattr(api.agent.workflow,'invoke',invoke)
    def forbidden(*args,**kwargs):raise AssertionError('source failure must not trigger another planner')
    monkeypatch.setattr(api.derived_supplements,'start',forbidden)
    result=api._query_derived(api.QueryRequest(query='합성 지수 증감률'))
    assert len(calls)==1 and result['state']==context
    assert result['status']=='data_unavailable' and result['failure_kind']==kind
    assert '단위' in result['summary'] and 'supplement' not in result
    assert result['recovery']['choices'][0]['id']=='retry'


def test_economic_formula_ambiguity_requests_conditions_not_missing_data(tmp_path):
    from langgraph_workflow import StatBridgeWorkflow
    from expression_tree_validator import DerivedValidationError
    workflow=object.__new__(StatBridgeWorkflow)
    records=[]
    workflow.derived_runtime=SimpleNamespace(catalog=SimpleNamespace(version='synthetic'),
                                           evidence=SimpleNamespace(save=records.append))
    result=workflow._derived_failure({'query':'합성 비율의 증감률'},'calculate_derived_indicator',
                                    DerivedValidationError('상대 변화율인지 차이(%p)인지 확인해 주세요.'))['result']
    assert result['status']=='need_details' and '차이' in result['question']
    assert records[0]['failure_kind']=='validation_error'


def test_kosis_annual_frequency_alias(catalog):
    p=plan(catalog,'inverse');p.sources[0].frequency='Y';data=response(p)
    data['rows']=data['rows'][:1];data['rows'][0].update(PRD_SE='A',PRD_DE='2020')
    assert source_series(p.sources[0],data).frequency=='Y'


def test_real_model_evaluation_does_not_count_deterministic_clarification(catalog):
    native=DerivedIndicatorPlanner(Model([]),catalog).plan('통화가치 변화율을 보여줘')
    assert native['status']=='need_details' and native['usage']==[]
    actual=DerivedIndicatorPlanner(Model([{'action':'clarify','question':'국내 구매력과 외화 대비 가치 중 무엇인가요?'}]),catalog).plan(
        '통화가치 변화율을 보여줘',allow_deterministic=False)
    assert actual['status']=='need_details' and len(actual['usage'])==1
