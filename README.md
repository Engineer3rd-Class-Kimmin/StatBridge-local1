# StatBridge

현재 작업 저장소는 [Engineer3rd-Class-Kimmin/StatBridge-local1](https://github.com/Engineer3rd-Class-Kimmin/StatBridge-local1)이다. Windows에서는 루트 `START_STATBRIDGE.cmd`로 실행한다. 통합 HTTP 실행 모듈은 `src/agent/agent_runtime.py`이며 기존 `statbridge_agent/` 패키지도 보존한다.

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
| `data/processed/` | 349개 표의 메타데이터와 347개 지원 표의 항목·분류 정보 |
| `data/tables/` | 별도 원본에서 공급하는 347개 통계표 CSV; Git에 포함하지 않음 |
| `eval/table-discovery/` | 질의 해석~통계표 탐색 평가 v1·v2·v3·v4.1 |
| `eval/end-to-end/` | 별도 계열의 전체 흐름 평가 v1·v2 초안 |

화면의 주 API는 `src/agent/bridge_api.py`입니다. `src/backend/query_api.py`도 같은 앱을 실행합니다. 이전 `/api/analyze` 계약은 제공하지 않으므로 해당 클라이언트는 새 `/api/query` 응답에 맞춰 수정해야 합니다. `analysis_service.py`는 기존 평가 도구 참고용으로 남아 있습니다.

## 설치와 실행

Python 3.12 이상, Node.js, pnpm이 필요합니다. 저장소 루트에서 실행합니다.

**원자료 CSV:** 이 저장소를 clone해도 `data/tables/`의 통계표 CSV 347개는 포함되지 않습니다. 로컬 수치 조회에는 반드시 필요하며, 별도 원본의 CSV를 `data/tables/`에 두거나 `STATBRIDGE_TABLES_DIR`로 그 디렉터리를 지정해야 합니다.

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
