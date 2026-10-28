"""Synthetic contracts; these are not reported as live KOSIS tests."""
import copy,json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from derived_api_inventory import DerivedApiInventory
from derived_availability import check_availability
from expression_tree_validator import DerivedValidationError


def requirement(term='환율'):
    return {'name':'일본 JPY 대미달러 환율','search_terms':[term],
        'periods':[{'label':'2기','start':'20241105','end':'20250224','frequency':'D'}],
        'unit':'JPY/USD 또는 USD/JPY (실제 호가 방향 확인 필요)','quotation':'실제 원자료 단위를 확인해 주세요.'}


class Client:
    supports_json_schema=True
    def __init__(self,rows):self.rows=list(rows);self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs));return json.dumps(self.rows.pop(0),ensure_ascii=False),{'model':'fixture'}


def runtime(req=None,meta=None,selection=None,response=None):
    meta=meta or {'table_id':'DT_FIXTURE','table_name':'국가별 결제통화','items':[{'item_id':'I','item_name':'비중'}],
        'dimensions':[],'frequencies':['M'],'periods':[{'frequency':'M','start_period':'202001','end_period':'202512'}]}
    snap={'origin':'actual_KOSIS_metadata_API','checked_at':'2026-10-19T00:00:00+00:00','sha256':'fixture','api_requests':1047,
          'tables':[meta]+[dict(meta,table_id=f'DT_FIXTURE{i}') for i in range(348)]}
    # Distinct irrelevant tables avoid multiplying semantic candidates.
    for m in snap['tables'][1:]:m.update(table_name='무관 통계',items=[],dimensions=[])
    rows=[{'inputs':[req or requirement()],'questions':['1기 월평균인가요?', '평균에 DXY와 중국을 포함하나요?']}]
    if selection is not None:rows.append(selection)
    client=Client(rows);calls=[]
    def fetch(**kwargs):calls.append(kwargs);return response
    cat=SimpleNamespace(approved=set(m['table_id'] for m in snap['tables']),validate_source_selection=lambda s:meta)
    return SimpleNamespace(api_inventory=SimpleNamespace(ensure=lambda:snap),catalog=cat,
        planner=SimpleNamespace(client=client),gateway=SimpleNamespace(get_statistics=fetch),
        input_series=lambda s,r:SimpleNamespace(values=r['values'],unit=r['unit']),calls=calls)


def test_full_live_inventory_negative_is_separate_from_questions_and_upload():
    r=runtime();result=check_availability(r,'아직 계산하지 마')
    assert result['status']=='need_details'
    a=result['availability'];assert a['checkedTables']==349 and a['calculationExecuted'] is False
    assert len(a['questions'])==2 and a['sources'][0]['status']=='not_found'
    assert 'federalreserve.gov' in str(a['sources'][0]['guidance'])
    assert 'JPY/USD' in str(a['sources'][0]['guidance'])
    assert not r.calls and len(r.planner.client.calls)==1 and 'supplement' not in result


def test_preflight_sees_explicit_table_definitions_before_asking_questions():
    r=runtime()
    check_availability(r,'국가별 결제통화를 확인해줘')
    context=json.loads(r.planner.client.calls[0][0][1])
    assert [m['table_id'] for m in context['explicit_table_metadata']]==['DT_FIXTURE']
    assert context['explicit_table_metadata'][0]['frequencies']==['M']


def test_repeated_negative_check_reuses_same_snapshot_without_model_calls():
    r=runtime();first=check_availability(r,'일별 환율 확인');second=check_availability(r,'일별 환율 확인')
    assert len(r.planner.client.calls)==1 and second['usage']==[] and second['cache_hit']
    assert second['availability']['sources']==first['availability']['sources']
    second['availability']['sources'].clear()
    assert check_availability(r,'일별 환율 확인')['availability']['sources']


def test_inventory_pending_does_not_call_gpt_or_report_missing_data():
    r=runtime();r.api_inventory=SimpleNamespace(ensure=lambda:None,progress=37,error=None)
    result=check_availability(r,'확인만')
    assert result['availability']['status']=='verifying' and result['availability']['sources']==[]
    assert not r.planner.client.calls


def test_inventory_api_failure_is_not_absence():
    r=runtime();r.api_inventory=SimpleNamespace(ensure=lambda:None,progress=37,error='TimeoutError')
    assert check_availability(r,'확인만')['availability']['status']=='verification_failed'


def test_metadata_candidate_is_not_automatically_available():
    req=requirement('결제통화');req['periods']=[{'label':'조회','start':'202301','end':'202302','frequency':'M'}]
    r=runtime(req,selection={'table_id':'','item_id':'','classifications':[],'reason':'비중은 환율이 아닙니다.'})
    result=check_availability(r,'확인만')
    assert result['availability']['sources'][0]['status']=='not_found' and not r.calls
    r=runtime(req,selection={'table_id':'','item_id':'','classifications':[],'reason':'선택 미확정'})
    r.catalog.bounded_metadata=lambda tid,q:{**r.api_inventory.ensure()['tables'][0],'total_items':99}
    result=check_availability(r,'확인만')
    assert result['availability']['sources'][0]['status']=='selection_required' and not r.calls


def test_external_id_is_rejected_before_observation_io():
    req=requirement('결제통화');req['periods']=[{'label':'조회','start':'202301','end':'202302','frequency':'M'}]
    r=runtime(req,selection={'table_id':'DT_OUTSIDE','item_id':'I','classifications':[],'reason':'x'})
    with pytest.raises(DerivedValidationError,match='후보에 없는'):check_availability(r,'확인')
    assert not r.calls


def test_positive_selection_requires_actual_observations():
    req=requirement('생산자');req['periods']=[{'label':'조회','start':'202301','end':'202302','frequency':'M'}]
    meta={'table_id':'DT_FIXTURE','table_name':'생산자물가','items':[{'item_id':'I','item_name':'총지수'}],
        'dimensions':[],'frequencies':['M'],'periods':[{'frequency':'M','start_period':'202001','end_period':'202512'}]}
    r=runtime(req,meta,{'table_id':'DT_FIXTURE','item_id':'I','classifications':[],'reason':'정확한 총지수'},
        {'values':{'202301':100,'202302':101},'unit':'2020=100'})
    a=check_availability(r,'확인')['availability']['sources'][0]
    assert a['status']=='available' and a['observationChecks'][0]['observations']==2
    assert len(r.calls)==1 and r.calls[0]['allow_fallback'] is False and r.calls[0]['prefer_local'] is False


def test_invalid_period_and_unsupported_frequency_do_not_fetch():
    r=runtime(requirement('결제통화'),selection={'table_id':'DT_FIXTURE','item_id':'I','classifications':[],'reason':'fixture'})
    a=check_availability(r,'확인')['availability']['sources'][0]
    assert a['status']=='not_found' and 'PRD API' in a['reason'] and not r.calls


def test_positive_api_timeout_does_not_become_missing_file_request():
    req=requirement('결제통화');req['periods']=[{'label':'조회','start':'202301','end':'202302','frequency':'M'}]
    r=runtime(req,selection={'table_id':'DT_FIXTURE','item_id':'I','classifications':[],'reason':'fixture'})
    r.gateway.get_statistics=lambda **kw:(_ for _ in ()).throw(TimeoutError())
    a=check_availability(r,'확인')['availability']['sources'][0]
    assert a['status']=='api_error' and a['guidance'] is None
    r=runtime(req,selection={'table_id':'DT_FIXTURE','item_id':'I','classifications':[],'reason':'fixture'})
    r.gateway.get_statistics=lambda **kw:(_ for _ in ()).throw(DerivedValidationError('원자료 단위를 확정할 수 없습니다.'))
    a=check_availability(r,'확인')['availability']['sources'][0]
    assert a['status']=='selection_required' and a['observationChecks'][0]['status']=='validation_required'


def test_dxy_guidance_is_ice_not_trade_weighted_substitute():
    from derived_input_guidance import input_guidance
    g=input_guidance({'required_data':'ICE DXY 달러인덱스'})
    assert len(g['links'])==1 and 'ice.com/' in g['links'][0]['url']
    assert '무료' in g['description'] and '대체하지' in g['description']


def test_snapshot_rejects_stale_incomplete_or_local_metadata(tmp_path):
    cat=SimpleNamespace(store=SimpleNamespace(table_ids=lambda:['A','B']))
    inv=DerivedApiInventory(cat,tmp_path)
    data={'origin':'actual_KOSIS_metadata_API','checked_epoch':time.time(),'tables':[{'table_id':'A','metadata_origin':'actual_KOSIS_metadata_API'}]}
    inv.path.write_text(json.dumps(data),encoding='utf8');assert inv.snapshot() is None
    data['tables'].append({'table_id':'B','metadata_origin':'local'})
    inv.path.write_text(json.dumps(data),encoding='utf8');assert inv.snapshot() is None
    data['tables'][1]['metadata_origin']='actual_KOSIS_metadata_API';data['checked_epoch']=0
    inv.path.write_text(json.dumps(data),encoding='utf8');assert inv.snapshot() is None


def test_availability_response_is_normal_clarification():
    from bridge_api import QueryRequest,_derived_query_response
    a=check_availability(runtime(),'확인')
    result=_derived_query_response(QueryRequest(query='확인'),a)
    assert result['status']=='need_details' and result['recovery']['issue']=='unsupported'
    assert 'supplement' not in result and 'missingData' not in result and result['availability']['checkedTables']==349


def test_missing_kosis_key_never_uses_local_catalog_as_live_proof():
    from derived_indicator_runtime import DerivedIndicatorRuntime
    r=object.__new__(DerivedIndicatorRuntime);r.availability_unconfigured=True
    r.catalog=SimpleNamespace(allow_file_inputs=False)
    result=r.plan('원자료를 확인하고 아직 계산하지 마')
    assert result['availability']['status']=='verification_failed' and result['availability']['checkedTables']==0
    assert not result['availability']['sources']


def test_verified_snapshot_roundtrip_and_tampering_rejected(tmp_path):
    import hashlib
    tables=[{'table_id':t,'metadata_origin':'actual_KOSIS_metadata_API','table_name':'공개 메타데이터'} for t in ('A','B')]
    data={'origin':'actual_KOSIS_metadata_API','checked_epoch':time.time(),'tables':tables,
        'sha256':hashlib.sha256(json.dumps(tables,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
    inv=DerivedApiInventory(SimpleNamespace(store=SimpleNamespace(table_ids=lambda:['A','B'])),tmp_path)
    inv.path.write_text(json.dumps(data),encoding='utf8');assert inv.snapshot()['tables']==tables
    data['tables'][0]['table_name']='변조'
    inv.path.write_text(json.dumps(data),encoding='utf8');assert inv.snapshot() is None


def test_normal_daily_graph_request_uses_preflight_without_user_check_instruction(monkeypatch):
    import derived_availability
    from derived_indicator_runtime import DerivedIndicatorRuntime
    calls=[];r=object.__new__(DerivedIndicatorRuntime);r.api_inventory=object()
    r.catalog=SimpleNamespace(allow_file_inputs=False)
    expected={'status':'need_details','availability':{'status':'checked','questions':['월평균인가요?'],'sources':[{'status':'not_found'}]}}
    monkeypatch.setattr(derived_availability,'check_availability',lambda runtime,q:(calls.append(q) or expected))
    result=r.plan('2024년 11월 5일부터 2025년 2월 24일까지 달러 대비 통화가치 변화율 그래프를 만들어줘')
    assert result['unsupported'] and result['availability']==expected['availability']
    assert 'supplement' not in result
    assert len(calls)==1


def test_preflight_with_all_inputs_ready_continues_normal_calculation_planning(monkeypatch):
    import derived_availability
    from derived_indicator_runtime import DerivedIndicatorRuntime
    r=object.__new__(DerivedIndicatorRuntime);r.api_inventory=object();calls=[]
    r.catalog=SimpleNamespace(allow_file_inputs=False)
    r.planner=SimpleNamespace(plan=lambda q,**kwargs:(calls.append(q) or {'status':'planned','usage':['planner']}))
    monkeypatch.setattr(derived_availability,'check_availability',lambda runtime,q:{'availability':{'status':'checked','questions':[],'sources':[{'status':'available'}]},'usage':['preflight']})
    assert r.plan('일별 증가율 계산해줘')['usage']==['preflight','planner'] and len(calls)==1
    assert r.plan('일별 자료 확인만 하고 아직 계산하지 마')['availability']['status']=='checked' and len(calls)==1
