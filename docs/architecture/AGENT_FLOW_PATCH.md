# StatBridge Agent Flow Patch

## 목표
UI 자연어 질문을 Agent가 받고, 통계언어 사전 v5를 검색한 뒤 애매하면 버튼형 역질문으로 확인합니다. 선택값은 hard constraint로 저장하여 사전을 재검색하고, 최종적으로 사전에 존재하는 `tblId`, `itmId`, `objL1..8`, `prdSe`, 기간만 사용해 MCP 통계 서비스의 `get_statistics`를 호출합니다.

## 실행 흐름
`UI -> Agent API -> StatLanguageResolver v5 -> (필요 시 clarification buttons -> 재검색) -> McpToolGateway -> StatisticsService/MCP tool layer -> KOSIS API`

현재 portable MCP 서버는 외부 클라이언트용 stdio 서버입니다. HTTP Agent가 매 요청마다 별도 stdio MCP 프로세스를 띄우지 않도록, Agent의 `McpToolGateway`는 MCP tool들이 사용하는 동일한 `StatisticsService`를 호출합니다. 즉 API 파라미터 생성과 데이터 계층은 MCP와 동일합니다.

## 주요 파일
- `StatBridge-official/src/agent/statbridge_agent.py`: 사전 검색, 역질문 상태, API plan, MCP 호출 오케스트레이션
- `StatBridge-official/src/agent/mcp_gateway.py`: Agent -> MCP tool/service gateway
- `StatBridge-official/src/agent/bridge_api.py`: UI용 FastAPI endpoint
- `StatBridge-official/src/agent/stat_dictionary/`: v5 통계언어 사전
- `StatBridge-official/src/agent/frontend/src/App.tsx`: 역질문 버튼 UI
- `TEST_AGENT_FLOW.py`: API ID와 MCP 전달 파라미터 단위 테스트

## 실행
기존처럼 `START_STATBRIDGE.cmd`를 실행합니다.

KOSIS 실호출을 하려면 최초 실행 후 생성되는 `statbridge_mcp_server/.env`에 다음을 넣습니다.

```env
KOSIS_API_KEY=발급받은키
```

그 뒤 재시작합니다.

## 확인 질문
UI에서 아래 순서로 테스트합니다.

1. `대출 얼마나 늘었어?`
2. `주택담보대출` 버튼
3. `현재 남아 있는 대출 잔액` 버튼
4. 결과의 Data Lineage에서 Agent -> 통계언어 -> MCP -> KOSIS 흐름 확인

명확한 질문 테스트:

`최근 5년 경제심리지수 추이`

이 경우 역질문 없이 `DT_513Y001`로 직접 resolve됩니다.
