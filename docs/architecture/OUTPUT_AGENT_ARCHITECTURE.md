# 출력 에이전트 분리 구조

## 변경 목적

기존에는 UI 연결 계층인 `bridge_api.py`가 MCP 조회 결과를 그래프 계열로 변환하고 그래프 요약까지 만들었다. 변경 후 입력과 출력 책임을 분리한다.

```text
React 입력 UI
  → FastAPI /api/query
  → LangGraph resolve_request
  → 통계언어 사전·HCX·검색
  → LangGraph execute_statistics
  → MCP StatisticsService / KOSIS
  → LangGraph await_output_selection
  → UI 그래프 종류·배치·편집값 선택
  → POST /api/output
  → LangGraph prepare_output
  → OutputAgent
  → 편집 가능한 visualization 명세
  → React ChartRenderer
```

## 입력 에이전트 책임

- 자연어 질문 수신
- 통계언어 구조화와 역질문
- 검증된 표·항목·분류 ID 선택
- 조회 기간 및 MCP 호출 계획 확정
- MCP 통계 데이터 실행

그래프 계열 변환, 그래프 유형 결정, 출력 요약 생성은 더 이상 담당하지 않는다.

## 출력 에이전트 책임

`src/agent/output_agent.py`의 `OutputAgent`가 다음을 담당한다.

- MCP 행 데이터를 프런트엔드 독립적인 시계열 구조로 변환
- `auto`, `line`, `bar`, `area`, `scatter` 그래프 유형 처리
- 여러 계열의 `combined`, `separate` 레이아웃 처리
- 그래프 제목, 범례 표시, X/Y축 이름 편집 주문 수신
- 사용 가능한 그래프와 레이아웃 목록을 `editOptions`로 반환
- 데이터 변화 요약 생성

현재는 규칙 기반 스켈레톤이다. 이후 추천 모델이나 별도 출력 LLM을 붙이더라도 MCP 원자료와 입력 에이전트 코드는 변경하지 않도록 경계를 잡았다.

## LangGraph 변경

기존 실행 경로:

```text
resolve_request → execute_statistics → END
```

변경 실행 경로는 데이터 조회와 출력을 두 요청으로 분리한다.

```text
resolve_request → execute_statistics → await_output_selection → END

사용자 출력 선택 후:

```text
START → prepare_output → END
```

역질문과 미해결 경로는 기존처럼 바로 종료된다. MCP 조회에 성공한 경우에만 출력 에이전트가 실행된다.

## API 계약 스켈레톤

요청에 다음 필드가 추가됐다.

```json
{
  "chart_type": "auto",
  "chart_mode": "combined",
  "chart_options": {
    "title": "그래프 제목",
    "show_legend": true,
    "x_axis_label": "시점",
    "y_axis_label": "값"
  }
}
```

`POST /api/query`의 데이터 조회 성공 응답은 그래프를 포함하지 않고 `need_output_config`, `outputSessionId`, 사용 가능한 그래프 목록만 반환한다. UI가 출력 선택을 마치면 `POST /api/output`에 세션 ID와 편집값을 보낸다. 최종 응답에는 `chart`, `chartMode`, `chartType`, `outputSpec`이 포함되며 UI는 그 명세만 렌더링한다.

## 제거된 기존 책임

`bridge_api.py`에 있던 `_series_key`, `_chart_series`, `_chart_summary`를 삭제했다. 이 로직은 모두 `OutputAgent`로 이동했다. React의 기존 `LineChart`도 `ChartRenderer`로 교체해 출력 에이전트가 선택한 그래프 유형을 받도록 변경했다.

프런트엔드에서 여러 API 응답의 그래프 계열과 색상을 직접 합치던 코드도 제거했다. 다중 비교는 여러 `outputSessionId`를 `/api/output`으로 보내며, 원자료 병합과 계열 생성은 출력 에이전트 경계 안에서 수행한다.
