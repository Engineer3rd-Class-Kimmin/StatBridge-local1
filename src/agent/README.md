# src/agent — Agent · LLM · Frontend

소유: @snarmse

담당:
- 자연어 질의 해석
- **Query Planner** (툴 선택·호출 순서 계획)
- **LangGraph 기반 Agent Workflow**
- MCP Tool 선택·호출
- 응답·시각화 구성 (`render_chart`)
- Web UI·사용자 흐름 (FastAPI + 정적 페이지)
- 수치 무결성 게이트 (표시값 = fetch값)

관련: `docs/운영/역할별-작업-구체화.md` 2장

## 현재 구조 확인

```text
statbridge_agent/
├─ models.py                 # LLM/판단 모델 Protocol과 테스트 Mock
├─ hcx.py                    # HCX OpenAI-compatible API 어댑터
├─ tools.py                  # MCP 계약 소비 계층 (Fixture/InProcess)
├─ pipeline.py               # 자연어 → ParsedQuery → Top-K 파이프라인
├─ nodes/
│  ├─ query_interpret.py     # 자연어 질의 → QueryIntent/ParsedQuery
│  └─ retrieve.py            # 검색어 구성 → search_tables
└─ experiment/               # 모델·노드별 비교 실험
```

`tools.py`의 `ToolClient`는 `schemas/mcp_tools.json` v1.0.0을 기준으로 합니다.
Agent 단위 테스트에서는 `FixtureTools`, 백엔드와 한 프로세스로 통합할 때는
`InProcessTools`를 사용합니다. 실제 stdio MCP 연결은 후속 `McpStdioClient`
어댑터로 추가하며, 백엔드 검색·KOSIS 코드를 Agent 경로에 복제하지 않습니다.

현재 1차 마일스톤은 다음 경로입니다.

```text
사용자 자연어 → HCX Structured Output → ParsedQuery
→ ToolClient.search_tables(nl_query, top_k=5) → 실제 통계표 후보
```

HCX는 지표·기간·검색어만 생성하며 통계표 ID는 생성하지 않습니다. 표 ID는
반드시 Backend MCP 검색 결과에서만 가져옵니다.
