# StatBridge MCP Server (KOSIS 한국은행 347개 대응)

이 패키지는 **한국은행 KOSIS 통계표 347개**를 대상으로 MCP 서버를 로컬에서 실행하기 위한 최소 동작 버전입니다.

핵심 목표:

- `search_tables`: 자연어/키워드로 통계표 검색
- `get_meta`: 통계표 메타데이터 조회
- `fetch_data`: 실제 통계 데이터 조회
- **fallback 구조**:
  1. 로컬 CSV 캐시(`data_full/tables`) 우선
  2. 없으면 KOSIS OpenAPI 호출
  3. 파라미터 불일치 시 `ALL` 중심 재시도

> 범위: 현재 수집이 확인된 **347개 지원 표** 기준

---

## 1) 준비 파일

아래 CSV 파일이 필요합니다.

- `data/processed/bok_table_master.csv`
- `data/processed/bok_items.csv`
- `data/processed/bok_classifications.csv`
- `data/processed/bok_periods.csv`
- `data/processed/bok_comments.csv` (선택)
- `data/processed/bok_sources.csv` (선택)
- `data_full/tables/*.csv` (있으면 로컬 우선 조회)

> 기존 `kosis_bok_metadata_collector` 산출물을 그대로 연결하면 됩니다.

---

## 2) 설치

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

`.env`에서 `KOSIS_API_KEY`를 채웁니다.

---

## 3) 로컬 실행

### MCP Inspector로 개발 테스트

```powershell
.\.venv\Scripts\python.exe -m mcp dev .\server.py
```

### 일반 stdio 실행

```powershell
.\.venv\Scripts\python.exe .\server.py
```

---

## 4) 주요 MCP Tool

### `search_tables`
입력 예시:

```json
{
  "nl_query": "경제 분위기",
  "top_k": 5
}
```

### `get_meta`
입력 예시:

```json
{
  "table_id": "DT_513Y001"
}
```

### `fetch_data`
입력 예시:

```json
{
  "table_id": "DT_513Y001",
  "item_id": "ALL",
  "classifications": {"objL1": "ALL"},
  "frequency": "M",
  "start_period": "202001",
  "end_period": "202412",
  "prefer_local": true
}
```

---

## 5) 폴백 전략

`fetch_data`는 다음 순서로 동작합니다.

1. 로컬 캐시 CSV 존재 시 필터 조회
2. 없거나 결과가 비면 KOSIS OpenAPI 호출
3. KOSIS 호출 실패 시 다음 순서로 재시도
   - 주어진 `item_id`, 주어진 `objL*`
   - `item_id=ALL`, 주어진 `objL*`
   - 주어진 `item_id`, `objL1=ALL` (나머지 미전송)
   - `item_id=ALL`, `objL1=ALL` (나머지 미전송)

주의:
- **사용하지 않는 `objL2~objL8`은 빈 문자열로 보내지 않음**
- 메타데이터가 없는 2개 예외표는 현재 범위에서 제외됨

---

## 6) 권장 다음 단계

1. `bok_stat_dictionary.csv` 추가
2. `search_tables`에 alias/dictionary boost 적용
3. embedding 검색 추가
4. query planner 추가
5. streamable-http 배포 전환



## v3 변경사항

- `bok_periods.csv`에 해당 표의 행이 없을 때 `bok_table_master.csv`의
  `FREQUENCIES`, `START_PERIOD`, `END_PERIOD`를 fallback으로 사용
- `PRD_SE`와 `PRD_DE`가 서로 다른 행으로 내려오는 KOSIS 구조를 stateful parsing
- 주기 정규화: `월→M`, `분기→Q`, `년/연→Y`, `반기→S`
- 기간 정규화: `2003.01→200301`, `1994 4/4→199404`


## v4 추가 기능

- `get_meta`:
  - 로컬 기간 정보가 비어 있으면 KOSIS PRD 메타 API 실시간 fallback
- `fetch_data`:
  - 분류 깊이 자동 계산
  - 실제 item/class 조합까지 fallback 후보에 포함
- `validate_all_tables(limit=0, prefer_local=False)`:
  - 347개 표를 최신 1개 시점씩 smoke test
  - `limit=10`으로 먼저 일부 테스트 권장

### 권장 테스트 순서

1. `validate_all_tables(limit=10, prefer_local=false)`
2. 성공률 확인
3. `limit=50`
4. 마지막에 `limit=0` 전체 347개


## v5 - Inspector timeout 대응

347개 전체 API 검증은 수분 이상 걸릴 수 있어 MCP Inspector의 단일 Tool 호출 시간 제한을 넘을 수 있습니다.
따라서 `validate_tables_batch`를 사용합니다.

예:

```json
{
  "offset": 0,
  "limit": 20,
  "prefer_local": false,
  "include_success_results": true
}
```

응답의 `next_offset` 값을 다음 호출의 `offset`으로 사용합니다.

예:
- 1차: offset 0, limit 20
- 2차: offset 20, limit 20
- ...
- `has_more=false`가 되면 종료

실패 표만 빠르게 확인하고 싶으면:

```json
{
  "offset": 0,
  "limit": 20,
  "prefer_local": false,
  "include_success_results": false
}
```

이 경우 성공 상세행은 생략되고 실패 결과만 `results`에 남습니다.
