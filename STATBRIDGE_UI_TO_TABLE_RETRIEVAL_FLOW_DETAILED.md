# StatBridge1 UI 텍스트 입력부터 통계표 탐색까지의 전체 실행 흐름

## 1. 문서 목적과 확인 범위

이 문서는 현재 `C:\Users\김민\Desktop\경진대회\StatBridge1` 코드에서 사용자가 홈 화면에 자연어 질문을 입력한 순간부터 다음 단계까지 실제 실행되는 흐름을 설명한다.

1. 브라우저 UI가 질문을 수집한다.
2. Agent API가 HTTP 요청을 받는다.
3. Agent가 자연어를 통계 검색용 표현으로 구조화한다.
4. 통계언어 사전의 규칙 검색, 벡터 검색, 리랭킹이 후보 통계표를 만든다.
5. 모호하면 통계표를 임의 선택하지 않고 역질문 상태를 반환한다.
6. 명확하면 사전에 등록된 ID만 사용해 KOSIS API plan을 만든다.
7. 사용자가 기간과 그래프 방식을 확정하면 MCP 호환 Gateway가 `StatisticsService`를 호출한다.
8. `StatisticsService`가 KOSIS에서 수치를 가져온다.
9. Agent API가 행 데이터를 그래프 계열로 변환한다.
10. UI가 요약, 그래프, 표, 데이터 출처, 데이터 계보를 출력한다.

중요한 구조적 사실은 다음과 같다.

- HCX-003은 자연어 구조화만 담당한다. `table_id`, `itmId`, `objL` ID 또는 통계 수치를 생성할 권한이 없다.
- 실제 ID는 `stat_language_dictionary.json`과 로컬 MCP 메타데이터에 이미 존재하는 값에서만 선택된다.
- 벡터 검색 결과의 metadata도 사전의 `tables_by_id`와 교집합을 통과해야 한다.
- UI의 Agent API는 요청마다 별도 MCP stdio 프로세스를 띄우지 않는다. 동일한 `StatisticsService`를 감싼 `McpToolGateway`를 인프로세스로 호출한다.
- 별도로 실행되는 MCP stdio 서버도 같은 `StatisticsService`를 사용하므로 외부 MCP 클라이언트와 UI Agent의 핵심 데이터 계약은 같다.

---

## 2. 실행 프로세스 구조

`START_STATBRIDGE.cmd`가 다음 세 프로세스를 실행한다.

```text
START_STATBRIDGE.cmd
├─ Agent API
│  └─ uvicorn bridge_api:app --host 127.0.0.1 --port 8000
├─ MCP stdio server
│  └─ python server.py
└─ Frontend
   └─ vite --host 127.0.0.1 --port 5173 --strictPort
```

시작 전에 실행기는 다음을 수행한다.

- `%~dp0`를 기준으로 패키지 루트를 정한다.
- `runtime_data\processed`를 `STATBRIDGE_DATA_DIR`로 지정한다.
- `runtime_data\tables`를 `STATBRIDGE_TABLES_DIR`로 지정한다.
- 복사된 가상환경을 재사용하지 않고 이 컴퓨터용 `.venv_runtime`을 확인하거나 생성한다.
- Python 의존성, Node.js, frontend 의존성을 확인한다.
- `statbridge_mcp_server\.env`에서 KOSIS/NCP 키가 있는지만 확인한다. 키 값은 화면에 출력하지 않는다.
- 기존 8000/5173 포트 프로세스를 정리한다.
- Agent health와 frontend HTTP 응답을 확인한 뒤 브라우저를 연다.

### 주요 실행 파일

| 역할 | 파일 |
|---|---|
| 통합 실행기 | `START_STATBRIDGE.cmd` |
| Agent 실행 | `runtime_scripts/RUN_AGENT.cmd` |
| MCP 실행 | `runtime_scripts/RUN_MCP.cmd` |
| UI 실행 | `runtime_scripts/RUN_FRONTEND.cmd` |
| React 화면 | `StatBridge-official/src/agent/frontend/src/App.tsx` |
| UI HTTP client | `StatBridge-official/src/agent/frontend/src/api/client.ts` |
| Agent HTTP API | `StatBridge-official/src/agent/bridge_api.py` |
| Agent orchestration | `StatBridge-official/src/agent/statbridge_agent.py` |
| 규칙·사전 검색 | `StatBridge-official/src/agent/stat_dictionary/stat_language_resolver.py` |
| 통계언어 사전 | `StatBridge-official/src/agent/stat_dictionary/stat_language_dictionary.json` |
| Hybrid 검색 | `StatBridge-official/src/agent/hybrid_retriever.py` |
| HCX 분류·답변 | `StatBridge-official/src/agent/ncp_clova_client.py` |
| Embedding·Reranker | `StatBridge-official/src/agent/ncp_retrieval_client.py` |
| MCP 호환 Gateway | `StatBridge-official/src/agent/mcp_gateway.py` |
| 통계 서비스 | `statbridge_mcp_server/statbridge_mcp/statistics_service.py` |
| KOSIS HTTP client | `statbridge_mcp_server/statbridge_mcp/kosis_client.py` |
| 로컬 metadata | `statbridge_mcp_server/statbridge_mcp/metadata_store.py` |

---

## 3. 전체 호출 흐름 한눈에 보기

```text
[사용자]
   │ 질문 입력 / 분석하기
   ▼
[React App.tsx]
   │ submitQuery({query, execute:true})
   ▼
[POST http://127.0.0.1:8000/api/query]
   │ QueryRequest 검증
   ▼
[bridge_api.query]
   │ 최초 요청은 agent.run(..., execute=False)
   ▼
[StatBridgeAgent.resolve]
   ├─ Resolver preflight
   ├─ ambiguity gate / no_match / catalog_only
   ├─ HCX-003 자연어 구조화
   ├─ 비교 계열 분리
   ├─ HybridStatRetriever
   │  ├─ rule score
   │  ├─ query embedding
   │  ├─ Chroma 3 collection 검색
   │  ├─ NCP reranker
   │  └─ score fusion
   ├─ Resolver 최종 안전 판정
   └─ 사전 ID로 ApiPlan 생성
          │
          ├─ need_clarification → 버튼/상태 반환
          ├─ no_match → 빈 결과 반환
          ├─ catalog_only → 수치 실행 차단
          └─ resolved → 기간 입력 요청
                         │
[사용자: 항목·기간·그래프 방식 선택]
                         │
                         ▼
[bridge_api.query 재호출]
   │ 날짜를 KOSIS period로 변환
   ▼
[StatBridgeAgent.execute_resolution]
   ▼
[McpToolGateway.get_statistics]
   ▼
[StatisticsService.get_statistics]
   ├─ metadata 검증
   ├─ API 지원 여부 검증
   ├─ item/classification/frequency/period 정규화
   └─ KOSIS OpenAPI 호출
          ▼
[KOSIS rows]
   ▼
[bridge_api._chart_series]
   ├─ 숫자 변환
   ├─ 계열 그룹화
   ├─ 기간 정렬·중복 제거
   └─ 요약 생성
          ▼
[QueryResponse JSON]
   ▼
[React Results]
   ├─ 답변 요약
   ├─ 통합/분리 그래프
   ├─ 주요 데이터 표
   ├─ 사용한 통계 카드
   └─ 데이터 계보
```

---

## 4. UI에서 텍스트가 처음 처리되는 과정

### 4.1 입력 상태

`App.tsx`는 질문을 React state인 `query`에 보관한다. 입력창의 `onChange`가 사용자가 타이핑할 때마다 이 값을 갱신한다.

```text
input.value → setQuery(e.target.value) → query state
```

분석 버튼은 다음 경우 비활성화된다.

- 요청 처리 중인 경우
- `query.trim()`이 빈 문자열인 경우

### 4.2 최초 제출

폼 제출 시 `onSubmit`은 다음 순서로 작동한다.

1. 브라우저 기본 form submit을 막는다.
2. 공백 질문이면 종료한다.
3. loading을 켠다.
4. 이전 오류, 결과, 기간 선택 상태를 비운다.
5. `submitQuery({query: query.trim(), execute: true})`를 호출한다.

여기서 `execute:true`가 들어가지만 서버는 최초 요청에서 바로 수치를 조회하지 않는다. 먼저 통계표를 확정한 뒤 `need_period`를 반환하여 사용자가 정확한 기간을 고르게 한다.

### 4.3 HTTP 요청

`frontend/src/api/client.ts`의 `submitQuery`가 다음 요청을 보낸다.

```http
POST http://127.0.0.1:8000/api/query
Content-Type: application/json

{
  "query": "사용자 질문",
  "execute": true
}
```

`VITE_API_BASE_URL`이 있으면 해당 주소를 사용하고, 없으면 기본값 `http://127.0.0.1:8000/api`를 사용한다.

### 4.4 연결 실패 처리

HTTP 상태가 성공이 아니거나 fetch 자체가 실패하면 UI는 실제 통계 결과처럼 가장하지 않는다. `localFallback`이 다음 안전 응답을 만든다.

- `status: no_match`
- chart와 tables는 빈 배열
- Agent API 연결 실패 경고
- `START_STATBRIDGE.cmd`로 Agent/MCP를 실행하라는 안내

즉 mock UI가 연결 실패를 실제 KOSIS 결과로 보여주지 않는다. 단, `VITE_USE_MOCK=true`를 명시한 개발 환경에서는 mock 응답을 사용한다.

---

## 5. Agent API가 요청을 받는 과정

### 5.1 `QueryRequest` 구조

FastAPI의 `/api/query`는 다음 필드를 받는다.

| 필드 | 의미 |
|---|---|
| `query` | 사용자의 원문 질문 |
| `state` | 이전 검색·역질문 상태 |
| `clarification` | 구형 단일 선택 `{clarification_id, value}` |
| `selections` | 현재 UI의 복수 선택 그룹 |
| `execute` | 실제 수치 조회 여부 |
| `period_start`, `period_end` | UI의 `YYYY-MM-DD` 기간 |
| `chart_mode` | `combined` 또는 `separate` |

서버는 `payload.query.strip()` 결과가 비면 HTTP 400을 반환한다.

### 5.2 서비스 초기화

`bridge_api.py`가 import될 때 다음 객체가 한 번 만들어진다.

```text
StatisticsService
    ↓
McpToolGateway(service)
    ↓
StatBridgeAgent(service=mcp_gateway)
```

`StatisticsService`가 KOSIS 키 없이 초기화되지 못하면 `_NoKeyClient`를 넣어 metadata 계획 기능은 유지하되 실제 KOSIS 호출은 명시적으로 실패하게 한다.

### 5.3 최초 요청의 핵심 결정

복수 버튼 선택값이 아직 없으면 서버는 다음을 호출한다.

```python
agent.run(
    query=agent_query,
    state=agent_state,
    clarification=agent_clarification,
    execute=False,
)
```

`execute=False`가 중요하다. 최초 요청은 통계표 탐색과 API plan 생성까지만 하고 KOSIS 수치 호출은 보류한다.

---

## 6. Agent 내부 1단계: Resolver preflight

`StatBridgeAgent.resolve()`는 먼저 `StatLanguageResolver.resolve(effective_query)`를 실행한다. 이를 preflight라고 볼 수 있다.

preflight의 목적은 LLM이 질문을 확장하기 전에 다음을 먼저 지키는 것이다.

- “금리”, “대출”, “물가”처럼 여러 통계 개념으로 갈라지는 일상어인지 확인한다.
- 사전에 명시된 ambiguity group이 있으면 점수가 높아도 먼저 역질문한다.
- 지원하지 않는 핵심 지표는 후보 생성을 중단한다.
- 명확한 표명이나 강한 alias가 있으면 불필요한 역질문을 억제한다.
- catalog-only 표인지 확인한다.

### 6.1 후속 질문 결합

`state._user_query`가 있으면 이전 질문과 현재 질문을 `merge_followup_query`로 결합한다.

예를 들어 이전 질문이 수출 금액이고 현재 질문이 “비중으로 바꿔줘”라면, 동일 축의 이전 조건을 제거하고 새 조건을 결합한다. 코드가 관리하는 대표 변경 축은 다음과 같다.

- 수출 ↔ 수입
- 금액 ↔ 비중
- 신규취급액 ↔ 잔액
- 실적 ↔ 전망
- 명목 ↔ 실질
- 대외채무 ↔ 대외채권 ↔ 순대외채권
- 재무상태표 ↔ 손익계산서 등

별도로 `QueryState`는 metric, subject, region, institution, company size, measure basis, valuation basis, frequency, period, comparison, series를 구조화한다.

### 6.2 모호성 그룹

사전 본문과 `clarification_extensions.json`에서 clarification group을 읽는다. 현재 기본 사전에는 11개 group이 있으며 확장 group도 함께 합쳐진다.

각 group은 대체로 다음 정보를 가진다.

- trigger terms
- 이미 명확하면 질문하지 않게 하는 skip terms
- 질문 문장
- 선택지 label/value
- 선택지에 대응하는 표 이름 조건
- confirmed terms

Resolver는 다음 순서로 역질문 필요성을 판단한다.

1. 이미 확정하거나 질문한 group인지 확인한다.
2. trigger가 질문에 있는지 확인한다.
3. skip term이 있는지 확인한다.
4. 선택지 하나가 이미 명시되어 있으면 모호하지 않은 것으로 처리한다.
5. 정확한 통계표명이 질문에 있으면 광범위한 ambiguity gate를 건너뛸 수 있다.
6. 각 선택지가 실제 후보 또는 전체 사전에서 적어도 하나의 표에 연결되는지 확인한다.
7. 유효 선택지가 두 개 이상일 때만 역질문을 만든다.

`collect_clarifications`는 현재 질문에 적용 가능한 group을 반복 수집하되, 실제 사전 검색 결과를 만들 수 없는 선택지는 UI에 노출하지 않는다.

---

## 7. Agent 내부 2단계: HCX-003 자연어 구조화

preflight에서 바로 역질문하지 않고, 규칙만으로 확실한 fast path도 아니면 `_classify()`가 HCX-003을 호출한다.

### 7.1 HCX-003이 받는 입력

- 사용자 원문
- “통계 검색에 유리한 표현으로만 정규화”하라는 system instruction
- ID와 수치 추측 금지
- JSON 객체만 반환하라는 제약

### 7.2 HCX-003 출력 구조

```json
{
  "normalized_query": "검색용 문장",
  "concepts": ["핵심 통계 개념"],
  "subjects": ["대상·부문·지역"],
  "measures": ["잔액·비중·지수·증가율"],
  "time_terms": ["최근 5년·월별·2024년"],
  "comparison_terms": ["전년대비·추이"],
  "qualifiers": ["계절조정·신규취급액·실적"],
  "series": [
    {"label": "화면용 이름", "query": "한 통계표를 찾기 위한 검색어"}
  ]
}
```

단일 지표라면 `series`는 비우고, 명시적으로 2~5개 지표를 비교하면 각 지표를 독립 검색할 수 있도록 series를 만든다.

### 7.3 검색문 확장

Agent는 원문과 다음 필드를 안정적으로 중복 제거해 이어 붙인다.

- normalized query
- concepts
- subjects
- measures
- time terms
- comparison terms
- qualifiers

이 결과가 `dictionary_query`다. HCX 호출이 실패하면 원문을 그대로 사용해 deterministic dictionary가 계속 작동한다.

### 7.4 HCX fast path

preflight Top-1 점수가 150 이상이고 이유에 정확한 `table_name` 또는 `exact_table_phrase`가 있으면 HCX-003을 호출하지 않는다. 이때 classification은 `deterministic_fast_path`로 기록된다.

---

## 8. 통계언어 사전의 내용

현재 사전에는 347개 통계표가 있다. 각 표는 대체로 다음을 가진다.

```text
table_id, table_name, org_id
domains
aliases
semantic_aliases
public_query_terms
keywords
prd_se
period_start_observed, period_end_observed
item_ids, item_names
units
dimensions[]
api_call_params
clarification_tags
```

dimension에는 다음이 들어간다.

- 실제 API parameter: `objL1`~`objL8`
- 분류 이름
- 실제 `value_id`
- 원래 이름과 normalized 이름
- aliases, layman terms, related concepts
- negative terms

이 구조 덕분에 “가계”, “신규취급액”, “비중” 같은 자연어가 실제 분류 ID로 연결될 수 있다. LLM이 ID를 만들지 않고, 이미 사전에 있는 `value_id`가 선택된다.

---

## 9. 규칙 기반 통계표 점수 계산

`StatLanguageResolver._score()`는 347개 표를 순회하며 후보 점수를 계산한다.

### 9.1 검색 전 차단

- 지원하지 않는 핵심 metric이면 빈 후보를 반환한다.
- 사용자가 확정한 조건과 맞지 않는 표는 제외한다.
- metric 의도와 구조적으로 충돌하는 표는 제외한다.

예를 들어 “금리” 요청이 “비중” 표로 가거나, “잔액” 요청이 금리 표로 가는 것을 제한한다.

### 9.2 양의 점수 신호

대표 신호는 다음과 같다.

- 사용자가 버튼으로 확정한 term: 강한 hard anchor
- 공개 자연어 intent rule
- table ID 직접 언급
- 정확한 table name
- 표 이름의 핵심 구문
- alias
- semantic alias
- public query term과 token overlap
- dimension value/alias/layman term 일치
- 요청 frequency 일치
- 계절조정·원계열·말잔·평잔·명목·실질·신규·잔액 등의 qualifier
- 표 이름 lexical token overlap
- table-specific anchor
- 요청 연도가 실제 관측 기간 안에 포함됨
- 최근/최신 질문에서 최신 관측 종료 시점

### 9.3 dimension hit

각 dimension에서 가장 강한 일치 value 하나를 고른다. 후보 결과에는 다음이 남는다.

```json
{
  "api_param": "objL1",
  "value_id": "사전에 저장된 실제 ID",
  "value_name": "분류 이름",
  "matched_term": "질문에서 맞은 말",
  "match_kind": "alias 또는 layman term 등"
}
```

나중에 API plan을 만들 때 이 `dimension_hits`가 대표 기본 분류 ID를 교체한다.

### 9.4 음의 신호와 hard filter

- negative term만 맞고 positive match가 없으면 감점한다.
- 사용자가 요구하지 않은 “지역별” 변형 표는 감점한다.
- 명시한 연도가 표의 관측 범위 밖이면 후보에서 제외한다.
- 질문에 더 구체적인 형제 표 이름이 있는데 현재 표가 더 넓으면 감점한다.
- 요청 frequency와 표 frequency가 맞지 않으면 제외한다.

### 9.5 후보 인정 조건

최소 규칙 점수 기본값은 사전 정책의 25다. 그러나 점수만 있다고 최종 후보로 인정하지 않는다. `resolve()`는 table name, alias, semantic/public intent, token/anchor, confirmed term 또는 충분한 dimension hit 같은 grounded evidence가 없는 후보를 다시 버린다.

---

## 10. 임베딩 데이터가 만들어지는 방법

벡터 색인은 runtime 질문을 받을 때 즉석에서 통계사전 전체를 다시 임베딩하지 않는다. `tools/build_stat_vector_index.py`가 사전을 읽어 미리 세 종류의 문서를 만든다.

### 10.1 `stat_concepts`

한 표의 다음 내용을 묶는다.

- domain
- aliases
- semantic aliases
- public query terms

문서 예시는 다음 형태다.

```text
통계표 [표 이름] | 개념 및 별칭: [개념1]; [별칭1]; ...
```

### 10.2 `stat_tables`

표 자체의 구조를 묶는다.

```text
통계표ID [ID] | 이름 [표 이름] | 주기 [M/Q/Y...] |
항목 [item names] | 단위 [units]
```

### 10.3 `stat_dimensions`

분류 축과 값의 자연어를 묶는다.

- normalized/value name
- aliases
- layman terms
- related concepts

```text
통계표 [표 이름] | 분류값 [dimension]: [값·별칭·일상어...] | ...
```

### 10.4 저장 과정

1. 각 문서의 SHA-256을 계산한다.
2. 기존 `embedding_cache.json`에 같은 문서 hash가 있으면 임베딩을 재사용한다.
3. 없으면 NCP Embedding v2에 문서를 전달한다.
4. ChromaDB cosine collection에 vector, document, metadata를 upsert한다.
5. metadata에는 최소한 `table_id`, `table_name`이 들어간다.

기본 저장 경로는 `StatBridge-official/data/vector_store`이며 collection은 다음 세 개다.

- `stat_concepts`
- `stat_tables`
- `stat_dimensions`

---

## 11. Runtime Hybrid 검색

`HybridStatRetriever.rank()`는 다음 순서로 실행된다.

### 11.1 Rule 후보 생성

먼저 Resolver rule ranking을 최대 `vector_top_k` 이상 생성한다. 기본값은 다음과 같다.

- vector Top-K: 24
- reranker Top-K: 12
- 최종 반환 Top-K: 호출자가 지정하며 Agent는 보통 12

### 11.2 Rule fast path

Top-1 이유가 정확한 table name/exact phrase/confirmed selection이고 다음을 만족하면 벡터와 reranker를 건너뛴다.

- rule Top-1 ≥ 150
- Top-1과 Top-2 차이 ≥ 60

이 후보에는 `retrieval_path=rule_fast_path`가 붙는다.

### 11.3 Query Embedding

fast path가 아니고 vector store와 NCP key가 있으면 확장된 `dictionary_query`를 Embedding v2에 전달한다.

Embedding client는 같은 텍스트를 최대 256개 LRU cache로 재사용한다.

### 11.4 ChromaDB 검색

동일 query vector를 세 collection에 각각 전달한다. 각 결과에서 cosine distance를 다음 점수로 바꾼다.

```text
vector_score = max(0, 1 - distance)
```

여러 collection에서 같은 table이 나오면 가장 높은 vector score와 그 document를 보존한다.

### 11.5 ID 안전 교집합

vector metadata의 `table_id`가 `resolver.tables_by_id`에 없으면 즉시 버린다. 따라서 오래되거나 오염된 vector metadata만으로 새로운 API ID를 만들 수 없다.

### 11.6 Reranker

vector score 상위 12개 table document를 NCP Reranker에 보낸다.

```json
{
  "query": "확장된 질문",
  "documents": [
    {"id": "DT_xxx", "doc": "색인 당시 문서"}
  ]
}
```

Reranker가 돌려준 순위를 1.0에서 아래로 내려가는 점수로 변환한다. 같은 query와 같은 document ID 묶음은 최대 256개 LRU cache로 재사용한다.

### 11.7 Score fusion

규칙과 vector 후보의 합집합을 만든 뒤 다음 계산을 한다.

```text
rule_norm = min(1, rule_score / 220)

final_score =
    0.34 × vector_score
  + 0.28 × reranker_score
  + 0.38 × rule_norm
  + confirmed_bonus
  - negative_penalty
```

confirmed 이유가 있으면 bonus 0.12가 추가된다. 최종 후보는 `final_score` 내림차순, 동점이면 `table_id` 순으로 정렬된다.

### 11.8 Hybrid 장애 시 동작

다음 상황에서는 예외를 사용자에게 그대로 터뜨리지 않고 rule ranking으로 되돌아간다.

- vector store가 없음
- collection을 열 수 없음
- NCP retrieval key가 없음
- embedding/reranker API 오류

단, 결과 trace의 vector/reranker 점수는 실제로 수행되지 않은 경우 0이다.

---

## 12. 단일 지표와 다중 지표의 분기

### 12.1 단일 지표

HCX `series`가 비어 있으면 전체 `dictionary_query`를 한 번 hybrid 검색한다. Resolver가 ambiguity/no-match 여부를 결정한 뒤, resolved 상태라면 hybrid Top-1이 `selected_table`이 된다.

### 12.2 HCX가 2~5개 series를 구조화한 경우

`_resolve_comparison`은 각 series를 독립적으로 검색한다.

```text
series label + series query + 공통 qualifier
        ↓
Hybrid rank
        ↓
각 series Top-1
```

같은 `table_id`가 두 series에 중복 선택되면 하나를 성공으로 가장하지 않고 해당 series를 missing으로 처리한다. 하나라도 찾지 못하면 전체 결과는 `no_match`와 `missing_series`가 된다.

모두 찾으면 각 table에 대해 별도의 API plan을 만들고 최대 5개 계열을 반환한다.

### 12.3 Deterministic multi fallback

HCX가 series를 만들지 않았더라도 질문에 명시된 비교 표현이 있으면 `rank_many`가 문장을 분리하고 각 부분을 독립 검색한다. 이 경로도 사전 후보만 사용한다.

---

## 13. 역질문과 복수 선택 처리

### 13.1 `need_clarification` 응답

모호성 group이 남으면 Agent는 다음을 반환한다.

- `clarification_id`
- 기본 question
- 실제 사전 후보에 연결되는 options
- `state.original_query`
- `state.confirmed`
- `state.asked_clarifications`
- candidate tables
- HCX classification

HCX-007은 선택지를 생성하지 않는다. 사전이 만든 선택지를 변경하지 않고 질문 문장을 자연스럽게 다듬는 역할만 한다. HCX-007 실패 시 사전 기본 질문을 그대로 쓴다.

### 13.2 한 화면의 복수 선택 UI

`ClarificationPanel`은 모든 clarification group을 한 화면에 보여준다. 각 선택지는 toggle 방식이라 여러 값을 고를 수 있다.

동시에 다음도 한 화면에서 받는다.

- 항목 선택
- 시작일
- 종료일
- 여러 계열을 한 그래프에 표시할지 여부

선택이 바뀌면 UI는 120ms debounce 후 `execute:false` preview 요청을 보내 선택 조합에서 가능한 공통 기간을 갱신한다.

### 13.3 선택값 재검색 최적화

복수 선택 제출 시 `bridge_api._resolve_selected_options()`가 동작한다.

1. 각 group의 선택값 조합을 만든다.
2. 조합은 최대 5개로 제한한다.
3. 이전 state의 `confirmed`에 선택값을 추가한다.
4. `agent.resolve(query, state=confirmed state)`를 실행한다.
5. 동일 table/item/classification 조합은 중복 제거한다.

이 경로는 “두 번째 HCX/vector 전체 round-trip 없이” confirmed hard constraint로 재검색하도록 설계됐다.

### 13.4 잘못된 버튼 방지

`collect_clarifications`가 각 option을 probe 검색하고, 독립 또는 다른 group 선택과 결합했을 때 실제 후보를 만들 수 없는 option은 제거한다. 즉 지원 데이터가 없는 지표를 버튼으로 먼저 보여주는 것을 막는다.

---

## 14. 최종 상태 결정

통계표 탐색 결과는 다음 네 가지 핵심 상태로 나뉜다.

### `need_clarification`

통계 개념이 모호하며 사용자의 선택이 필요하다. UI는 버튼 화면을 표시한다.

### `catalog_only`

표는 카탈로그에 있지만 현재 일반 Parameter OpenAPI 방식으로 안전하게 수치 조회할 수 없다. 수치 호출을 중단한다.

### `no_match`

grounded candidate가 없거나, 다중 지표 중 일부를 찾지 못했다. UI는 빈 graph/table과 missing series 안내를 보여준다.

### `resolved`

실제 사전의 table이 확정됐다. 이 상태에서 API plan을 만든다.

---

## 15. API plan 생성

`build_api_plan()`은 선택된 candidate의 `table_id`로 사전 원본 table을 다시 찾는다. 후보 dictionary 밖의 ID는 이 단계에 들어올 수 없다.

### 15.1 기본 필드

- `table_id`: 선택한 사전 표
- `table_name`: 사전 표 이름
- `org_id`: 사전/API params의 기관 ID, 기본 301
- `item_id`: 사전 `itmId` 또는 첫 번째 `item_ids`, 없으면 ALL
- `frequency`: `prdSe` 또는 표 주기
- start/end period
- classifications
- exact KOSIS params

### 15.2 분류 ID 결정

각 dimension의 첫 번째 검증 값 ID를 기본 대표값으로 놓는다. 질문에서 찾은 `dimension_hits`가 있으면 해당 `objL1`~`objL8` 값을 실제 hit의 `value_id`로 교체한다.

허용되는 parameter 이름도 정규식 `objL[1-8]`을 통과해야 한다.

### 15.3 기간 기본값

질문에 명시적 연도가 있으면 해당 연도 범위를 사용한다. “이후/부터”가 있으면 표의 최신 관측 종료까지 확장한다.

“최근 N년/개월/분기”가 있으면 frequency에 맞게 역산한다.

기간 표현이 없으면 과도한 전체표 호출을 막기 위해 trend를 볼 수 있는 안전 기본 범위를 사용한다.

- 월: 최근 12개월
- 분기: 최근 8분기
- 연/년간: 최근 5년

그러나 UI는 실제 수치 실행 전에 사용자가 정확한 기간을 다시 선택하게 한다.

### 15.4 exact params

```json
{
  "method": "getList",
  "format": "json",
  "jsonVD": "Y",
  "smblChk": "Y",
  "orgId": "301",
  "tblId": "사전의 table_id",
  "itmId": "사전의 item_id",
  "prdSe": "M/Q/Y...",
  "startPrdDe": "정규화된 시작 기간",
  "endPrdDe": "정규화된 종료 기간",
  "objL1": "사전의 분류값 ID"
}
```

---

## 16. 기간 선택 단계

통계표는 찾았지만 UI 요청에 날짜가 없으면 `bridge_api`는 수치를 실행하지 않고 `need_period`를 반환한다.

### 16.1 선택 가능 범위

각 plan의 사전 `period_start_observed`와 `period_end_observed`를 HTML date 범위로 바꾼다.

다중 표라면 모든 표가 동시에 제공되는 교집합을 사용한다.

```text
available min = 각 표 시작일 중 가장 늦은 값
available max = 각 표 종료일 중 가장 이른 값
```

UI date input의 min/max에 이 값을 넣으므로 원자료가 없는 기간은 선택하지 못한다.

### 16.2 UI 날짜 → KOSIS 기간

사용자가 `YYYY-MM-DD`를 고르면 `_api_period()`가 frequency별로 변환한다.

- 일: `YYYYMMDD`
- 월: `YYYYMM`
- 분기: `YYYY01`~`YYYY04`
- 반기: `YYYY01`~`YYYY02`
- 연: `YYYY`

사용자 범위가 표 범위를 벗어나면 공통 제공 범위로 조정하고 warning을 남긴다. 시작이 종료보다 늦거나 공통 기간이 없으면 HTTP 400으로 중단한다.

---

## 17. MCP 호환 실행 구조

기간이 확정되면 `agent.execute_resolution()`이 이미 만든 plan을 실행한다. 이 함수는 HCX 분류와 vector 검색을 다시 하지 않는다.

```text
Agent.execute_resolution
    ↓
McpToolGateway.get_statistics
    ↓
StatisticsService.get_statistics
```

### 왜 “MCP 호환 Gateway”인가

별도 MCP stdio 서버의 public tool도 결국 `StatisticsService.get_statistics()`를 호출한다. UI Agent는 같은 PC 안에서 같은 Python service를 이미 가지고 있으므로, HTTP 요청마다 stdio MCP subprocess를 거치지 않고 Gateway가 같은 계약을 직접 호출한다.

따라서 다음 둘은 transport만 다르다.

```text
외부 MCP client → MCP stdio tool → StatisticsService
UI Agent       → McpToolGateway → StatisticsService
```

### UI 실행의 안전 설정

Agent는 실제 실행 시 다음을 명시한다.

- `prefer_local=False`
- `allow_fallback=False`

즉 UI는 오래된 local CSV를 KOSIS 결과처럼 자동 혼합하지 않고, 확정된 첫 API parameter 조합으로 KOSIS를 호출한다.

---

## 18. StatisticsService가 API plan을 실행하는 과정

### 18.1 table metadata 확인

`MetadataStore`는 시작 시 다음 CSV를 문자열 dtype으로 읽어 table별 index를 만든다.

- `bok_table_master.csv`
- `bok_items.csv`
- `bok_classifications.csv`
- `bok_periods.csv`
- `bok_comments.csv`
- `bok_sources.csv`

table ID가 metadata store에 없으면 즉시 오류다.

### 18.2 API 지원 여부

metadata의 `api_status`가 `SUPPORTED`가 아니면 KOSIS 일반 Parameter OpenAPI를 호출하지 않고 예외 상태와 빈 rows를 반환한다.

### 18.3 metadata와 기간 정규화

- item, dimensions, frequency, period metadata를 읽는다.
- start/end가 명시되어 있으면 불필요한 live period metadata round-trip을 생략한다.
- frequency를 canonical 값으로 바꾼다.
- 기간이 비어 있을 때만 metadata의 min/max를 사용한다.
- classifications가 아예 없으면 depth에 맞는 `objL=ALL` 기본값을 만든다.

### 18.4 KOSIS 호출

`KosisClient.get_statistics()`는 다음 endpoint를 사용한다.

```text
https://kosis.kr/openapi/Param/statisticsParameterData.do
```

API key는 `.env`에서 읽고 URL 결과나 UI debug에 노출하지 않는다. 호출 parameter는 plan의 org/table/item/frequency/period/classification이다.

rate limiter가 호출 간격과 분당 호출 수를 제한하고, HTTP 응답을 JSON으로 파싱한다. KOSIS가 `err` 객체를 반환하면 `KosisApiError`로 처리한다.

### 18.5 성공 결과

성공 시 `StatisticsService`는 다음 구조를 반환한다.

```json
{
  "source": "kosis_api",
  "status": "success",
  "table_id": "...",
  "table_name": "...",
  "used_params": {},
  "row_count": 12,
  "rows": [
    {
      "PRD_DE": "202501",
      "DT": "수치",
      "ITM_ID": "...",
      "ITM_NM": "...",
      "C1": "...",
      "C1_NM": "...",
      "UNIT_NM": "..."
    }
  ]
}
```

다중 plan이면 Agent가 각 결과 row에 `_SERIES_LABEL`을 추가하고 한 execution에 합친다.

### 18.6 오류 결과

KOSIS 실행 중 오류가 나도 이미 확정된 plan은 버리지 않는다. Agent는 다음을 반환한다.

- `execution.status=error`
- 오류 메시지
- 빈 rows
- 원래 selected table과 API plan

따라서 UI/debugger가 “어떤 요청을 보내려 했는지” 확인할 수 있다.

---

## 19. KOSIS 행을 그래프로 변환하는 과정

`bridge_api._chart_series()`가 execution rows를 UI chart 구조로 바꾼다.

### 19.1 숫자 검증

`DT` 값을 comma 제거 후 `float`로 바꿀 수 있는 행만 사용한다. 숫자로 변환할 수 없는 값은 graph에 넣지 않는다.

### 19.2 기간 검증

`PRD_DE`가 빈 행은 제외한다.

### 19.3 계열 이름

우선순위는 다음과 같다.

1. 다중 비교 plan이 붙인 `_SERIES_LABEL`
2. `ITM_NM`
3. C1_NM~C8_NM 중 전체/계/합계가 아닌 분류명

unit은 `UNIT_NM`에서 가져온다.

### 19.4 중복과 정렬

같은 계열·단위에서 동일 기간이 여러 번 나오면 기간 key로 하나만 남긴다. 이후 기간 문자열 오름차순으로 정렬한다.

### 19.5 UI series 구조

```json
{
  "id": "series-1",
  "label": "표시 이름",
  "unit": "%",
  "color": "#4568ff",
  "points": [
    {"date": "202501", "value": 3.2}
  ]
}
```

최대 6개 색상을 순환 사용한다.

---

## 20. 답변 요약 생성

현재 UI query 경로는 `generate_answer=False`로 `execute_resolution()`을 호출한다. 따라서 실제 화면의 기본 요약은 HCX-007 장문 답변이 아니라 `_chart_summary()`의 deterministic 요약이다.

요약은 첫 계열의 첫 값과 마지막 값을 비교해 다음 중 하나로 표현한다.

- 증가
- 감소
- 같은 수준 유지

다중 계열이면 총 계열 수를 덧붙인다.

`NcpClovaClient.answer_with_data()` 기능도 구현되어 있지만, 그것은 `generate_answer=True`이고 `STATBRIDGE_GENERATE_NARRATIVE`가 활성화된 다른 실행 경로에서만 사용된다. 이 경우에도 최대 60개 실제 조회 행과 API plan만 근거로 HCX-007에 전달하며, 없는 수치나 원인을 추측하지 말라고 제한한다.

---

## 21. 그래프 표시 방식 분기

실행 결과가 성공이고 실제 chart series가 두 개 이상인데 `chart_mode`가 없으면 서버는 `need_chart_mode`를 반환한다.

UI는 다음 중 하나를 선택하게 한다.

- `combined`: 여러 계열을 한 그래프에
- `separate`: 계열별 그래프 여러 개

현재 clarification 화면은 항목·기간·chart mode를 한 번에 받으므로 일반적으로 그 단계에서 이미 mode가 전달된다.

---

## 22. 최종 QueryResponse와 UI 출력

최종 resolved 응답은 다음 핵심 필드를 가진다.

```text
status
query
interpretedQuery
summary
period
availablePeriod
frequency
chart[]
chartMode
tables[]
insights[]
lineage[]
warnings[]
state
debug
```

### 22.1 화면 왼쪽 결과 영역

- 원래 질문
- 간단한 답변 요약
- 시계열 graph
- legend의 계열명과 단위
- 선택 기간과 frequency
- 핵심 인사이트
- 계열/시점/값 데이터 표
- CSV 다운로드

`chartMode=separate`면 series마다 별도 `LineChart`를 만들고, 아니면 모든 series를 한 `LineChart`에 전달한다.

### 22.2 사용한 통계 카드

각 API plan의 table ID로 metadata를 조회해 다음을 보여준다.

- table ID
- table name
- source
- 대표 item
- unit

표가 3개면 cards도 plan 기준으로 3개 생성된다.

### 22.3 데이터 계보

UI는 서버가 만든 lineage를 그대로 시간 순서로 표시한다.

```text
UI 자연어 수신
→ HCX-003 자연어 → 통계언어
→ 통계언어 사전 검색
→ HCX-007 메인 Agent
→ MCP 통계 서비스
→ KOSIS API
```

lineage는 설명용 문자열이며 실제 debug에는 selected table, API plan, execution status, row preview, classification, dictionary query, 모델명, 단계별 시간이 별도로 포함된다.

### 22.4 CSV 다운로드

UI는 서버를 다시 호출하지 않고 현재 `result.chart`의 데이터를 다음 열로 직렬화한다.

```text
계열, 단위, 시점, 값
```

---

## 23. 데이터 계보 화면의 카탈로그 흐름

사이드바의 데이터 계보 화면은 `/api/catalog`를 호출한다. 이 경로는 질문 검색과 별개로 다음을 수행한다.

1. `StatisticsService.store.available_supported_tables()`에서 실제 지원 metadata 표를 가져온다.
2. 사전 347개 중 지원 table ID와 교집합만 남긴다.
3. domain을 대분류·중분류로 그룹화한다.
4. 각 table을 카드로 바꾼다.

카드에는 다음을 공개한다.

- organization
- frequency와 사람용 주기 이름
- unit scale
- 관측 시작/종료 기간
- item names
- units
- dimension의 사람용 이름, 값 개수, 예시

내부 `objL1` 같은 API parameter 이름이나 긴 호출 URL은 사용자 카드에 노출하지 않는다.

---

## 24. State가 왕복하는 이유

UI는 역질문·기간 선택·그래프 방식 선택 때 서버가 반환한 `state`를 다시 보낸다.

state에는 다음이 포함될 수 있다.

- original query 또는 확장 dictionary query
- 사용자가 확정한 clarification 값
- 이미 물어본 clarification IDs
- candidate table 요약
- 원래 사용자 query
- HCX classification

이를 통해 후속 요청에서 다음을 반복하지 않는다.

- 이미 확정한 조건을 잃는 것
- 같은 역질문을 다시 하는 것
- 기간만 바꿨는데 metric부터 다시 추론하는 것
- 실행 단계에서 HCX/vector 검색을 다시 하는 것

---

## 25. Cache와 속도 최적화 지점

현재 흐름에는 다음 cache/fast path가 있다.

- Resolver `_score_cache`: 동일 query/confirmed/top-k 결과, 최대 512 근처에서 clear
- HCX classification LRU: 최대 256
- Embedding query LRU: 최대 256
- Reranker query+document IDs LRU: 최대 256
- Chroma collection 객체 lazy load 후 재사용
- 정확한 표명/confirmed 선택의 rule fast path
- 선택 버튼 처리 시 두 번째 HCX/vector round-trip 생략
- 기간이 명시되면 live period metadata round-trip 생략
- 기간/그래프 변경 시 이미 확정된 resolution 실행
- Agent API와 MCP service 사이 subprocess 생성 생략

가장 비용이 큰 일반 경로는 대략 다음이다.

```text
HCX-003 classification
+ Query Embedding
+ 3개 Chroma collection 검색
+ Reranker
+ KOSIS HTTP
```

정확한 표명 fast path나 cache hit에서는 앞부분 호출이 상당수 생략된다.

---

## 26. 오류와 안전 fallback

| 실패 지점 | 동작 |
|---|---|
| UI → Agent API 연결 실패 | 실제 결과 없이 안전 no-match 안내 |
| HCX-003 실패 | 원문으로 deterministic dictionary 검색 계속 |
| Embedding/Reranker 실패 | rule ranking으로 fallback |
| HCX-007 역질문 문장 생성 실패 | 사전 기본 질문 사용 |
| unsupported metric | 후보를 만들지 않음 |
| 일부 multi series miss | 전체를 성공 처리하지 않고 missing series 반환 |
| catalog-only table | 수치 호출 차단 |
| 기간 교집합 없음 | HTTP 400 |
| KOSIS 호출 실패 | plan 보존, execution error, 빈 chart |
| 숫자가 아닌 DT | graph에서 제외 |

---

## 27. 핵심 안전 원칙

### 27.1 LLM이 ID를 만들지 않는다

HCX-003 prompt에 ID 생성 금지가 명시돼 있고, 그 출력 schema 자체에도 ID 필드가 없다.

### 27.2 vector 결과도 바로 API가 되지 않는다

vector metadata table ID는 반드시 현재 사전 ID와 교차 검증한다. 최종 plan 생성 시에도 `tables_by_id[table_id]`에서 원본 사전 레코드를 다시 읽는다.

### 27.3 사용자가 모호하면 질문한다

clarification group은 score dominance보다 먼저 적용된다. 즉 후보 하나가 조금 높다는 이유로 “금리”의 종류를 임의 확정하지 않는다.

### 27.4 숫자는 KOSIS row에서만 graph가 된다

UI chart value는 execution row의 `DT`를 숫자로 변환한 값이다. HCX classification이나 reranker 출력은 숫자 데이터가 될 수 없다.

### 27.5 UI 실행은 KOSIS를 기본으로 한다

Agent 실행은 `prefer_local=False`, `allow_fallback=False`다. local CSV와 live KOSIS를 조용히 혼합하지 않는다.

---

## 28. 실제 예시 흐름

사용자가 다음을 입력했다고 가정한다.

```text
2021년부터 예금금리와 대출금리를 비교해줘
```

실행 흐름은 다음과 같다.

1. React가 query를 trim하고 `/api/query`에 보낸다.
2. Agent가 Resolver preflight를 실행한다.
3. 질문에 “예금금리”, “대출금리”가 명시되어 있으므로 광범위한 “금리 종류” 역질문은 불필요할 수 있다.
4. HCX-003이 수신금리와 대출금리의 두 series를 구조화한다.
5. 각 series query가 독립적으로 hybrid 검색된다.
6. Rule은 alias, table tokens, 신규취급액/잔액 등의 qualifier를 평가한다.
7. Query embedding이 concept/table/dimension collection에서 후보를 찾는다.
8. Reranker가 vector 상위 후보를 재정렬한다.
9. 두 series가 서로 다른 유효 table로 확정된다.
10. 각 table의 사전 항목·dimension ID로 API plan 두 개를 만든다.
11. 서버가 두 표의 공통 제공 기간을 계산해 `need_period`를 반환한다.
12. 사용자가 시작·종료일과 combined/separate를 선택한다.
13. UI가 같은 state와 선택값으로 다시 요청한다.
14. 날짜가 각 표 frequency의 KOSIS period로 변환된다.
15. `execute_resolution`이 두 plan을 차례로 MCP Gateway에 전달한다.
16. StatisticsService가 metadata를 검증하고 KOSIS를 두 번 호출한다.
17. 각 row에 series label을 붙여 합친다.
18. `_chart_series`가 두 graph series로 그룹화한다.
19. `_chart_summary`가 첫 계열의 시작/마지막 값과 총 계열 수를 설명한다.
20. UI가 graph, 데이터 표, 통계표 카드 두 개, lineage를 출력한다.

---

## 29. 한 문장으로 정리한 현재 구조

StatBridge1은 사용자의 자연어를 HCX-003으로 검색 표현에 맞게 구조화하되, 실제 통계표·항목·분류 ID는 347개 통계언어 사전의 규칙 검색과 사전에서 생성한 세 종류의 embedding 색인, reranker 결과를 결합해 선택하고, 모호한 경우 사용자 버튼 선택으로 확정한 뒤 동일한 MCP `StatisticsService` 계약을 통해 KOSIS 원자료를 조회하여 UI graph와 데이터 계보로 변환하는 구조다.

---

## 30. 현재 코드를 읽을 때 혼동하기 쉬운 구현상 세부사항

다음은 설계 설명과 실제 runtime 동작을 구분하기 위해 반드시 알아야 하는 사항이다.

1. Resolver는 `clearly_resolved`를 계산하지만 현재 반환 분기에서는 이 변수를 직접 사용하지 않는다. 실제로는 ambiguity group이 있으면 역질문하고, 없으면 grounded candidate 존재 여부로 resolved/no-match를 정한다.
2. Hybrid의 `confident()` 결과는 `retrieval_confident`에 기록되지만 현재 Agent가 resolved 결과를 거부하는 최종 gate로 사용하지 않는다.
3. UI가 최초 요청에 `execute:true`를 보내도 `bridge_api`는 table 탐색 단계에서 `agent.run(..., execute=False)`를 호출한다. 실제 KOSIS 실행은 날짜가 들어온 후에만 일어난다.
4. `START_STATBRIDGE.cmd`가 MCP stdio 서버를 별도 실행하지만 UI HTTP 요청은 그 프로세스와 stdio 통신하지 않는다. UI는 같은 `StatisticsService`를 감싼 인프로세스 Gateway를 사용한다.
5. `StatisticsService` 자체는 `prefer_local=True` 기본값과 API fallback 기능을 지원하지만, UI Agent의 `execute_resolution`은 둘 다 비활성화하고 확정된 KOSIS 호출 하나만 실행한다.
6. HCX-007에는 실제 데이터 기반 narrative 생성 기능이 있지만 현재 `/api/query`의 graph 실행은 `generate_answer=False`다. 화면 summary는 deterministic `_chart_summary` 결과다.
7. `QueryResponse.debug`에는 내부 plan과 row preview가 포함되지만 현재 `Results` 화면의 기본 본문은 이를 모두 그대로 노출하지 않는다. 화면에는 summary, chart, tables, insights, lineage 중심으로 표시한다.
