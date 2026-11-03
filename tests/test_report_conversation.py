import copy,json
from decimal import Decimal
import pytest
from pydantic import ValidationError
from report_conversation import ReportPlan,reply,refresh,execute,key,public_state,MAX_TURNS
from test_statistical_question import item

class NoModel:
    configured=True
    def request(self,*args,**kwargs):raise AssertionError('unnecessary model call')


def data():
    return [item('대출금리',('5.1','5.0','4.9'),dates=('202501','202502','202503')),item('수신금리',('3.1','3.0','2.6'),dates=('202501','202502','202503'))]


def talk(q,state=None,series=None,revision=0,client=None):return reply(q,series or data(),state,revision,client or NoModel())


def test_followup_rank_formula_and_table_need_no_gpt_and_preserve_context():
    a,s=talk('해당 그래프의 변화율을 설명해줘')
    b,s=talk('그중 가장 많이 하락한 것은?',s)
    assert b['facts'][0]['result']=='-0.5' and b['facts'][0]['observations'][-1]['label']=='수신금리'
    assert b['modelCalls']==0 and len(s['turns'])==2
    c,s=talk('그 계산식을 알려줘',s)
    assert '%p' in ''.join(c['paragraphs']) and len(s['turns'][-1]['context']['keys'])==1
    d,s=talk('같은 기준으로 전월대비로 보여줘',s)
    assert d['facts'][0]['result']=='-0.4'
    e,s=talk('표로 정리해줘',s)
    assert e['table']['rows'][0][3]=='-0.40' and e['modelCalls']==0


def test_edit_recomputes_ranking_and_dependent_turns_not_old_winner():
    _,s=talk('그래프 변화를 설명해줘');_,s=talk('그중 가장 많이 하락한 것은?',s);_,s=talk('표로 정리해줘',s)
    changed=data();changed[0]['points'][-1]['value']='1.0'
    before=copy.deepcopy(s);updated=refresh(s,changed,1)
    assert s==before
    assert updated['turns'][1]['answer']['facts'][0]['result']=='-4.1'
    assert updated['turns'][2]['answer']['table']['rows'][0][0]=='대출금리'
    assert all(t['verifiedRevision']==1 for t in updated['turns'])
    assert updated['turns'][1]['askedRevision']==0


def test_removed_series_and_missing_dates_do_not_use_old_values():
    _,s=talk('그래프 변화율 설명해줘');_,s=talk('그중 가장 많이 하락한 것은?',s);_,s=talk('표로 정리해줘',s)
    changed=data()[:1];updated=refresh(s,changed,1)
    # The initial all-series plan now covers current data, so winner changes legitimately.
    assert updated['turns'][1]['context']['keys']==[key(changed[0])]
    plan=ReportPlan(selection='explicit',keys=[key(data()[1])])
    answer,_=execute('이전 지표',plan,changed,{})
    assert answer['status']=='insufficient_data' and not answer['facts']
    answer,_=execute('전년대비',ReportPlan(comparison='yearly'),data(),{})
    assert answer['status']=='insufficient_data' and '202403' in ' '.join(answer['paragraphs'])


def test_model_used_once_for_unfamiliar_wording_and_only_receives_metadata():
    class Model:
        configured=True
        calls=0
        def request(self,system,payload,**kwargs):
            self.calls+=1;body=json.loads(payload)
            assert 'value' not in payload and 'points' not in payload
            assert body['history'][-1]['question']=='그래프 변화율 설명해줘'
            return json.dumps(ReportPlan(operation='changes',selection='explicit',keys=[key(data()[0])],style='brief').model_dump()),{}
    _,s=talk('그래프 변화율 설명해줘');model=Model()
    a,s=talk('돈 빌릴 때 적용되는 쪽만 간결하게 써줘',s,client=model)
    b,s=talk('돈 빌릴 때 적용되는 쪽만 간결하게 써줘',s,client=model,revision=1)
    assert model.calls==1 and a['modelCalls']==1 and b['modelCalls']==0
    assert a['facts'][0]['observations'][-1]['label']=='대출금리'


@pytest.mark.parametrize('bad',[{'operation':'eval'},{'operation':'changes','code':'print(1)'},{'selection':'explicit','keys':[]},{'comparison':'weekly'}])
def test_model_plan_schema_fails_closed(bad):
    with pytest.raises(ValidationError):ReportPlan.model_validate(bad)


def test_unknown_model_series_and_provider_failure_do_not_mutate_context():
    class Bad:
        configured=True
        def request(self,*args,**kwargs):return json.dumps({'selection':'explicit','keys':['foreign']}),{}
    _,s=talk('그래프 변화를 설명해줘');before=copy.deepcopy(s)
    with pytest.raises(RuntimeError):talk('생소한 요청',s,client=Bad())
    assert s==before
    with pytest.raises(RuntimeError):talk('생소한 요청',s)
    assert s==before


def test_unsupported_causes_do_not_become_invented_report_claims():
    a,_=execute('왜 이런 변화가 생겼어?',ReportPlan(operation='clarify',reason='cause'),data(),{})
    assert a['status']=='insufficient_data' and not a['facts'] and '보고서 근거' in a['paragraphs'][0]


def test_rank_ties_mixed_units_and_zero_denominator():
    ds=data();ds[1]['points']=copy.deepcopy(ds[0]['points'])
    a,_=execute('최대 하락',ReportPlan(operation='rank',direction='down'),ds,{})
    assert len(a['facts'])==2 and '공동' in a['paragraphs'][0]
    ds[1]['unit']='조원';a,_=execute('최대',ReportPlan(operation='rank'),ds,{})
    assert a['status']=='insufficient_data'
    ds=[item('지수',('0','1'),unit='지수')];a,_=execute('변화',ReportPlan(),ds,{})
    assert a['status']=='insufficient_data'


def test_relative_change_is_not_percentage_point_and_gap_is_level_gap():
    a,_=execute('비중의 상대 변화율',ReportPlan(measure='relative'),data(),{})
    assert Decimal(a['facts'][0]['result'])==pytest.approx(Decimal('4.9')/Decimal('5.1')*100-100)
    b,_=execute('금리 격차 확대?',ReportPlan(operation='gap'),data(),{})
    assert b['facts'][0]['result']=='2.3' and b['facts'][0]['absoluteWidthChange']=='0.3'
    assert '5.1' not in ' '.join(b['paragraphs'])


def test_history_bounded_and_public_state_hides_plan_cache():
    s=None
    for _ in range(MAX_TURNS+3):_,s=talk('그래프 변화를 설명해줘',s)
    assert len(s['turns'])==MAX_TURNS and s['version']==MAX_TURNS+3
    assert 'plan_cache' not in public_state(s)


def test_explicit_date_missing_stops_and_new_display_mode_revalidates():
    a,_=execute('날짜 변경',ReportPlan(baseline='202401'),data(),{})
    assert a['status']=='insufficient_data' and '202401' in a['paragraphs'][0]
    _,s=talk('그래프 변화를 설명해줘')
    ds=data()
    for d in ds:d['points']=d['points'][-2:]
    updated=refresh(s,ds,0,'table')
    assert updated['turns'][0]['answer']['facts'][1]['result']=='-0.4'


def test_brief_keeps_all_requested_series_and_never_leaves_transition_only():
    a,_=execute('간결하게 써줘',ReportPlan(style='brief'),data(),{})
    assert len(a['paragraphs'])==1
    assert all(name in a['paragraphs'][0] for name in ['대출금리','수신금리'])
    assert '다음과 같아' not in a['paragraphs'][0]
    assert all(o.get('sourceId')=='SYNTHETIC' for f in a['facts'] for o in f['observations'])


def test_paragraph_style_and_simple_style_are_natural_and_preserve_figures():
    a,_=execute('한 문단',ReportPlan(style='paragraph'),data(),{})
    assert len(a['paragraphs'])==1 and '0.5%p' in a['paragraphs'][0]
    b,_=execute('쉽게',ReportPlan(style='simple'),data(),{})
    assert '하락하였다' in ' '.join(b['paragraphs']) and '내려감했어' not in ' '.join(b['paragraphs'])


def test_freeform_planner_receives_the_actual_schema():
    class Planner:
        configured=True
        def request(self,system,user,**kwargs):
            payload=json.loads(user)
            assert payload["schema"] == ReportPlan.model_json_schema()
            assert "keys" in payload["schema"]["properties"]
            return ReportPlan().model_dump_json(), {}
    answer,state=talk("표시된 두 금융 지표를 보고서 문장으로 정돈해줘",client=Planner())
    assert answer["modelCalls"]==1 and state["turns"][-1]["plan"]["operation"]=="changes"
