# StatBridge HCX-003 vs Jev 역할 맞춤 100문항 평가기

## 무엇을 비교하나
업로드된 StatBridge1 실제 코드의 `NcpClovaClient.classify_stat_language()` 역할만 평가합니다.
HCX-003은 현재 다음 일을 합니다.
1. 핵심 통계 분야/개념 보존
2. 분석 의도(추이, 최신, 비교, 비중 등) 보존
3. 조건(계절조정, 원계열, 말잔, 신규취급액 등) 보존
4. 명시된 자료 주기 보존
5. 2~5개 비교 지표를 `series`로 분리

HCX-003이 하지 않는 `table_id/itmId/objL 생성`, 역질문 문장 생성, 최종 답변 생성은 이 핵심 점수에서 제외합니다.

## Gold Set 근거
- `stat_language_dictionary.json`의 실제 `routing_examples` 29건
- 각 routing example의 `expected_table_id`, `intent`, `qualifiers`
- 해당 table의 실제 `domains`
- 의미를 바꾸지 않는 자연어 변형 3종씩 = 87건
- 실제 비교 용례 기반 multi-series 13건
- 합계 100건

## 왜 Jev와 공정한가
Jev는 자유 문자열 생성 모델이 아니므로 HCX의 JSON 문장을 그대로 만들게 하지 않습니다.
대신 두 모델의 결과를 동일한 canonical decision으로 환산하여 채점합니다.
- 분야 정확도
- 분석 의도 F1
- 조건/기준 F1
- 자료 주기 정확도(명시된 문항만)
- 다중지표 분리 개수 정확도

HCX는 StatBridge 실서비스 프롬프트를 그대로 사용합니다.
Jev는 같은 질문에 대해 canonical 후보를 native Choice/Yes-No 형태로 판단합니다.

## 모델
현재 계정에서 확인된 모델:
- jev-latest
- jev-preview

둘 다 평가합니다. 운영 안정 기준선은 `jev-latest`, 차기 후보는 `jev-preview`로 보면 됩니다.

## 실행
1. `.env.example`을 `.env`로 복사
2. HCX와 TypeSafe API 키 입력
3. 5문항 연결 테스트:
   `./run_smoke.ps1`
4. 전체 100문항 x 3회 x 3모델:
   `./run_full.ps1`

## 결과
`results/YYYYMMDD_HHMMSS/summary.json`과 `comparison.md` 생성.

### 지표 한국어 의미
- role_score_pct: 아래 역할 지표 평균. HCX-003 대체 적합성의 요약값
- domain_accuracy_pct: 질문의 통계 분야를 맞힌 비율
- intent_f1_pct: 추이/비교/비중/증가율 등 분석 의도를 빠뜨리거나 덧붙이지 않고 맞힌 정도
- qualifier_f1_pct: 계절조정/원계열/말잔/잔액/신규취급액 같은 조건을 정확히 잡은 정도
- frequency_accuracy_pct: 월별/분기별/연간처럼 질문에 명시된 주기를 맞힌 비율
- series_count_accuracy_pct: 여러 지표를 비교할 때 독립 검색할 지표 개수를 제대로 분리한 비율
- valid_output_pct: API 200이며 해석 가능한 정상 출력 비율
- consistency_pct: 동일 질문 3회 반복 시 핵심 판단이 완전히 같았던 비율
- latency_p50_s: 절반의 요청이 이 시간 안에 끝남
- latency_p95_s: 95% 요청이 이 시간 안에 끝남
- api_error_pct: HTTP 오류 비율

## 주의
이 평가는 JevBench 일반 성능이 아니라 `StatBridge HCX-003 역할 대체 적합성` 평가입니다.
