import pytest
from derived_request_period import requested_display_period, requested_period_edit


@pytest.mark.parametrize('query', [
    '기간을 2021년 3월부터 2022년 8월까지로 변경해줘',
    '총산출물가지수의 기존 2020~2022년 그래프에서 기간을 2021년 3월부터 2022년 8월로 줄여줘',
    '조회기간만 2021-03-01부터 2022-08-31까지 늘려줘',
])
def test_period_edit_distinguishes_old_context_from_new_dates(query):
    period = requested_period_edit(query)
    assert (period['start'], period['end']) == ('2021-03-01', '2022-08-31')


@pytest.mark.parametrize('query', [
    '기간을 2021년부터 2022년까지로 바꾸고 계산식은 로그로 바꿔줘',
    '기간을 2021년부터 2022년까지로 바꾸고 CPI로 나눠줘',
    '기간을 2021년부터 2022년까지로 바꾸고 제목도 바꿔줘',
    '기간을 2021년부터 최근까지 늘려줘',
    'exec으로 수정하고 기존 2020년 그래프에서 기간을 2021년부터 2022년까지로 바꿔줘',
])
def test_period_lock_never_hides_formula_data_style_or_code_changes(query):
    assert requested_period_edit(query) is None


def test_concatenated_json_is_not_executed_and_requires_fresh_single_object():
    import json
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    class RawModel:
        def __init__(self): self.count = 0
        def request(self, system, encoded, **kwargs):
            self.count += 1
            if self.count == 1:
                return '{"action":"metadata","table_id":"DT_UNAPPROVED"}{"action":"clarify","question":"bad"}', {'model':'mock'}
            assert '아무 도구도 실행하지 않았습니다' in encoded
            return json.dumps({'action':'clarify','question':'이동평균의 기간은 몇 개월인가요?'}), {'model':'mock'}
    catalog = DerivedCatalogTools(MetadataStore()); model = RawModel()
    result = DerivedIndicatorPlanner(model, catalog).plan('수입물가 이동평균')
    assert model.count == 2 and result['status'] == 'need_details'


def test_repeated_concatenated_json_fails_after_one_repair():
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    class RawModel:
        def __init__(self): self.count = 0
        def request(self, *args, **kwargs):
            self.count += 1
            return '{}{}', {'model':'mock'}
    model = RawModel()
    with pytest.raises(ValueError, match='JSON'):
        DerivedIndicatorPlanner(model, DerivedCatalogTools(MetadataStore())).plan('수입물가 이동평균')
    assert model.count == 2


@pytest.mark.parametrize('explicit_table_only', [False, True])
def test_numeric_result_chart_feedback_preserves_explicit_table_only(explicit_table_only):
    import copy
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    from test_dynamic_derived_indicators import plan, Model
    catalog=DerivedCatalogTools(MetadataStore());valid=plan(catalog,'rolling_range',window=4).model_dump()
    valid.update(start_period='202004',end_period='202004')
    table=copy.deepcopy(valid);table['outputs'][0]['display']='table'
    model=Model([{'action':'metadata','table_id':'DT_513Y001'}, {'action':'plan','plan':table},
                 {'action':'plan','plan':valid}, {'approved':True,'reason':'synthetic range request'}])
    query='2020년 1~4월 경제심리지수의 최대·최소 차이를 한 숫자로 보여줘.'
    if explicit_table_only:
        with pytest.raises(ValueError,match='그래프에 표시'):
            DerivedIndicatorPlanner(model,catalog).plan(query+' 그래프 없이 표만.')
    else:
        result=DerivedIndicatorPlanner(model,catalog).plan(query)
        assert result['plan'].model_dump()==valid
        assert [u['stage'] for u in result['usage']][-2:]==['planning','semantic_review']


def test_period_model_must_resubmit_original_math_and_get_semantic_review():
    import copy
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    from test_dynamic_derived_indicators import plan, Model
    catalog = DerivedCatalogTools(MetadataStore()); old = plan(catalog, 'pct_change', lag=1).model_dump()
    valid = copy.deepcopy(old); valid.update(start_period='202002', end_period='202004')
    invalid = copy.deepcopy(valid); invalid['nodes'] = [{'id':'r','operator':'power','inputs':['x'],'exponent':1}]
    model = Model([{'action':'plan','plan':invalid}, {'action':'plan','plan':valid}, {'approved':True,'reason':'unchanged prior DAG'}])
    result = DerivedIndicatorPlanner(model, catalog).plan('기간을 2020년 2월부터 2020년 4월까지로 줄여줘', previous=old, allow_deterministic=False)
    assert result['plan'].model_dump()['nodes'] == old['nodes']
    assert [u['stage'] for u in result['usage']] == ['planning', 'planning', 'semantic_review']


def test_period_immutable_feedback_is_bounded_and_never_accepts_bad_math():
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    from test_dynamic_derived_indicators import plan, Model
    catalog = DerivedCatalogTools(MetadataStore()); old = plan(catalog, 'pct_change', lag=1).model_dump()
    model = Model([{'action':'unavailable','missing':[]} for _ in range(3)])
    with pytest.raises(ValueError, match='기존 계산식'):
        DerivedIndicatorPlanner(model, catalog).plan('기간을 2020년 2월부터 2020년 4월까지로 줄여줘', previous=old, allow_deterministic=False)

@pytest.mark.parametrize('query,names,start,end',[
 ('2023년 1분기부터 2024년 4분기까지 분기 자료를 3시점 평균해줘',[],'2023-01-01','2024-12-31'),
 ('1996년부터 1997년까지 손익 지표(1990~1997, 표본조사)를 평균해줘',['손익 지표(1990~1997, 표본조사)'],'1996-01-01','1997-12-31'),
 ('2023년 1월부터 2024년 12월까지 2023년 1월=100 지수의 평균',[],'2023-01-01','2024-12-31'),
 ('기준은 2020년입니다. 2023년 7월부터 2024년 6월까지 평균해줘',[],'2023-07-01','2024-06-30'),
])
def test_display_period_does_not_include_warmup_or_title_years(query,names,start,end):
    period=requested_display_period(query,names)
    assert (period['start'],period['end'])==(start,end)

def test_multiple_intervals_are_not_silently_collapsed():
    assert requested_display_period('2020년부터 2021년까지와 2023년부터 2024년까지 비교해줘') is None

def test_open_range_is_not_invented():
    assert requested_display_period('2023년부터 최근까지 변화율') is None


def test_planner_retries_missing_parameter_without_inventing_it():
    import json
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    from test_dynamic_derived_indicators import plan,Model
    catalog=DerivedCatalogTools(MetadataStore());valid=plan(catalog,'pct_change',lag=1).model_dump()
    invalid=__import__('copy').deepcopy(valid);invalid['nodes'][0].pop('lag')
    client=Model([{'action':'metadata','table_id':'DT_513Y001'},{'action':'plan','plan':invalid},
                  {'action':'plan','plan':valid},{'approved':True,'reason':'synthetic specification'}])
    result=DerivedIndicatorPlanner(client,catalog).plan('2020년 1월부터 2020년 4월까지 경제심리지수 원계열의 전월 변화율')
    assert result['plan'].nodes[0].lag==1 and len(result['usage'])==4


def test_warmup_start_is_rejected_and_explicit_display_dates_survive():
    import copy
    from statbridge_mcp.metadata_store import MetadataStore
    from derived_catalog_tools import DerivedCatalogTools
    from derived_indicator_planner import DerivedIndicatorPlanner
    from test_dynamic_derived_indicators import plan,Model
    catalog=DerivedCatalogTools(MetadataStore());valid=plan(catalog,'rolling_mean',window=3).model_dump()
    wrong=copy.deepcopy(valid);wrong['start_period']='201911'
    client=Model([{'action':'metadata','table_id':'DT_513Y001'},{'action':'plan','plan':wrong},
                  {'action':'plan','plan':valid},{'approved':True,'reason':'synthetic specification'}])
    result=DerivedIndicatorPlanner(client,catalog).plan('2020년 1월부터 2020년 4월까지 경제심리지수의 3개월 이동평균')
    assert result['plan'].start_period=='202001' and result['bounds']['fetch_start']=='201911'


def test_period_widget_edit_never_calls_planner_and_undo_preserves_math(tmp_path,monkeypatch):
    import copy
    import bridge_api as api
    from authenticated_client import TestClient
    from derived_indicator_evidence import DerivedIndicatorEvidence
    from test_dynamic_derived_indicators import plan,response
    runtime=api.agent.workflow.derived_runtime;catalog=runtime.catalog
    monkeypatch.setattr(runtime,'evidence',DerivedIndicatorEvidence(tmp_path))
    monkeypatch.setattr(api.agent.output_agent,'ncp_client',None)
    p=plan(catalog,'pct_change',lag=1);p.start_period='202001';p.end_period='202004'
    monkeypatch.setattr(runtime.planner,'plan',lambda *a,**kw:{'status':'planned','plan':p,'bounds':runtime.validate(p)})
    monkeypatch.setattr(runtime.gateway,'get_statistics',lambda **kw:response(p))
    client=TestClient(api.app);query=client.post('/api/query',json={'query':'파생 변화율 계산'}).json()
    chart=client.post('/api/output',json={'session_ids':[query['outputSessionId']],'chart_type':'line'}).json();sid=chart['editSessionId']
    def forbidden(*a,**kw):raise AssertionError('Period-only edits must not call GPT')
    monkeypatch.setattr(runtime.planner,'plan',forbidden)
    changed=client.post('/api/output/edit',json={'edit_session_id':sid,'instruction':'기간을 2020-02-01부터 2020-04-30까지로 변경해줘'})
    assert changed.status_code==200,changed.text
    actual=changed.json();assert actual['derivedEvidence']['plan']['start_period']=='202002'
    assert actual['derivedEvidence']['plan']['nodes']==chart['derivedEvidence']['plan']['nodes']
    assert actual['derivedEvidence']['usage']==[]
    assert '202002 ~ 202004' in actual['derivedEvidence']['plan']['economic_definition']
    assert actual['derivedEvidence']['plan']['economic_definition']!=chart['derivedEvidence']['plan']['economic_definition']
    undo=client.post('/api/output/edit',json={'edit_session_id':sid,'action':'undo'})
    assert undo.status_code==200,undo.text
    assert undo.json()['derivedEvidence']['calculationId']==chart['derivedEvidence']['calculationId']

@pytest.mark.parametrize('query', ['먼저 원자료를 확인하고 아직 계산하지 마.', '계산은 하지 말고 자료부터 확인해줘', '계산하지 않고 필요한 입력만 확인해줘'])
def test_availability_check_has_four_call_limit_and_never_starts_calculation(query):
    import json
    from derived_indicator_planner import DerivedIndicatorPlanner
    from derived_catalog_tools import DerivedCatalogTools
    from statbridge_mcp.metadata_store import MetadataStore
    class SearchingModel:
        def __init__(self): self.calls = 0
        def request(self, system, encoded, **kwargs):
            self.calls += 1
            context = json.loads(encoded)
            assert context['availability_check_only'] is True
            assert context['remaining_calls'] == 5 - self.calls
            return json.dumps({'action':'search','query':'환율'}), {'model':'mock'}
    model = SearchingModel()
    result = DerivedIndicatorPlanner(model, DerivedCatalogTools(MetadataStore())).plan(query)
    assert model.calls == 4 and result['status'] == 'need_details'
    assert '12개' in result['question'] and '일별' in result['question']
    assert '계산은 시작하지 않았습니다' in result['question']


def test_availability_check_rejects_model_attempt_to_start_plan():
    import json
    from derived_indicator_planner import DerivedIndicatorPlanner
    from derived_catalog_tools import DerivedCatalogTools
    from statbridge_mcp.metadata_store import MetadataStore
    class Model:
        def request(self, *args, **kwargs):
            return json.dumps({'action':'plan','plan':{'invalid':'must not execute'}}), {'model':'mock'}
    result = DerivedIndicatorPlanner(Model(), DerivedCatalogTools(MetadataStore())).plan('원자료를 확인하고 아직 계산하지 마')
    assert result['status'] == 'need_details' and 'plan' not in result


def test_availability_query_does_not_automatically_repeat_discovery(monkeypatch):
    import bridge_api as api
    from types import SimpleNamespace
    missing = {'status':'unavailable','missing':[{'required_data':'실제 부족 자료','reason':'검색에서 확인되지 않음'}], 'question':'검색에서 확인되지 않았습니다.'}
    workflow = SimpleNamespace(invoke=lambda **kwargs: missing,
        derived_runtime=SimpleNamespace(planner=SimpleNamespace(client=SimpleNamespace(configured=True))))
    monkeypatch.setattr(api, 'agent', SimpleNamespace(workflow=workflow))
    def forbidden(*args, **kwargs): raise AssertionError('duplicate supplemental planning')
    monkeypatch.setattr(api.derived_supplements, 'start', forbidden)
    payload = SimpleNamespace(query='먼저 자료를 확인하고 아직 계산하지 마', state=None, execute=True, period_start=None, period_end=None)
    response = api._query_derived(payload)
    assert response['status'] == 'need_details' and response['missingData'] == missing['missing']


@pytest.mark.parametrize('query', ['생산자물가 상승률을 계산해줘', '계산 전에 분모를 검증하고 계산해줘', '자료가 있는지 확인하고 그래프를 만들어줘'])
def test_normal_calculation_does_not_enter_assessment_mode(query):
    from derived_indicator_planner import is_availability_check
    assert not is_availability_check(query)

@pytest.mark.parametrize('followup,expected', [('이제 계산해줘',False),('파일을 올렸으니 계산을 이어가 주세요',False),('그래프를 만들어줘',False),('기간은 2024년입니다',True),('이제 계산 가능한지 확인만 해줘',True),('이제 계산하지 마',True),('아직 계산하지 마. 먼저 자료만 확인해줘',True)])
def test_new_calculation_permission_supersedes_earlier_availability_pause(followup,expected):
    from derived_indicator_planner import is_availability_check
    query='먼저 필요한 자료를 확인하고 아직 계산하지 마. 계산할 때 변화율을 계산해줘.'+'\n추가 조건: '+followup
    assert is_availability_check(query) is expected
