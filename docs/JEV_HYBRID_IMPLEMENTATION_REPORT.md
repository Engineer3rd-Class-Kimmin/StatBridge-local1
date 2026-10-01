# Jev 하이브리드 구현 및 검증 보고서

> 이 문서는 StatBridge1에서 작성한 구현·검증 기록을 보존한 것이다. StatBridge-local에서는 `StatBridge-official/src/agent` → `src/agent`, `statbridge_mcp_server` → `data/statbridge_mcp_server`, `evaluation_runs` → `eval/runs`로 이식했다. 최신 통합 기록과 수치 해석 보충은 `docs/운영/2026-10-01-통합-작업-요약.md`를 따른다.
>
> 정정: 아래 하이브리드 표의 API 오류율 1%는 invalid 출력률이다. HTTP API 오류율은 0%, 비정상 출력률은 1%다. 최종 100%는 평가셋을 보고 프롬프트를 보완한 뒤 저장 HCX 결과와 실제 Jev 호출을 합친 확인 결과이며 독립 holdout 또는 전체 서비스 표 검색 성공률이 아니다. 자동 테스트의 PASS 범위도 당시 실행한 루트 테스트로 한정한다. StatBridge-local 전체 `tests/`에서는 구버전 패키지 테스트 10개의 수집 오류와 1개 버전 테스트 실패가 확인됐다.

검증일: 2026-10-01
대상: `C:\Users\김민\Desktop\경진대회\StatBridge1`

## 1. 기존 구조

`NcpClovaClient.classify_stat_language()`가 HCX-003을 호출하여 `normalized_query`, `concepts`, `subjects`, `measures`, `time_terms`, `comparison_terms`, `qualifiers`, `series`를 생성한다. 이 단계는 통계표 ID나 KOSIS 값을 만들지 않는다.

`StatBridgeAgent._classify()`가 원문과 위 분류값을 사전 검색문으로 합친다. `series`가 2~5개이면 `_resolve_comparison()`이 각 series마다 `HybridStatRetriever.rank()`를 독립 호출한다. Hybrid Retriever는 deterministic rule fast path를 먼저 확인하고, 필요할 때 Embedding v2 → Chroma Top-K → NCP Reranker → 사전 rule fusion을 수행한다. 선택된 실제 사전 항목으로만 `table_id`, `itmId`, `objL1~objL8`을 구성하며 LangGraph가 MCP `StatisticsService` 실행과 출력 에이전트 단계를 연결한다.

정확 일치·고점수 사전 후보는 `resolve()`의 deterministic fast path에서 HCX와 Jev를 모두 생략한다. 이는 기존 경로와 성능을 보존한다.

환경 파일은 `ncp_clova_client._load_env()`가 우선 `statbridge_mcp_server/.env`에서 읽는다.

## 2. 변경 구조

HCX-003 뒤, 검색 진입 전에 `JevSeriesClient.classify_series_count()`를 추가했다. Jev는 원질문을 최우선 근거로 보고 HCX의 concepts, measures, series를 보조 근거로 받아 0/2/3/4/5 중 하나만 반환한다. HCX의 나머지 필드는 변경하지 않는다.

```text
질문 → HCX-003 구조화 → Jev 지표 개수 판정 → series 검증/보정
     → 기존 Hybrid Retriever → 사전 ID 선택 → LangGraph → MCP/KOSIS → 출력 에이전트
```

## 3. 변경 파일 목록

| 파일 | 수정 함수/영역 | 수정 이유 |
|---|---|---|
| `StatBridge-official/src/agent/jev_series_client.py` | `JevSettings`, `request_body`, `classify_series_count` | TypeSafe System One의 좁은 series-count 호출, 응답 검증, timeout/HTTP/JSON 오류 처리 |
| `StatBridge-official/src/agent/jev_series_hybrid.py` | `apply_jev_series_decision`, `_safe_metric_candidates` | HCX series 유지·제거·안전 보정 |
| `StatBridge-official/src/agent/statbridge_agent.py` | `__init__`, `_classify`, deterministic fast path | HCX 직후 Jev 연결 및 trace 저장 |
| `StatBridge-official/src/agent/bridge_api.py` | `/api/health` 응답 | Jev 활성화·키 설정 여부·모델 관측 |
| `statbridge_mcp_server/.env.example` | Jev 환경변수 | 키 하드코딩 방지와 feature flag 제공 |
| `TEST_JEV_HYBRID.py` | 14개 테스트 | 핵심 6사례, API 스키마, disable, key 없음, timeout, 잘못된 응답, fallback 검증 |
| `tools/evaluate_jev_hybrid_100.py` | 저장 응답 paired replay | 기존 3회 반복 결과 재현 비교 |
| `tools/run_live_jev_hybrid_100.py` | 현재 프롬프트 100문항 실행 | 실제 적용 프롬프트의 전체 gold set 검증 |
| `tools/run_jev_hybrid_e2e.py` | Agent/MCP/KOSIS probe | 네 질문의 실제 네트워크 E2E 기록 |

HCX-003 프롬프트, resolver, Hybrid Retriever, MCP/KOSIS 코드는 변경하지 않았다.

## 4. Jev 요청/응답 구조

요청 경로는 `POST https://api.typesafe.ai/v1/systemone`이다. `options`가 아니라 현재 스키마의 `criteria`를 사용한다.

```json
{
  "model": "jev-preview",
  "state": "원래 사용자 질문(판단의 최우선 근거): ...\nHCX concepts(보조 근거): [...]\nHCX measures(보조 근거): [...]\nHCX series(보조 근거): [...]",
  "questions": {
    "series_count": {
      "type": "choice",
      "instructions": "원질문에 명시된 독립 통계지표 개수만 판정...",
      "criteria": {
        "0": "단일 지표",
        "2": "독립 지표 2개",
        "3": "독립 지표 3개",
        "4": "독립 지표 4개",
        "5": "독립 지표 5개"
      }
    }
  }
}
```

정상 응답에서 사용하는 부분은 다음뿐이다.

```json
{
  "answers": {
    "series_count": {
      "type": "choice",
      "choice": "2",
      "confidence": 0.98,
      "probabilities": {"0": 0.02, "2": 0.98, "3": 0, "4": 0, "5": 0}
    }
  }
}
```

Authorization 값은 코드, 로그, 평가 파일에 저장하지 않았다.

## 5. series 보정 규칙

- 0: 최종 `series=[]`, action=`cleared`.
- 2/3/4/5이고 HCX 개수가 동일: HCX series를 그대로 유지, action=`kept`.
- 2/3/4/5이고 HCX가 부족: HCX concepts를 정확히 필요한 개수만큼 확보하고 각각이 통계지표임을 확인할 수 있을 때만 label/query를 구성, action=`repaired`.
- 지역명뿐이거나 지표 근거가 불충분하거나 HCX가 Jev보다 더 많은 경우: 임의로 선택·생성하지 않고 HCX 원본 유지, action=`not_safe`.
- Jev는 table ID, item ID, dimension ID 또는 지표 문자열을 생성하지 않는다.

trace에는 `jev_enabled`, `jev_model`, `jev_status`, `jev_latency_ms`, `jev_series_count`, `jev_probability`, `hcx_series_count_before`, `final_series_count`, `series_action`, `jev_fallback_used`가 저장된다. deterministic fast path는 `jev_status=skipped_deterministic_fast_path`로 남는다.

## 6. Fallback

키 없음, timeout, 네트워크 오류, HTTP 4xx/5xx, JSON 오류, choice 누락 및 허용되지 않은 choice는 모두 예외를 요청 실패로 전파하지 않고 HCX 원본을 유지한다. trace만 `jev_status=error` 또는 `not_configured`, `jev_fallback_used=true`로 기록한다.

`STATBRIDGE_JEV_ENABLED=0`이면 Jev를 호출하지 않는다. HCX 분류값과 기존 검색 분기는 변경되지 않는다.

## 7. 자동 테스트 결과

| 테스트 | 결과 |
|---|---|
| 단일 경제심리지수 → 0/빈 series | PASS |
| 기준금리+대출금리 → 2 series | PASS |
| 서울+부산 청년 고용률 → 단일지표 | PASS |
| 기준/예금/대출금리 → 3 series | PASS |
| 서울+부산+대구 소비자물가지수 → 단일지표 | PASS |
| 소비자물가지수 전년 대비 증가율 → 단일지표 | PASS |
| `criteria` 사용 및 `options` 부재 | PASS |
| 허용되지 않은 choice | PASS, HCX fallback |
| timeout/endpoint 계열 요청 오류 | PASS, HCX fallback |
| API key 없음 | PASS, HCX fallback |
| Jev disabled | PASS, 호출 0회 |
| 안전하지 않은 repair | PASS, HCX 보존 |
| Python 전체 기존 테스트 discovery | PASS (14 Jev 테스트와 기존 LangGraph/출력 테스트 실행) |
| Python 구문 컴파일 | PASS |
| Frontend `npm run build` | PASS |

참고: 기존 `TEST_NCP_MODELS.py`는 unittest가 아니라 외부 API smoke 출력 스크립트이며, 샌드박스 실행에서는 프록시 차단 메시지를 출력했다. 별도 승인 네트워크 E2E에서는 HCX-003, Jev, KOSIS가 실제 성공했다.

## 8. Gold Set 100문항 결과

기존 `goldset_statbridge_hcx003_role_100.jsonl`을 내용 변경 없이 프로젝트 `eval/goldset`에 복사했다. HCX는 기존 저장 결과를 재사용했고, 최종 하이브리드는 현재 좁은 Jev 프롬프트로 100문항을 각각 1회 실제 호출했다.

| 항목 | HCX-only | Hybrid |
|---|---:|---:|
| 통계 분야 파악 정확도 | 98.00% | 98.00% |
| 분석 의도 F1 | 64.23% | 64.23% |
| 세부 조건 F1 | 93.33% | 93.33% |
| 자료 주기 정확도 | 100.00% | 100.00% |
| 다중 지표 분리 정확도 | 28.00% | **100.00%** |
| 정상 출력률 | 99.00% | 99.00% |
| API 오류율 | 1.00% | 1.00% |
| p50 latency | 4.009초 | 4.180초 |
| p95 latency | 5.036초 | 5.251초 |

Hybrid API 오류 1%는 재사용한 HCX 저장 결과의 기존 1건 실패가 분모에 포함된 값이다. 이번 100회 Jev 호출의 fallback/API 실패는 0건이었다. 현재 프롬프트의 지표 분리 정확도는 100/100이며 p50은 HCX-only 대비 0.171초, p95는 0.215초 증가했다.

재현 파일:

- `evaluation_runs/20261001_jev_hybrid_live_100/summary.json`
- `evaluation_runs/20261001_jev_hybrid_live_100/hybrid_results.jsonl`
- `evaluation_runs/20261001_jev_hybrid_100/summary.json` (기존 저장 응답 3회 paired replay)

## 9. E2E 검증 결과

| 질문 | HCX 결과 | Jev/최종 series | 선택 표/MCP | 판정 |
|---|---|---|---|---|
| 최근 경제심리지수 추이 보여줘 | deterministic exact fast path | Jev 생략, 0 | `DT_513Y001`; KOSIS 12행, output agent까지 성공 | PASS |
| 최근 5년 기준금리와 대출금리를 비교해줘 | HCX 2 series | Jev 2(1.00), 최종 2 | 기준금리가 신뢰 사전에 없어 `no_match` | FAIL (사전 범위 제한) |
| 기준금리, 예금금리, 대출금리를 최근 3년간 비교해줘 | HCX 3 series | Jev 3(0.99), 최종 3 | 기준금리가 신뢰 사전에 없어 `no_match` | FAIL (사전 범위 제한) |
| 서울과 부산의 소비자물가지수를 비교해줘 | HCX 지역별 2 series | Jev 0(1.00), 최종 0 | 현재 사전의 지역 조합 해석에서 `no_match` | PASS(분리) / FAIL(검색) |

확인된 사항:

- 단일 지표의 불필요한 분리는 발생하지 않았다.
- 2개·3개 지표의 Jev 판정과 최종 series 수는 정확했다.
- 여러 지역의 같은 지표는 최종 series 0으로 보정됐다.
- 경제심리지수는 기존 사전 선택, LangGraph, MCP, KOSIS, 출력 에이전트까지 실제 성공했다.
- 기준금리/지역 CPI E2E의 최종 표 조회는 기존 사전 범위 때문에 완료되지 않았다. Jev가 ID를 만들어 우회하지 않도록 한 보안·정확성 원칙에 따른 결과다.

실행 기록: `evaluation_runs/20261001_jev_hybrid_e2e.json`.

## 10. 장애 테스트

| 상황 | 결과 |
|---|---|
| `TYPESAFE_API_KEY` 없음 | HCX 결과 유지, `not_configured`, fallback true |
| timeout | HCX 결과 유지, 요청 전체 정상 지속 |
| 잘못된 endpoint/연결 오류 | HCX 결과 유지, `error`, fallback true |
| HTTP/응답 choice 오류 | HCX 결과 유지, 오류가 검색 경로로 전파되지 않음 |
| `STATBRIDGE_JEV_ENABLED=0` | Jev 호출 0회, 기존 HCX-only 분류 유지 |

## 11. 최종 판정

**PASS WITH LIMITATIONS**

하이브리드의 핵심 목표인 다중지표 분리 정확도는 같은 고정 100문항에서 28.00%에서 100.00%로 72.00%p 개선되었고, 현재 Jev 호출 오류는 0/100이었다. p50 지연은 0.171초 증가했다. 14개 신규 테스트, 기존 LangGraph/출력 테스트, Frontend 빌드, 실제 HCX/Jev 및 1건의 MCP/KOSIS 조회는 통과했다.

제한사항은 지정된 2·3지표 기준금리 질문과 다지역 CPI 질문이 현재 신뢰 사전에서 최종 통계표로 해결되지 않아 해당 세 질문의 MCP/KOSIS 실행까지 성공하지 못했다는 점이다. Jev series 판정은 성공했으며, 기존 검색·사전 구조를 임의 ID로 우회하지 않았다.
