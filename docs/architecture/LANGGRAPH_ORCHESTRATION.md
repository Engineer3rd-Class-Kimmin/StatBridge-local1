# StatBridge1 LangGraph 오케스트레이션

## 적용 범위

LangGraph는 UI와 데이터 계층을 대체하지 않고 `StatBridgeAgent`의 실행 순서만 관리한다.

```text
START
  -> resolve_request
      -> await_clarification -> END
      -> finish_unresolved   -> END
      -> execute_statistics  -> END
```

- `resolve_request`: 기존 `StatBridgeAgent.resolve()` 호출
- `await_clarification`: 기존 역질문 결과를 변경 없이 반환
- `finish_unresolved`: `no_match`, `catalog_only` 등 기존 비해결 결과를 반환
- `execute_statistics`: 기존 `StatBridgeAgent.execute_resolution()` 호출

## 보존한 경계

- 통계언어 사전과 `StatLanguageResolver`
- HCX-003/HCX-007 호출
- 검증된 table/item/classification ID 선택
- API Plan 생성
- `McpToolGateway`
- `StatisticsService`
- KOSIS 및 로컬 데이터 접근
- `/api/query` 응답 형식과 React UI

`bridge_api.py`의 최초 질의, 사용자 선택 재검색, 기간 확정 후 실행은 모두
`StatBridgeWorkflow`를 통과한다. 그래프 실행 결과에는 내부 확인용
`orchestration.engine`, `stage`, `path`가 추가되며 기존 필드는 유지된다.

## 검증

```powershell
statbridge_mcp_server\.venv\Scripts\python.exe TEST_LANGGRAPH_FLOW.py
statbridge_mcp_server\.venv\Scripts\python.exe TEST_AGENT_FLOW.py
```

`/api/health`는 `orchestration_engine=langgraph`와 현재 그래프 노드 목록을 반환한다.
