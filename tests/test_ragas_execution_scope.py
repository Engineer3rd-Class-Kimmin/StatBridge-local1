"""Synthetic evidence: evaluation scope must not manufacture factual support."""
import copy
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('ragas_execution_scope',Path(__file__).parents[1]/'tools/ragas_execution_scope.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
build_execution_scope=module.build_execution_scope
ScopeEvidenceError=module.ScopeEvidenceError


def data():
    identity={'provider':'kosis','table_id':'T','item_id':'I','frequency':'Y','classifications':{'objL1':'C'}}
    final={'period':{'start':'2027-01-01','end':'2028-12-31'},'summary':'UNTRUSTED',
           'chart':[{'sourceId':'T','frequency':'Y','sourceIdentity':identity,
                     'points':[{'date':'2027','value':7},{'date':'2028','value':9}]}]}
    evidence=[{'origin':'official_api_response','payload':{
        'request':{'tblId':'T','itmId':'I','prdSe':'Y','objL1':'C','startPrdDe':'2027','endPrdDe':'2028'},
        'response':[{'TBL_ID':'T','ITM_ID':'I','PRD_SE':'A','C1':'C','PRD_DE':p,'DT':str(v)}
                    for p,v in [('2027',7),('2028',9)]]}}]
    metadata={'tables':[{'table_id':'T','periods':[{'frequency':'Y','start_period':'2010','end_period':'2028'}]}]}
    return final,evidence,metadata


def test_catalog_history_does_not_replace_executed_display_period():
    final,evidence,metadata=data();before=copy.deepcopy((final,evidence,metadata))
    scope=build_execution_scope(final,evidence,metadata)['series'][0]
    assert scope['first_displayed_observation_period']=='2027'
    assert scope['catalog_available_periods'][0]['periods'][0]['start_period']=='2010'
    assert 'summary' not in str(scope) and 'UNTRUSTED' not in str(scope)
    assert (final,evidence,metadata)==before


@pytest.mark.parametrize('tamper',['value','date','item','class','frequency','period'])
def test_invalid_output_cannot_certify_its_own_scope(tamper):
    final,evidence,metadata=data()
    if tamper=='value':final['chart'][0]['points'][0]['value']=100
    elif tamper=='date':final['chart'][0]['points'][0]['date']='2026'
    elif tamper=='item':final['chart'][0]['sourceIdentity']['item_id']='OTHER'
    elif tamper=='class':final['chart'][0]['sourceIdentity']['classifications']['objL1']='OTHER'
    elif tamper=='frequency':final['chart'][0]['frequency']='M'
    else:final['period']['start']='2028-01-01'
    with pytest.raises(ScopeEvidenceError):build_execution_scope(final,evidence,metadata)


def test_lookback_period_and_actual_display_are_both_retained():
    final,evidence,metadata=data()
    final['chart']=[{'sourceId':'mean','frequency':'M','calculation':'mean',
                     'points':[{'date':'202803','value':8}]}]
    receipt={'origin':'executed_calculation_evidence','payload':{
        'plan':{'sources':[{'table_id':'T'}],'outputs':[{'node':'mean'}]},
        'bounds':{'frequency':'M','fetch_start':'202801','fetch_end':'202803'},
        'node_results':{'mean':{'frequency':'M','values':{'202803':8}}}}}
    scope=build_execution_scope(final,evidence+[receipt],metadata)['series'][0]
    assert scope['fetch_periods'][0]['fetch_start']=='202801'
    assert scope['first_displayed_observation_period']=='202803'
    receipt['payload']['plan']['outputs'][0]['display']='table'
    with pytest.raises(ScopeEvidenceError):build_execution_scope(final,evidence+[receipt],metadata)


def test_no_chart_has_no_execution_scope():
    assert build_execution_scope({'summary':'unverified display start 2027'},[],{}) is None
