from __future__ import annotations
import json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
AGENT=ROOT/'src'/'agent'
MCP=ROOT/'data'/'statbridge_mcp_server'
sys.path[:0]=[str(AGENT),str(MCP)]

# Importing MCP config first loads statbridge_mcp_server/.env.
from statbridge_mcp import config as _mcp_config  # noqa: F401
from ncp_clova_client import NcpClovaClient

c=NcpClovaClient()
print('configured:',c.configured)
print('classifier:',c.settings.classifier_model,c.settings.classifier_api_version,c.settings.classifier_url or '(default URL)')
print('main:',c.settings.main_model,c.settings.main_api_version,c.settings.main_url or '(default URL)')
if not c.configured:
    raise SystemExit('NCP_CLOVA_API_KEY를 data/statbridge_mcp_server/.env에 넣어주세요.')

print('\n[1] HCX-003 classifier smoke test')
try:
    r=c.classify_stat_language('요즘 사람들이 집 담보로 빌린 돈이 얼마나 늘었어?')
    print(json.dumps({k:v for k,v in r.items() if not k.startswith('_')},ensure_ascii=False,indent=2))
    print('[PASS] classifier URL:',r.get('_url'))
except Exception as exc:
    print('[FAIL] HCX-003:',exc)
    print('HCX-003는 기본적으로 v1/chat-completions/HCX-003를 사용합니다. 키 권한과 .env 설정을 확인하세요.')

print('\n[2] HCX-007 main-agent smoke test')
try:
    text,_=c.chat_main(
        '너는 StatBridge 메인 에이전트다. 한 문장으로만 답한다.',
        '통계 후보가 소비자물가와 생산자물가로 갈린다. 사용자에게 어느 물가인지 확인하는 한 문장을 작성해라.',
        max_tokens=400,
        thinking_effort='none',
    )
    print(text)
    print('[PASS] HCX-007')
except Exception as exc:
    print('[FAIL] HCX-007:',exc)
