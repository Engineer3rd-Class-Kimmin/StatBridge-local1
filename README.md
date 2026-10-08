# StatBridge

현재 작업 저장소는 [Engineer3rd-Class-Kimmin/StatBridge-local1](https://github.com/Engineer3rd-Class-Kimmin/StatBridge-local1)이다. Windows에서는 `scripts/windows/START_STATBRIDGE.cmd`로 실행한다. 통합 HTTP 실행 모듈은 `src/agent/agent_runtime.py`이며 기존 `statbridge_agent/` 패키지도 보존한다.

오늘의 출력 에이전트·Jev 패치·평가 기록은 [통합 작업 요약](docs/운영/2026-10-01-통합-작업-요약.md), 저장소 이전 과정은 [저장소 이전 기록](docs/운영/2026-10-01-저장소-이전.md)에 정리했다.

한국은행 통계표를 자연어로 탐색하고, 선택한 계열의 관측값과 출처를 확인하는 로컬 애플리케이션입니다.

기여·브랜치·리뷰·검증 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를, 에이전트용 요약 규칙은 [AGENTS.md](AGENTS.md)를 참고하세요.

## 코드와 데이터

| 위치 | 역할 |
|---|---|
| `src/backend/statbridge_mcp/` | 통계표 검색, 메타데이터, KOSIS·로컬 CSV 조회, MCP 도구 |
| `src/agent/bridge_api.py` | 화면용 FastAPI (`/api/query`, `/api/catalog`, `/api/health`) |
| `src/agent/agent_runtime.py`, `src/agent/stat_dictionary/` | 질의 해석, 역질문, 통계표·계열 선택 |
| `src/agent/frontend/` | React 화면 |
| `data/processed/` | 한국은행 349개 지원 표의 목록·항목·분류·기간 메타데이터 |
| `data/tables/` | 별도 원본에서 공급하는 347개 통계표 CSV; Git에 포함하지 않음 |
| `eval/table-discovery/` | 질의 해석~통계표 탐색 평가 v1·v2·v3·v4.1 |
| `eval/end-to-end/` | 별도 계열의 전체 흐름 평가 v1·v2 초안 |

화면의 주 API는 `src/agent/bridge_api.py`입니다. `src/backend/query_api.py`도 같은 앱을 실행합니다. 이전 `/api/analyze` 계약은 제공하지 않으므로 해당 클라이언트는 새 `/api/query` 응답에 맞춰 수정해야 합니다. `analysis_service.py`는 기존 평가 도구 참고용으로 남아 있습니다.

## 설치와 실행

### Windows: 다운로드 후 더블클릭

GitHub의 **Code → Download ZIP**으로 받은 파일을 먼저 압축 해제한 뒤, scripts/windows 폴더의 **`scripts/windows/START_STATBRIDGE.cmd`**를 더블클릭합니다. ZIP 내부에서 직접 실행하지 마세요.

런처가 Python·Node.js·가상환경·패키지를 검사하고 필요한 환경을 준비합니다. API 키가 없으면 `.env`를 만들고 메모장을 열어 KOSIS·CLOVA 키 입력을 안내합니다. 키 자체는 배포하지 않습니다.

첫 실행에는 349개 표의 벡터 인덱스도 생성합니다. 최대 1,047개 문서의 Embedding v2 API 호출과 패키지 다운로드로 시간이 걸리며 API 사용량이 발생합니다. 다음 실행에는 인덱스 내용과 사전의 일치 여부를 확인하고 기존 캐시를 재사용합니다. 인터넷 연결과 해당 API 사용 권한이 필요합니다.

준비가 끝나면 Agent API·MCP·UI를 실행하고 브라우저를 엽니다. 종료는 `scripts/windows/STOP_STATBRIDGE.cmd`로 합니다. 다른 프로그램이 8000/5173 포트를 사용하면 강제로 종료하지 않고 안내합니다. 실행 로그는 Git에 포함하지 않는 `.venv/cache/evaluation_runs/`에 남습니다. Python/Node 자동 설치는 Windows Package Manager(`winget`)가 필요하며 설치 정책·관리자 권한에 막히면 화면의 안내대로 설치한 뒤 다시 실행합니다.

기존 설치에서 업데이트한 경우에도 런처가 Python Plotly와 프런트 `plotly.js-dist-min`을 검사해 빠진 출력 의존성을 설치합니다. 루트에 별도 CMD를 만들지 않으며 위 실행 경로를 사용합니다.

### 그래프 출력과 수정

10월 6일 팀원 작업과 로컬 후속 패치를 [통합 기록](docs/implementation/2026-10-06-chart-editing-integration.md)에 정리했습니다. 정본 경로와 기존 Windows 런처를 유지합니다.

입력 UI에서 질의·통계표·기간을 확정하면 `/api/query`가 MCP 데이터를 조회하고 `need_output_config`와 `outputSessionId`를 반환합니다. 화면에서 그래프 종류, 제목·축·범례, 자연어 요청을 선택한 뒤 `/api/output`으로 `session_ids`, `chart_type`, `chart_mode`, `natural_language` 등을 전달합니다. LangGraph의 출력 단계가 별도 `OutputAgent`를 호출하며, 화면은 응답의 `outputSpec.plotlyFigure`를 Plotly로 표시합니다.

`outputSpec.chartState`는 검증된 그래프 상태이고 `editSessionId`는 수정 세션입니다. `/api/output/edit`에 `edit_session_id`와 `instruction`을 보내면 기존 조회 데이터에 자연어 편집을 적용합니다. 이때 KOSIS 자료를 다시 조회하지 않습니다. 자연어 수정에는 CLOVA 키가 필요하며, 잘못된 그래프 요청은 HTTP 422의 `detail`로 표시합니다. 수정 세션은 최대 100개로 제한하고 초과 시 가장 먼저 만들어진 세션을 제거합니다. 서버를 재시작하면 세션은 사라집니다.

선·막대·누적 막대·영역·산점·버블·파이·도넛·히스토그램·박스·히트맵·트리맵·워터폴을 지원합니다. 계열 수와 단위 등 조건을 만족하지 못하는 명시적 선택은 오류로 안내하며, 자동 선택만 안전한 선/막대 그래프로 재시도합니다. 재시도 시 설명 모델은 성공한 그래프에 대해서만 호출합니다.

그래프 선택 화면은 현재 조회된 값으로 **지원되는 종류와 지원되지 않는 종류를 분리**합니다. 지원되지 않는 종류를 누르면 필요한 계열 수·공통 시점·단위·값 조건을 안내합니다. 서로 다른 단위를 한 그래프로 요청한 경우 자동으로 별도 그래프로 바꾸지 않으며, 가능한 배치 또는 명시적인 보조축 설정을 확인해야 합니다. 원·도넛에는 항목명·기준 시점·값·단위·구성비를 표시합니다.

입력한 통계표 이름·ID·측정 조건을 정본 사전에 대조하고, 복수 요청은 원문과 모든 조회 계획을 유지합니다. 이름이 맞지 않거나 요청한 지표가 누락된 경우 다른 표를 대신 출력하지 않습니다. 텍스트에 명확한 기간이 있으면 `periodSelection`으로 확인하고 별도 날짜 선택을 생략합니다. 조합이 모호하거나 자료가 없으면 확인 질문/오류를 반환합니다.

복수 기관·산업·지출 항목·국제수지 항목·지급 수단 및 건수/금액 요청은 `catalog_request_planner.py`에서 측정 개념과 정본 분류값을 대조해 각 계열의 조회 계획을 만듭니다. 문맥상 상위 합계와 명시한 하위 항목을 중복 선택하지 않으며, 주기·지역·명목/실질·조정 조건을 확인합니다. 명시 표 ID, 모호한 후보와 불완전한 복수 요청은 기존 탐색 경로를 유지합니다. 정본에 없는 연체율·전망치·채권 금리를 인접 지표로 대체하지 않습니다. API 재검증은 전체 정본 계획과 확정 조건을 대조합니다. 이 경로는 평가 정답이나 문항 ID를 읽지 않습니다.

기자 질의 공개 dev 골든셋 v2의 실제 API 종료 경계 평가는 아래 명령으로 재현합니다. 새 `evaluation_runs/` 폴더에 문항별 HTTP 반환 직후 로그를 남기고, **30문항의 모든 체크가 통과할 때만** `scores.json`과 `PASS-30-of-30.md`를 저장합니다. 실패 실행은 진단 로그와 `diagnostic_scores.json`을 남기고 종료 코드 1을 반환합니다. `execute=false`로 표·항목·분류·주기·기간·역질문/기간 대기 상태를 검증하며 수치 조회, 그래프·수정이 동작과 블라인드 일반화 성능을 검증하지 않습니다.

```powershell
.\.venv\Scripts\python.exe eval/reporter-table-discovery-30/v2/scripts/evaluate_runtime.py
```

편집 화면에서 **수정은 수정이에게 지시하세요** 안내의 캐릭터를 누르면 기존 수정 세션에 연결된 채팅이 열립니다. 투명 GIF 버튼은 드래그 또는 포커스 후 방향키로 이동할 수 있고 같은 브라우저 주소에서는 위치가 저장됩니다. 펜·화살표·사각형·텍스트 표시를 선택해 이동/크기 조절하고, 실제 계열·관측 시점에 연결한 뒤 요청합니다. 부분 편집을 전체 계열로 확대하지 않습니다. 수정 중에는 작업 동작, 평상시에는 8가지 동작 GIF를 표시합니다. 움직임 줄이기 설정에서는 PNG를 사용합니다.

수정 해석은 별도 `ChartEditAgent`와 모델 어댑터를 사용하며 현재 `HCX-007`로 고정합니다. HCX는 손글씨·이미지를 읽지 않고 텍스트와 검증된 표시 범위를 해석합니다. 미래 이미지 지원 모델은 어댑터를 교체해 연결할 수 있지만 아직 구현·검증되지 않았습니다. 명확한 단독 그래프 종류 변경 및 지원 색상 변경은 결정적으로 처리합니다. 실제 조회값은 생성/변경하지 않으며, 실패 시 기존 그래프를 유지합니다.

추가 API 계약: `POST /api/output/options`에 `{session_ids:[...]}`를 보내면 현재 세션의 그래프 지원 조건을 반환합니다. `/api/output/edit`은 기존 필드 외에 선택적 `visual`(marks, selected_target, selection, graph_image, marked_image), `action`(`edit`/`undo`/`redo`), `revision`을 받습니다. 수정 이력은 최대 20개이며 오래된 revision은 409, 적용 불가 요청은 422로 안내합니다. 전체 종류 변경은 기존 부분 스타일 중 호환되지 않는 설정만 정리하고 안내합니다. 프런트 계약은 `src/agent/frontend/src/api/types.ts`가 정본입니다.

“경상수지와 구성 항목”을 명시하면 같은 검증된 통계표의 계정코드로 경상수지·상품수지·서비스수지·본원소득수지·이전소득수지 5개 계열을 조회합니다. 월별 `YYYYMM` 관측점은 Plotly 날짜좌표로 표시하되 원자료 ID와 편집 선택 ID는 그대로 보존합니다.

KOSIS 키가 있으면 로컬 원자료 CSV 없이도 349개 표의 OpenAPI 자료를 조회할 수 있습니다. 개별 분류의 결측값이나 수록기간 차이까지 없어지는 것은 아닙니다. 카탈로그에서 분류를 선택하고 기간을 지정한 뒤, 수신한 자료의 그래프 종류와 편집 항목을 선택합니다.

자세한 변경·검증 범위는 [10월 1일 저녁 작업 기록](docs/2026-10-01-evening-349-update.md)에 정리했습니다.

### 수동 개발 환경

Python 3.12 이상, Node.js, pnpm이 필요합니다. 저장소 루트에서 실행합니다.

**로컬 원자료 CSV:** 이 저장소를 clone해도 `data/tables/`의 통계표 CSV 347개는 포함되지 않습니다. KOSIS가 아닌 로컬 CSV로 수치를 조회할 때 필요하며, 별도 원본의 CSV를 `data/tables/`에 두거나 `STATBRIDGE_TABLES_DIR`로 그 디렉터리를 지정합니다. KOSIS OpenAPI 조회에는 이 CSV가 필요하지 않습니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r src/backend/requirements.txt pytest
cd src/agent/frontend && pnpm install --frozen-lockfile && cd ../../..
```

Windows PowerShell에서는 `.venv/bin/python` 대신 `.venv\Scripts\python.exe`를 사용합니다.
의존성 설치 후 `scripts\windows\START_STATBRIDGE.cmd`로 Agent API와 화면을 함께 시작할 수 있습니다.

CSV가 없어도 메타데이터로 검색과 카탈로그는 확인할 수 있습니다. KOSIS 조회에는 `KOSIS_API_KEY`, HCX·임베딩·재순위화에는 `NCP_CLOVA_API_KEY`가 필요합니다. 키는 루트 `.env`에 두며 Git에 추가하지 않습니다. 예시는 `.env.example`을 참고합니다.

```bash
PYTHONPATH=src/backend:src/agent .venv/bin/python -m uvicorn bridge_api:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 화면을 실행합니다.

```bash
cd src/agent/frontend
pnpm dev
```

`http://127.0.0.1:5173`을 열면 Vite가 `/api` 요청을 8000번 Agent API로 전달합니다. MCP stdio 서버는 저장소 루트에서 별도로 실행합니다.

```bash
PYTHONPATH=src/backend .venv/bin/python src/backend/server.py
```

MCP 도구에는 검색, 메타데이터·수치 조회, 배치 검증, 상태 확인이 포함됩니다. API 키가 없으면 KOSIS·NCP 네트워크 기능은 사용할 수 없지만 사전 기반 질의와 메타데이터 탐색은 가능합니다. 벡터 인덱스는 별도 생성물이며 필요할 때 `tools/build_stat_vector_index.py`로 빌드합니다.

## 검증과 평가

```bash
PYTHONPATH=src/backend:src/agent .venv/bin/python -m pytest -q tests
.venv/bin/python eval/table-discovery/v4.1/scripts/validate_v41.py
.venv/bin/python tools/evaluate_golden_v41.py --dataset eval/table-discovery/v4.1/dev.jsonl --output /tmp/statbridge-v41-dev.json
cd src/agent/frontend && pnpm build
```

두 평가 계열은 버전 번호와 점수를 공유하지 않습니다. 각 버전의 범위·검토 상태·실행 명령은 [평가 자료 안내](eval/README.md)를 참고하세요. v4.1의 비공개 holdout 정답과 평가기는 별도 접근 제한 저장소에서 관리하며, 공개 holdout 파일에는 질문만 있습니다.
