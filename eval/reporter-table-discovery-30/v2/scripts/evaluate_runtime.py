"""Public v2 dev-set runner. Runtime receives queries only; gold scores after inference.

Final scores and Markdown are saved only on strict 30/30. Failed attempts retain
endpoint diagnostics, return nonzero, and never overwrite another run.
"""
from pathlib import Path
import argparse
import json


def save_passing_result(output_dir, result):
    summary = result['summary']
    if summary['cases'] != 30 or summary['passed'] != 30 or summary['failed'] != 0:
        return False
    if len(result['cases']) != 30 or not all(c['passed'] and all(c['checks'].values()) for c in result['cases']):
        return False
    env = result['environment']
    lines = [
        '# 기자 질의 골든셋 v2 패치 후 최종 검증 결과', '',
        '**30/30 PASS, 실패 0건.** 모든 문항의 적용 가능한 체크가 통과한 경우에만 이 파일을 저장한다.', '',
        '## 검증 범위', '',
        '`POST /api/query(query, execute=false)` → 실제 LangGraph/Agent → 카탈로그 표·항목·분류 선택 → 조회 계획 검증 → HTTP 응답 반환 직후 종료 로그.', '',
        '- 현재 체크아웃 FastAPI를 TestClient로 직접 호출했다. 다른 저장소의 실행 서버를 사용하지 않았다.',
        '- 문항마다 query만 전달하고 이전 문항의 state는 전달하지 않았다. 30문항 추론 종료 후 정답을 읽어 비교했다.',
        '- 수치 조회와 출력 생성 진입점에 실패 가드를 설치했다. 표 탐색·계열·기간·주기·API 경계까지 검증한다.',
        '- KOSIS/CSV 관측값, 그래프 렌더링, 수정이 편집 및 설명 문장은 검증 범위 밖이다.',
        '- 공개 dev셋이며 ai-assisted, human_approved=false. 정답을 보며 패치한 개발 회귀 결과이고 블라인드 일반화 성능은 아니다.',
        '- 자연어 해석 슬롯 전체의 의미 동등성은 자동 점수에 포함하지 않는다. 실제 분류 결과는 종료 로그에 보존한다.', '',
        '## 패치 내용', '',
        '- 표 이름 부분 일치보다 측정 개념과 실제 카탈로그 분류값을 함께 대조한다.',
        '- 기관·산업·지출 항목·국제수지 세부 항목·지급 수단·건수/금액의 복수 요청을 개별 계획으로 보존한다.',
        '- 긴 분류명 내부의 상위 분류와 문맥상 합계를 추가 선택하지 않는다.',
        '- 미지원 연체율·전망치·채권 금리를 다른 지표로 대체하지 않는다.',
        '- 명시 ID는 기존 정본 경로를 유지하며 주기·지역·실질/명목·조정 기준 및 확정 조건을 확인한다.',
        '- API 재검증에서 전체 정본 계획과 대조해 누락·중복·변조를 차단한다.',
        '- 런타임 선택 코드에 골든셋 파일, 문항 ID, 정답 표 ID를 연결하지 않았다.', '',
        '## 실행 환경과 코드 증거', '',
    ]
    for key, value in env.items():
        lines += [f'- {key}: `{value}`']
    lines += ['', 'HEAD만으로 미커밋 패치를 식별할 수 없어 실행 시 소스 해시도 함께 저장했다. 모델/검색 설정이 활성화되어도 모든 문항에서 호출되는 것은 아니다. 분류 상태별 집계는 아래와 같다.', '']
    from collections import Counter
    counts = Counter(c['classification'].get('status', 'unknown') for c in result['cases'])
    lines += [f'- {key}: {count}건' for key, count in sorted(counts.items())]
    lines += ['', '## 종료 체크 집계', '', '| 체크 | 통과 | 적용 문항 |', '|---|---:|---:|']
    for key, count in summary['check_results'].items():
        lines += [f"| {key} | {count['passed']} | {count['total']} |"]
    lines += ['', f"수치 조회 {summary['numeric_calls']}회, 출력 생성 {summary['output_calls']}회, 문항 실행 시간 합계 {summary['total_seconds']}초.", '',
              'no_match/역질문 문항의 빈 선택 집합도 표·계열 체크의 분모에 들어간다. 정상 선택 21건과 미지원 8건, 역질문 1건을 합한 전체 30건이며 선택 정확도만의 지표로 혼용하지 않는다.', '',
              '## 30문항 종료 결과', '', '| ID | 결과 | Agent 기대 / 실제 | API 기대 / 실제 | 초 |', '|---|---|---|---|---:|']
    for case in result['cases']:
        lines += [f"| {case['id']} | PASS | {case['expected_agent_status']} / {case['actual_agent_status']} | {case['expected_api_status']} / {case['actual_api_status']} | {case['duration_s']} |"]
    lines += ['', '## 문항별 상세', '']
    for case in result['cases']:
        record = json.loads((output_dir/(case['id']+'.json')).read_text(encoding='utf8'))
        lines += [f"### {case['id']} — PASS", '', f"> {case['query']}", '',
                  f"- 실제 종료 로그: [{case['id']}.json]({case['id']}.json)",
                  f"- 기대 표: `{case['expected_table_ids']}` / 실제 표: `{case['actual_table_ids']}`", '',
                  '```json', json.dumps({'checks':case['checks'], 'expected_plans':case['expected_plans'],
                      'actual_plans':case['actual_plans'], 'classification':case['classification'],
                      'actual_boundary':{k:record['boundary_response'].get(k) for k in ['status','periodSelection','clarifications','warnings']},
                      'http_status':record['http_status'], 'numeric_calls':record['numeric_calls'],
                      'output_calls':record['output_calls'], 'error':record['error']}, ensure_ascii=False,indent=2), '```', '']
    lines += ['## 재현과 저장 정책', '',
              '```powershell', '.\.venv\Scripts\python.exe eval/reporter-table-discovery-30/v2/scripts/evaluate_runtime.py', '```', '',
              '- 실행별 새 evaluation_runs 폴더를 생성한다. 기존 실행 폴더에는 쓰지 않는다.',
              '- 매 문항 HTTP 반환 직후 문항 JSON과 endpoint_logs.jsonl에 증거를 기록한다.',
              '- 실패하면 diagnostic_scores.json만 남기고 종료 코드 1을 반환한다. 최종 결과 Markdown과 scores.json은 30/30일 때만 생성한다.',
              '- environment.json에 소스·카탈로그·골든셋 해시를 남긴다. 원자료, 키, 비공개 holdout은 포함하지 않는다.',
              '- 생성 결과는 Git에서 제외된 폴더에 저장하며 커밋하지 않는다.', '']
    (output_dir/'scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (output_dir/'PASS-30-of-30.md').write_text('\n'.join(lines),encoding='utf8')
    return True


def main():
    from pathlib import Path
    import sys,json,time,hashlib,subprocess,collections,datetime
    ROOT=Path(__file__).resolve().parents[4]
    sys.path[:0]=[str(ROOT/'src/agent'),str(ROOT/'src/backend')]
    from fastapi.testclient import TestClient
    import bridge_api as api
    parser=argparse.ArgumentParser(description='Actual API discovery boundary evaluation; final results require all 30 cases to pass.')
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    OUT=args.output_dir or ROOT/'evaluation_runs'/('reporter30_v2_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    OUT.mkdir(parents=True,exist_ok=False)
    GOLD=ROOT/'eval/reporter-table-discovery-30/v2/statbridge-reporter30.jsonl'
    queries=[{'id':x['id'],'query':x['query']} for x in [json.loads(l) for l in GOLD.read_text(encoding='utf-8').splitlines() if l]]
    (OUT/'query_only_inputs.jsonl').write_text(''.join(json.dumps(q,ensure_ascii=False)+'\n' for q in queries),encoding='utf-8')
    source_files=['src/agent/agent_runtime.py','src/agent/catalog_request_planner.py','src/agent/bridge_api.py','eval/reporter-table-discovery-30/v2/scripts/evaluate_runtime.py']
    mode={'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_files},'catalog_sha256':hashlib.sha256((ROOT/'src/agent/stat_dictionary/stat_language_dictionary.json').read_bytes()).hexdigest(),'checkout':str(ROOT),'python':sys.executable,'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'dataset_sha256':hashlib.sha256(GOLD.read_bytes().replace(b'\r\n',b'\n')).hexdigest(),'ncp_configured':api.agent.ncp.configured,'classifier_model':api.agent.ncp.settings.classifier_model,'hybrid_enabled':api.agent.hybrid.enabled,'retrieval_configured':api.agent.hybrid.client.configured,'vector_exists':api.agent.hybrid.path.exists(),'jev_configured':api.agent.jev.configured,'catalog_tables':len(api.agent.tables_by_id),'execution':False,'endpoint':'POST /api/query (in-process TestClient, actual application)','started_at':datetime.datetime.now().astimezone().isoformat()}
    (OUT/'environment.json').write_text(json.dumps(mode,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ENVIRONMENT',json.dumps(mode,ensure_ascii=False),flush=True)
    latest={};calls={'numeric':0,'output':0}
    original_run=api.agent.run
    original_validate=api.agent.validate_resolution
    def traced_run(*args,**kwargs):
     result=original_run(*args,**kwargs);latest['resolution']=result;return result
    def traced_validate(*args,**kwargs):
     result=original_validate(*args,**kwargs);latest['validated']=result;return result
    def numeric_guard(*args,**kwargs):
     calls['numeric']+=1;raise RuntimeError('EVALUATION_BOUNDARY: numeric execution forbidden')
    def output_guard(*args,**kwargs):
     calls['output']+=1;raise RuntimeError('EVALUATION_BOUNDARY: output generation forbidden')
    api.agent.run=traced_run;api.agent.validate_resolution=traced_validate
    api.agent.service.get_statistics=numeric_guard
    api.agent.output_agent.prepare=output_guard
    predictions=[]
    with TestClient(api.app) as client:
     for index,q in enumerate(queries,1):
      latest.clear();before=dict(calls);start=time.perf_counter()
      try:
       response=client.post('/api/query',json={'query':q['query'],'execute':False})
       body=response.json();http_status=response.status_code;error=None
      except Exception as exc:
       body={};http_status=None;error=type(exc).__name__+': '+str(exc)
      resolution=latest.get('validated') or latest.get('resolution') or {}
      plans=resolution.get('api_plans') or ([resolution['api_plan']] if resolution.get('api_plan') else [])
      record={**q,'endpoint':'POST /api/query','http_status':http_status,'agent_status':resolution.get('status'),'api_status':body.get('status'),'classification':resolution.get('classification') or {},'dictionary_query':resolution.get('dictionary_query'),'selected_table_ids':sorted({p['table_id'] for p in plans}),'api_plans':plans,'clarifications':resolution.get('clarifications') or ([resolution] if resolution.get('status')=='need_clarification' else []),'orchestration':{k:resolution.get(k) for k in ['orchestration_stage','orchestration_path']},'boundary_response':body,'numeric_calls':calls['numeric']-before['numeric'],'output_calls':calls['output']-before['output'],'duration_s':round(time.perf_counter()-start,3),'completed_at':datetime.datetime.now().astimezone().isoformat(),'error':error}
      predictions.append(record)
      (OUT/(q['id']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
      with (OUT/'endpoint_logs.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
      print(f"[{index}/30] ENDPOINT {q['id']} agent={record['agent_status']} api={record['api_status']} http={http_status} numeric={record['numeric_calls']} output={record['output_calls']} seconds={record['duration_s']}",flush=True)
    # Gold is loaded for scoring only after all query-only inference has finished.
    golds=[json.loads(l) for l in GOLD.read_text(encoding='utf-8').splitlines() if l]
    scored=[]
    def identity(p,gold=False):
     return (p['table_id'],p['item_id'],tuple(sorted(p['classifications'].items())))
    for gold,pred in zip(golds,predictions):
     w=gold['workflow_gold'];gplans=w['classification_and_plan']['series'];plans=pred['api_plans'];byidentity={identity(p):p for p in plans}
     actual_groups=pred['boundary_response'].get('clarifications') or []
     expected_clar=w['resolution'].get('clarification') or {}
     observed_clar=next((g for g in actual_groups if (g.get('id') or g.get('clarification_id'))==expected_clar.get('id')),None)
     checks={'http_success':pred['http_status']==200,'agent_status':pred['agent_status']==w['resolution']['agent_status'],'api_boundary_status':pred['api_status']==w['resolution']['api_status'],'table_set':set(pred['selected_table_ids'])==set(w['table_discovery']['expected_table_ids']),'series_identity_set':set(byidentity)=={identity(p) for p in gplans},'exact_api_params':len(plans)==len(gplans) and all(identity(p) in byidentity and byidentity[identity(p)]['exact_params']==p['exact_params'] for p in gplans),'numeric_not_executed':pred['numeric_calls']==0,'chart_not_generated':pred['output_calls']==0 and not pred['boundary_response'].get('chart')}
     if w['resolution']['agent_status']=='need_clarification':
      checks['clarification_options']=observed_clar is not None and {o['value'] for o in observed_clar.get('options',[])}=={o['value'] for o in expected_clar.get('options',[])}
     if w['resolution']['agent_status']=='resolved':
      selected_text=(pred['boundary_response'].get('periodSelection') or {}).get('source')=='text'
      checks['period_followup']=pred['api_status']=='need_period' and selected_text is (not w['post_discovery']['period_selection_required'])
     scored.append({'id':gold['id'],'query':gold['query'],'checks':checks,'passed':all(checks.values()),'failed_checks':[k for k,v in checks.items() if not v],'expected_agent_status':w['resolution']['agent_status'],'actual_agent_status':pred['agent_status'],'expected_api_status':w['resolution']['api_status'],'actual_api_status':pred['api_status'],'expected_table_ids':w['table_discovery']['expected_table_ids'],'actual_table_ids':pred['selected_table_ids'],'expected_plans':gplans,'actual_plans':plans,'duration_s':pred['duration_s'],'classification':pred['classification']})
    summary={'cases':len(scored),'passed':sum(x['passed'] for x in scored),'failed':sum(not x['passed'] for x in scored),'check_results':{key:{'passed':sum(x['checks'].get(key) is True for x in scored),'total':sum(key in x['checks'] for x in scored)} for key in sorted({k for x in scored for k in x['checks']})},'numeric_calls':calls['numeric'],'output_calls':calls['output'],'total_seconds':round(sum(x['duration_s'] for x in scored),3)}
    result={'environment':mode,'summary':summary,'cases':scored}
    if not save_passing_result(OUT,result):
     (OUT/'diagnostic_scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
     print('NOT_SAVED: final result requires 30/30; diagnostics retained.',flush=True)
    print('SCORES',json.dumps(summary,ensure_ascii=False),flush=True)

    return 0 if summary['cases']==30 and summary['passed']==30 and not summary['failed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
