from __future__ import annotations
import os, sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / 'src' / 'agent'
MCP = ROOT / 'src' / 'backend'
os.environ.setdefault('STATBRIDGE_DATA_DIR', str(ROOT / 'data' / 'processed'))
os.environ.setdefault('STATBRIDGE_TABLES_DIR', str(ROOT / 'data' / 'tables'))
sys.path[:0] = [str(AGENT), str(MCP)]

from agent_runtime import StatBridgeAgent

class CaptureMcpGateway:
    def __init__(self): self.calls=[]
    def get_statistics(self, **kwargs):
        self.calls.append(kwargs)
        return {'source':'capture_mcp','status':'success','row_count':2,'rows':[{'PRD_DE':'202507','DT':'100.1'},{'PRD_DE':'202508','DT':'101.2'}]}

class FakeNcp:
    def __init__(self):
        self.configured=True
        self.settings=SimpleNamespace(classifier_model='HCX-003', main_model='HCX-007')
        self.classify_calls=[]; self.clarify_calls=[]; self.answer_calls=[]
    def classify_stat_language(self, query):
        self.classify_calls.append(query)
        if '경제 분위기' in query:
            return {'normalized_query':'경제심리지수 추이','concepts':['경제심리지수'],'subjects':[], 'measures':['지수'], 'time_terms':['최근'], 'comparison_terms':['추이'], 'qualifiers':[], '_model':'HCX-003'}
        return {'normalized_query':query,'concepts':[], 'subjects':[], 'measures':[], 'time_terms':[], 'comparison_terms':[], 'qualifiers':[], '_model':'HCX-003'}
    def clarify_question(self, original_query, clarification, state):
        self.clarify_calls.append((original_query,clarification['clarification_id']))
        return 'HCX-007 확인 질문: ' + clarification['question']
    def answer_with_data(self, query, plan, execution):
        self.answer_calls.append((query,plan['table_id'],execution['row_count']))
        return f"HCX-007 최종답변: {plan['table_name']} 조회 결과 {execution['row_count']}행을 확인했습니다."

def main():
    gateway=CaptureMcpGateway(); ncp=FakeNcp()
    agent=StatBridgeAgent(gateway,ncp_client=ncp)

    # 1) HCX-003 classifier must run before dictionary search.
    c=agent.resolve('요즘 경제 분위기 추이 보여줘')
    assert ncp.classify_calls and c['classification']['_model']=='HCX-003'
    assert '경제심리지수' in c['dictionary_query']
    print('[PASS] HCX-003 자연어→통계언어:', c['dictionary_query'])

    # 2) ambiguous query -> HCX-007 phrased clarification 1
    r1=agent.resolve('대출 얼마나 늘었어?')
    assert r1['status']=='need_clarification' and r1['clarification_id']=='loan_type'
    assert r1['question'].startswith('HCX-007 확인 질문:')
    print('[PASS] HCX-007 1차 역질문:', r1['question'])

    # 3) choose mortgage -> clarification 2, classifier must NOT rerun
    before=len(ncp.classify_calls)
    r2=agent.resolve('대출 얼마나 늘었어?', r1['state'], {'clarification_id':'loan_type','value':'주택담보대출'})
    assert r2['status']=='need_clarification' and r2['clarification_id']=='loan_measure'
    assert len(ncp.classify_calls)==before
    print('[PASS] 선택값 hard constraint + 재검색:', r2['question'])

    # 4) choose balance -> exact API plan -> MCP call -> HCX-007 final answer
    r3=agent.run('대출 얼마나 늘었어?', r2['state'], {'clarification_id':'loan_measure','value':'잔액'}, execute=True)
    assert r3['status']=='resolved'
    plan=r3['api_plan']; call=gateway.calls[-1]
    assert plan['table_id']==call['table_id'] and plan['item_id']==call['item_id']
    assert plan['classifications']==call['classifications'] and plan['frequency']==call['frequency']
    assert r3['answer'].startswith('HCX-007 최종답변:') and ncp.answer_calls
    print('[PASS] MCP 정확 호출:', call)
    print('[PASS] HCX-007 최종 답변:', r3['answer'])
    print('[PASS] exact KOSIS params:', plan['exact_params'])

if __name__ == '__main__': main()
