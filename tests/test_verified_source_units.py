"""Synthetic observations; source identity metadata is public, no gold values."""
import copy
from types import SimpleNamespace
import pytest
from verified_source_units import TABLE,ITEM,CLASSES,unit_resolution
from dynamic_expression_executor import source_series


def source():
    return SimpleNamespace(alias='ccsi',table_id=TABLE,item_id=ITEM,classifications=dict(CLASSES),
                           frequency='M',required_data='소비자심리지수')


def response():
    return {'status':'success','source':'kosis_api','table_id':TABLE,
            'rows':[{'ORG_ID':'301','TBL_ID':TABLE,'ITM_ID':ITEM,'C1':CLASSES['objL1'],
                     'C2':CLASSES['objL2'],'PRD_SE':'M','PRD_DE':period,'DT':value}
                    for period,value in [('202401','100'),('202402','102')]]}


def test_verified_dimensionless_unit_preserves_raw_response():
    raw=response();before=copy.deepcopy(raw)
    resolved=unit_resolution(source(),raw)
    assert resolved['unit']=='지수' and resolved['api_unit_status']=='not_supplied'
    assert resolved['sources'][0]['pdf_page']==9 and len(resolved['sources'][0]['sha256'])==64
    assert source_series(source(),raw).unit=='지수'
    assert raw==before and all('UNIT_NM' not in r for r in raw['rows'])


def test_original_esi_unit_is_narrow_and_provenance_preserving():
    from verified_source_units import ESI_CLASSES
    s = SimpleNamespace(alias='esi', table_id='DT_513Y001', item_id='13103134473999',
                        classifications=dict(ESI_CLASSES), frequency='M', required_data='경제심리지수 원계열')
    raw = {'status':'success', 'source':'kosis_api', 'table_id':s.table_id, 'rows':[
        {'ORG_ID':'301','TBL_ID':s.table_id,'ITM_ID':s.item_id,'C1':ESI_CLASSES['objL1'],
         'PRD_SE':'M','PRD_DE':'202701','DT':'101'}]}
    before = copy.deepcopy(raw)
    resolution = unit_resolution(s, raw)
    assert resolution['unit'] == '지수' and resolution['api_unit_status'] == 'not_supplied'
    assert source_series(s, raw).unit == '지수'
    assert raw == before
    raw['rows'][0]['C1'] = 'unverified_series'
    assert unit_resolution(s, raw) is None
    with pytest.raises(ValueError):
        source_series(s, raw)


@pytest.mark.parametrize('field,value',[('table_id','other'),('item_id','other'),
                                     ('frequency','Q'),('classifications',{'objL1':'other'})])
def test_other_sources_do_not_inherit_unit(field,value):
    s=source();setattr(s,field,value)
    assert unit_resolution(s,response()) is None


@pytest.mark.parametrize('field,value',[('ORG_ID','999'),('ITM_ID','other'),('TBL_ID','other'),
                                     ('C1','other'),('C2','other'),('PRD_SE','Q')])
def test_forged_response_does_not_inherit_unit(field,value):
    raw=response();raw['rows'][0][field]=value
    assert unit_resolution(source(),raw) is None
    with pytest.raises(ValueError):source_series(source(),raw)


def test_supplied_unit_is_not_overwritten_and_mixed_units_fail():
    raw=response()
    for row in raw['rows']:row['UNIT_NM']='%'
    assert unit_resolution(source(),raw) is None and source_series(source(),raw).unit=='%'
    raw['rows'][1]['UNIT_NM']=''
    with pytest.raises(ValueError,match='단위'):source_series(source(),raw)


def test_non_api_and_empty_response_not_resolved():
    raw=response();raw['source']='user_file'
    assert unit_resolution(source(),raw) is None
    for bad_unit in [0,False,[],{}]:
        raw=response();raw['rows'][0]['UNIT_NM']=bad_unit
        assert unit_resolution(source(),raw) is None
        with pytest.raises(ValueError):source_series(source(),raw)
    raw=response();raw['rows']=[]
    assert unit_resolution(source(),raw) is None


def test_resolution_copy_cannot_mutate_registry():
    resolved=unit_resolution(source(),response());resolved['sources'].clear()
    assert len(unit_resolution(source(),response())['sources'])==2


def test_receipt_separates_official_definition_from_raw_observations(tmp_path):
    from derived_indicator_runtime import DerivedIndicatorRuntime
    from derived_catalog_tools import DerivedCatalogTools
    from statbridge_mcp.metadata_store import MetadataStore
    from expression_tree_schema import ExpressionPlan
    s=source();catalog=DerivedCatalogTools(MetadataStore())
    plan=ExpressionPlan.model_validate({'plan_version':'1.0','indicator_name':'합성 단위 검증',
        'economic_definition':'합성 지수값의 상대 변화율 검증',
        'sources':[vars(s)],'nodes':[{'id':'growth','operator':'pct_change','inputs':['ccsi'],'lag':1}],
        'outputs':[{'node':'growth','label':'합성 상대 변화율'}],'start_period':'202401','end_period':'202402'})
    runtime=DerivedIndicatorRuntime(SimpleNamespace(service=SimpleNamespace(store=catalog.store)),SimpleNamespace(),tmp_path)
    raw=response();before=copy.deepcopy(raw)
    result=runtime.calculate('소비자심리지수 상대 변화율',{'plan':plan,'semantic_review':{'approved':True}}, {'ccsi':raw})
    assert result['execution']['rows'][-1]['DT']==pytest.approx(2)
    record=result['_derived']['record']
    assert record['unit_resolutions']['ccsi']['unit']=='지수'
    assert all('UNIT_NM' not in row for row in record['source_data']['ccsi']['rows'])
    assert result['derivedEvidence']['sourceSelections'][0]['unitResolution']['scale'].startswith('장기평균')
    assert any('상대변화율' in text for text in result['derivedEvidence']['limitations'])
    assert raw==before
