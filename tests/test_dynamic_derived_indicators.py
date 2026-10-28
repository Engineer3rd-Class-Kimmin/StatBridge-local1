"""Synthetic observations; no test number is an actual KOSIS observation."""
import copy
import json
from types import SimpleNamespace
import pytest
from expression_tree_schema import ExpressionPlan
from expression_tree_validator import validate_plan, DerivedValidationError, shift_period
from dynamic_expression_executor import DynamicExpressionExecutor, Series, source_series
from derived_catalog_tools import DerivedCatalogTools
from derived_indicator_planner import DerivedIndicatorPlanner
from derived_indicator_runtime import DerivedIndicatorRuntime
from derived_indicator_evidence import DerivedIndicatorEvidence
from statbridge_mcp.metadata_store import MetadataStore


@pytest.fixture(scope='module')
def catalog():
    return DerivedCatalogTools(MetadataStore())


def plan(catalog, op='pct_change', **params):
    meta = catalog.get_table_metadata('DT_513Y001')
    source = {'alias': 'x', 'table_id': meta['table_id'], 'item_id': meta['items'][0]['item_id'],
              'frequency': 'M', 'required_data': '경제심리지수',
              'classifications': {d['mcp_param']: d['values'][0]['class_id'] for d in meta['dimensions'] if d['values']}}
    return ExpressionPlan.model_validate({'plan_version': '1.0', 'indicator_name': '가상 관측 계산 테스트',
        'economic_definition': '합성 지수의 변화', 'sources': [source],
        'nodes': [{'id': 'calc', 'operator': op, 'inputs': ['x'], **params}],
        'outputs': [{'node': 'calc', 'label': '테스트 계산'}], 'start_period': '202001', 'end_period': '202004'})


def inputs(values=None, unit='지수'):
    return {'x': Series(values or {'202001': 100., '202002': 110., '202003': 121., '202004': 133.1}, unit, 'M', ['x'])}


@pytest.mark.parametrize('op,params,expected', [
    ('pct_change', {'lag': 1}, 10), ('difference', {'lag': 1}, 12.1),
    ('rolling_mean', {'window': 2}, 127.05), ('cumulative_sum', {}, 464.1),
    ('rebase_100', {'base_period': '202001'}, 133.1), ('lag', {'lag': 1}, 121),
    ('inverse', {}, 1/133.1), ('log', {}, __import__('math').log(133.1)), ('power', {'exponent': 2}, 133.1**2),
    ('power', {'exponent':0.5}, 133.1**0.5)])
def test_unary_arithmetic(catalog, op, params, expected):
    p = plan(catalog, op, **params)
    validate_plan(p, catalog)
    result = DynamicExpressionExecutor().execute(p, inputs())
    assert result['rows'][-1]['DT'] == pytest.approx(expected)


@pytest.mark.parametrize('op,expected', [('add',266.2),('subtract',0),('multiply',133.1**2),('divide',1)])
def test_binary(catalog, op, expected):
    p = plan(catalog, 'inverse')
    p.nodes[0] = p.nodes[0].model_copy(update={'operator': op, 'inputs': ['x', 'x']})
    assert DynamicExpressionExecutor().execute(p, inputs())['rows'][-1]['DT'] == pytest.approx(expected)


@pytest.mark.parametrize('frequency,previous,current', [('M','202001','202101'),('Q','202001','202101'),('S','202001','202101'),('Y','2020','2021')])
def test_yoy_calendar(catalog, frequency, previous, current):
    p = plan(catalog, 'yoy_pct')
    p.start_period = p.end_period = current
    result = DynamicExpressionExecutor().execute(p, {'x': Series({previous:100., current:110.}, '지수', frequency, ['x'])})
    assert result['rows'][0]['DT'] == pytest.approx(10)


def test_gaps_are_not_positional_lags(catalog):
    p = plan(catalog, 'pct_change', lag=1)
    result = DynamicExpressionExecutor().execute(p, inputs({'202001':100., '202003':120.,'202004':144.}))
    assert [r['PRD_DE'] for r in result['rows']] == ['202004']
    assert any(i['period'] == '202003' for i in result['issues'])


def test_zero_missing_and_domain(catalog):
    p = plan(catalog, 'inverse')
    result = DynamicExpressionExecutor().execute(p, inputs({'202001':0.,'202002':None,'202003':2.}))
    assert len(result['rows']) == 1
    assert result['issues'][0]['reason'] == 'zero_division'
    with pytest.raises(ValueError):
        DynamicExpressionExecutor().execute(plan(catalog, 'log'), inputs({'202001':-1.}))


def test_nested_warmup_and_dag(catalog):
    p = plan(catalog, 'yoy_pct')
    p.nodes.append(p.nodes[0].model_copy(update={'id':'smooth','operator':'rolling_mean','inputs':['calc'],'window':3}))
    p.outputs[0].node = 'smooth'
    bounds = validate_plan(p, catalog)
    assert bounds['fetch_start'] == '201811'
    p.nodes[0].inputs = ['smooth']
    with pytest.raises(ValueError, match='순환'):
        validate_plan(p, catalog)


@pytest.mark.parametrize('field,value', [('table_id','DT_EXTERNAL'),('item_id','INVENTED'),('frequency','Q'),('classifications',{'objL1':'INVALID'})])
def test_invalid_source(catalog, field, value):
    p = plan(catalog, 'inverse')
    setattr(p.sources[0],field,value)
    with pytest.raises(ValueError): validate_plan(p,catalog)


def test_schema_rejects_code_and_wrong_parameters(catalog):
    raw = plan(catalog,'inverse').model_dump()
    for patch in ({'operator':'exec'},{'code':'import os'},{'lag':1}):
        altered=copy.deepcopy(raw);altered['nodes'][0].update(patch)
        with pytest.raises(ValueError):ExpressionPlan.model_validate(altered)


def test_depth_and_unit_limits(catalog):
    p=plan(catalog,'inverse')
    for index in range(13):
        p.nodes.append(p.nodes[0].model_copy(update={'id':f'n{index}', 'inputs':[p.nodes[-1].id]}))
    with pytest.raises(ValueError, match='깊이'):validate_plan(p,catalog)
    p=plan(catalog,'inverse');p.nodes[0].operator='add';p.nodes[0].inputs=['x','y']
    with pytest.raises(ValueError,match='단위'):
        DynamicExpressionExecutor().execute(p,{**inputs(),'y':Series({'202001':1.},'원','M',['y'])})


def response(p):
    s=p.sources[0]
    return {'status':'success','source':'kosis_api','table_id':s.table_id,'rows':[
        {'ORG_ID':'301','TBL_ID':s.table_id,'ITM_ID':s.item_id,'PRD_SE':'M','PRD_DE':period,
         'UNIT_NM':'지수','DT':value, **{key.replace('objL','C'):v for key,v in s.classifications.items()}}
        for period,value in inputs()['x'].values.items()]}


def test_source_response_integrity(catalog):
    p=plan(catalog,'inverse');r=response(p)
    assert len(source_series(p.sources[0],r).values)==4
    for field,value in [('source','local_csv'),('table_id','outside'),('status','failed')]:
        bad=copy.deepcopy(r);bad[field]=value
        with pytest.raises(ValueError):source_series(p.sources[0],bad)
    r['rows'].append(r['rows'][0])
    with pytest.raises(ValueError,match='중복'):source_series(p.sources[0],r)


class Model:
    def __init__(self, actions):self.actions=iter(actions)
    def request(self,*args,**kwargs):return json.dumps(next(self.actions),ensure_ascii=False),{'model':'mock','usage':{'input_tokens':1}}


def test_missing_data_has_search_evidence(catalog):
    planner=DerivedIndicatorPlanner(Model([{'action':'search','query':'소비자물가'},
        {'action':'unavailable','missing':[{'required_data':'소비자물가지수','reason':'승인 카탈로그 검색에서 확인되지 않음'}]}]),catalog)
    result=planner.plan('소비자 구매력 변화율')
    assert result['status']=='unavailable'
    assert '소비자물가지수' in result['question'] and result['searches']
    assert '전체 API의 자료 부재' in result['question']


def test_planner_catalog_tools_and_review(catalog):
    p=plan(catalog,'inverse')
    planner=DerivedIndicatorPlanner(Model([{'action':'search','query':'경제심리지수'},
        {'action':'metadata','table_id':'DT_513Y001'},{'action':'plan','plan':p.model_dump()},
        {'approved':True,'reason':'합성 테스트'}]),catalog)
    assert planner.plan('지수 역수')['status']=='planned'


def test_metadata_refresh_accepts_server_verified_source_without_search_hit(catalog):
    p=plan(catalog,'inverse')
    planner=DerivedIndicatorPlanner(Model([
        {'action':'metadata','table_id':p.sources[0].table_id},
        {'action':'plan','plan':p.model_dump()}, {'approved':True,'reason':'검증된 원자료'}]),catalog)
    assert planner.plan('역수 계산',verified_sources=[p.sources[0].model_dump()])['status']=='planned'


def test_metadata_refresh_still_rejects_unseen_table(catalog):
    planner=DerivedIndicatorPlanner(Model([{'action':'metadata','table_id':'DT_UNSEEN'}]),catalog)
    with pytest.raises(DerivedValidationError,match='검색 결과에 없는'):
        planner.plan('역수 계산')


def test_planning_and_review_receive_server_metadata_and_verified_units(catalog):
    p=plan(catalog,'inverse');contexts=[]
    class Capture(Model):
        def request(self,*args,**kwargs):
            contexts.append(json.loads(args[1]));return super().request(*args,**kwargs)
    units=[{**{k:v for k,v in p.sources[0].model_dump().items() if k in ('table_id','item_id','classifications','frequency')},
            'unit':'지수','origin':'validated_KOSIS_observation_response'}]
    planner=DerivedIndicatorPlanner(Capture([{'action':'plan','plan':p.model_dump()},
        {'approved':True,'reason':'실제 메타데이터 일치'}]),catalog)
    assert planner.plan('역수 계산',verified_sources=[p.sources[0].model_dump()],verified_source_units=units)['status']=='planned'
    assert all(c['server_verified_observation_units']==units for c in contexts)
    description=contexts[-1]['source_descriptions'][0]
    assert description['table_id']==p.sources[0].table_id
    assert description['item_id']==p.sources[0].item_id
    assert description['origin']=='server_validated_catalog_selection'


def test_preflight_forwards_only_units_observed_for_exact_selection(catalog,tmp_path,monkeypatch):
    p=plan(catalog,'inverse')
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store)),Model([]),tmp_path)
    runtime.api_inventory=object();captured={}
    source={'status':'available','selection':p.sources[0].model_dump(),
            'observationChecks':[{'status':'available','unit':'지수'}]}
    monkeypatch.setattr('derived_availability.check_availability',lambda *a,**kw:{'availability':{'status':'checked','sources':[source],'questions':[]}})
    def capture(*args,**kwargs):captured.update(kwargs);return {'status':'planned'}
    monkeypatch.setattr(runtime.planner,'plan',capture)
    assert runtime._plan_uncached('두 지수의 차이 계산')['status']=='planned'
    observed=captured['verified_source_units'][0]
    assert observed['table_id']==p.sources[0].table_id and observed['unit']=='지수'
    source['observationChecks'].append({'status':'available','unit':'다른 단위'})
    runtime._plan_uncached('두 지수의 차이 계산')
    assert captured['verified_source_units']==[]


def test_edit_reuses_only_revalidated_prior_source_ids(catalog):
    p=plan(catalog,'pct_change',lag=1)
    amended=copy.deepcopy(p)
    amended.nodes.append(p.nodes[0].model_copy(update={'id':'ma3','operator':'rolling_mean','inputs':['calc'],'lag':None,'window':3}))
    amended.outputs[0].node='ma3'
    planner=DerivedIndicatorPlanner(Model([{'action':'plan','plan':amended.model_dump()},
        {'approved':True,'reason':'기존 상승률의 평균'}]),catalog)
    result=planner.plan('기존 상승률의 3개월 이동평균',previous=p.model_dump())
    assert result['status']=='planned' and len(result['usage'])==2


def test_invalid_model_plan_keeps_usage_trace(catalog):
    p=plan(catalog,'inverse')
    planner=DerivedIndicatorPlanner(Model([{'action':'plan','plan':p.model_dump()}]),catalog)
    with pytest.raises(DerivedValidationError) as caught:
        planner.plan('역수 계산')
    assert caught.value.evidence['attempts']==1 and caught.value.evidence['usage']


def test_runtime_immutable_evidence_and_subtree_reuse(catalog,tmp_path):
    p=plan(catalog,'pct_change',lag=2)
    gateway=SimpleNamespace(service=SimpleNamespace(store=catalog.store), get_statistics=lambda **kwargs:response(p))
    runtime=DerivedIndicatorRuntime(gateway,Model([]),tmp_path)
    planning={'plan':p,'bounds':runtime.validate(p),'semantic_review':{'approved':True}}
    result=runtime.calculate('가상 테스트',planning,runtime.fetch(p,planning['bounds']))
    original=copy.deepcopy(result)
    revised=runtime.revise(result,node_patch={'id':'calc','lag':1})
    assert result==original
    assert revised['execution']['rows'][0]['DT']==pytest.approx(10)
    assert len(list(tmp_path.glob('*.json')))==2
    files=[json.loads(f.read_text(encoding='utf-8')) for f in tmp_path.glob('*.json')]
    assert any(f['evidence'].get('previous')==result['derivedEvidence']['calculationId'] for f in files)


def test_search_excludes_catalog_periods_outside_request(catalog):
    found=catalog.search_approved_tables('2023년부터 2024년까지 생산자물가 총지수')
    assert any(r['table_id']=='DT_404Y014' for r in found)
    assert not any('1910' in r['table_name'] for r in found)


def test_rebase_of_yoy_fetches_history_before_base(catalog):
    p=plan(catalog,'yoy_pct')
    p.nodes.append(p.nodes[0].model_copy(update={'id':'base','operator':'rebase_100','inputs':['calc'],'base_period':'201801'}))
    assert validate_plan(p,catalog)['fetch_start']=='201701'


def test_partial_sources_block_before_arithmetic(catalog,tmp_path):
    p=plan(catalog,'inverse')
    p.sources.append(p.sources[0].model_copy(update={'alias':'y','classifications':{
        'objL1':catalog.get_table_metadata('DT_513Y001')['dimensions'][0]['values'][1]['class_id']}}))
    calls=[]
    def get(**kwargs):
        calls.append(kwargs)
        return response(p) if len(calls)==1 else {'status':'failed','source':'none','table_id':'DT_513Y001'}
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store),get_statistics=get),Model([]),tmp_path)
    with pytest.raises(ValueError,match='조회에 실패'):
        runtime.fetch(p,runtime.validate(p))
    assert not list(tmp_path.glob('*.json'))


def test_partial_batch_is_not_a_computed_result(catalog,tmp_path):
    p=plan(catalog,'inverse')
    p.sources.append(p.sources[0].model_copy(update={'alias':'y','classifications':{
        'objL1':catalog.get_table_metadata('DT_513Y001')['dimensions'][0]['values'][1]['class_id']}}))
    gateway=SimpleNamespace(service=SimpleNamespace(store=catalog.store),get_statistics_batch=lambda *a,**kw:{
        'status':'partial','results':[{'request_id':'x','table_id':p.sources[0].table_id,'status':'success','data':response(p)},
                                    {'request_id':'y','table_id':p.sources[1].table_id,'status':'error'}]})
    runtime=DerivedIndicatorRuntime(gateway,Model([]),tmp_path)
    with pytest.raises(ValueError,match='일부 자료만으로'):
        runtime.fetch(p,runtime.validate(p))


def test_planner_rejects_semantic_substitution(catalog):
    p=plan(catalog,'inverse')
    planner=DerivedIndicatorPlanner(Model([{'action':'search','query':'경제심리지수'},
        {'action':'metadata','table_id':'DT_513Y001'},{'action':'plan','plan':p.model_dump()},
        {'approved':False,'reason':'소비자 구매력에 경제심리지수를 사용할 수 없습니다.'}]),catalog)
    assert planner.plan('국내 소비자 구매력')['status']=='need_details'


def test_provider_failure_never_becomes_missing_data(catalog,tmp_path):
    from langgraph_workflow import StatBridgeWorkflow
    def failed(*args,**kwargs):raise ValueError('OpenAI 연결에 실패했습니다.')
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store)),Model([]),tmp_path)
    runtime.plan=failed
    workflow=StatBridgeWorkflow(SimpleNamespace())
    workflow.derived_runtime=runtime
    result=workflow.invoke(query='파생지표 계산')
    assert result['status']=='data_unavailable'
    assert '연결' in result['question'] and not result.get('missing')
    resumed=workflow.invoke(query='월별로 진행해줘',conversation_state={'_derived_query':'소비자 구매력 계산',
        '_derived_start':'2023-01-01','_derived_end':'2024-12-31'})
    assert '소비자 구매력' in resumed['state']['_derived_query']
    assert resumed['state']['_derived_start']=='2023-01-01'


def test_source_nonfinite_and_classification_integrity(catalog):
    p=plan(catalog,'inverse')
    for key,value in [('DT','NaN'),('C1','outside'),('ITM_ID','outside'),('PRD_SE','Q')]:
        bad=response(p);bad['rows'][0][key]=value
        with pytest.raises(ValueError):source_series(p.sources[0],bad)


def test_expanding_history_fetches_only_missing_interval(catalog,tmp_path):
    p=plan(catalog,'pct_change',lag=1)
    calls=[]
    def get(**kwargs):
        calls.append(kwargs)
        result=response(p)
        template=result['rows'][0]
        result['rows']=[{**template,'PRD_DE':period,'DT':100.+i} for i,period in enumerate(
            ['201910','201911','201912','202001','202002','202003','202004'])
            if kwargs['start_period']<=period<=kwargs['end_period']]
        return result
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store),get_statistics=get),Model([]),tmp_path)
    planning={'plan':p,'bounds':runtime.validate(p),'semantic_review':{'approved':True}}
    first=runtime.calculate('가상 테스트',planning,runtime.fetch(p,planning['bounds']))
    changed=runtime.revise(first,node_patch={'id':'calc','lag':2})
    assert len(changed['execution']['rows'])==4
    assert [(c['start_period'],c['end_period']) for c in calls]==[('201912','202004'),('201911','201911')]


def test_mixed_frequency_and_memory_work_block(catalog):
    p=plan(catalog,'inverse')
    p.sources.append(p.sources[0].model_copy(update={'alias':'y','frequency':'Y'}))
    with pytest.raises(ValueError,match='주기'):validate_plan(p,catalog)
    p=plan(catalog,'rolling_mean',window=120)
    p.start_period='190101';p.end_period='219912'
    for index in range(10):
        p.nodes.append(p.nodes[0].model_copy(update={'id':f'roll{index}','inputs':[p.nodes[-1].id]}))
    with pytest.raises(ValueError,match='한도'):validate_plan(p,catalog)


@pytest.mark.parametrize('instruction,expected',[
    ('제목을 생산자물가 상승률로 바꿔줘',False),
    ('2023년부터 2024년까지 음영으로 강조해줘',False),
    ('막대그래프로 바꿔줘',False),('빨간색으로 바꿔줘',False),
    ('기간을 2023-01-01부터 2024-12-31까지로 변경해줘',True),
    ('수입물가지수도 비교 항목으로 추가해줘',True),('시차 3개월로 계산해줘',True)])
def test_calculation_edit_routing(instruction,expected):
    from derived_intent_router import is_calculation_edit
    assert is_calculation_edit(instruction) is expected


def test_api_output_edit_failure_and_restore(catalog,tmp_path,monkeypatch):
    import bridge_api as api
    from authenticated_client import TestClient
    runtime=api.agent.workflow.derived_runtime
    monkeypatch.setattr(api.agent.output_agent, 'ncp_client', None)
    p=plan(catalog,'pct_change',lag=2)
    monkeypatch.setattr(runtime,'evidence',DerivedIndicatorEvidence(tmp_path))
    monkeypatch.setattr(runtime.planner,'plan',lambda *a,**kw:{'status':'planned','plan':p,'bounds':runtime.validate(p)})
    calls=[]
    def fetch(**kwargs):calls.append(kwargs);return response(p)
    monkeypatch.setattr(runtime.gateway,'get_statistics',fetch)
    client=TestClient(api.app)
    r=client.post('/api/query',json={'query':'파생 변화율 계산 후 선그래프로 보여줘'});assert r.status_code==200,r.text
    data=r.json();assert data['status']=='need_output_config',data
    r=client.post('/api/output',json={'session_ids':[data['outputSessionId']],'chart_type':'line'})
    assert r.status_code==200,r.text
    chart=r.json();assert chart['analysisKind']=='derived' and chart['chart'][0]['points'][-1]['value']==pytest.approx(21)
    sid=chart['editSessionId']
    assert api.EDIT_SESSIONS[sid]['output']['chartState']['chart_type']=='line'
    r=client.post('/api/output/edit',json={'edit_session_id':sid,'derived_node_patch':{'id':'calc','lag':1},
        'visual':{'marks':[],'selected_target':'전체 그래프'}})
    assert r.status_code==200,r.text
    assert len(calls)==1 and calls[0]['prefer_local'] is False and calls[0]['allow_fallback'] is False
    before=copy.deepcopy(api.EDIT_SESSIONS[sid]['output'])
    r=client.post('/api/output/edit',json={'edit_session_id':sid,'derived_node_patch':{'id':'calc','operator':'exec'}})
    assert r.status_code==422 and api.EDIT_SESSIONS[sid]['output']==before
    r=client.post('/api/output/edit',json={'edit_session_id':sid,'restore_initial':True})
    assert r.status_code==200,r.text
    assert r.json()['derivedEvidence']['calculationId']==chart['derivedEvidence']['calculationId']
    assert client.post('/api/output/derived-data',json={'edit_session_id':sid}).json()['sources'][0]['total']==4


def test_recalculated_title_and_series_styles_follow_new_output(catalog):
    import bridge_api as api
    from output_schema import ChartSpec
    p=plan(catalog,'inverse').model_dump()
    updated=copy.deepcopy(p);updated['indicator_name']='새 계산';updated['outputs'][0]={'node':'new_calc','label':'새 결과'}
    old={'derivedEvidence':{'plan':p}};new={'derivedEvidence':{'plan':updated}}
    current={'chartState':ChartSpec(title=p['indicator_name'],series_colors={'테스트 계산':'#ff0000'}).model_dump()}
    spec=api._derived_output_spec(old,new,current)
    assert spec.title=='새 계산' and spec.series_colors=={'새 결과':'#ff0000'}
