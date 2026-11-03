# 평가 자료

2026-11-02의 [RAGAS 보완·LLM Judge 작업 기록](../docs/evaluation/2026-11-02-ragas-llm-judge.md)은 최초 10문항 평가, Q05·Q06 수정 후 별도 실행, Q10 스크롤 보완 관찰과 저장 수치 검산을 구분합니다. RAGAS 보완 후 새 채점 점수와 사람 정성 승인 점수는 보고하지 않습니다(ai-assisted).

2026-10-23~28의 최소 E2E v1·v1.1·v2·v2.1·v1.4는 [첫 평가·개선·재검증 이력](../docs/evaluation/min-e2e-history/README.md)에 정리했습니다. 실행 범위, 전수/부분 재검증, 정성 승인 상태와 현재 미확보인 원시 자료를 구분합니다.

버전 번호는 아래 두 계열 **안에서만** 이어집니다. 서로 다른 계열의 v1·v2를 합치거나 점수를 직접 비교하지 않습니다.

| 계열 | 경로 | 범위와 현재 상태 |
|---|---|---|
| 표 탐색 | [`table-discovery/v1`](table-discovery/v1/README.md) | ClaBi에서 가져온 150건 초안, 사람 승인 0건 |
| 표 탐색 | [`table-discovery/v2`](table-discovery/v2/README.md) | ClaBi에서 가져온 150건 구조 개선 초안, 사람 승인 0건 |
| 표 탐색 | [`table-discovery/v3`](table-discovery/v3/README.md) | ClaBi에서 가져온 dev 30건, 사람 승인 0건 |
| 표 탐색 | [`table-discovery/v4.1`](table-discovery/v4.1/README.md) | 공개 dev 50·test 70건과 정답 없는 holdout 질문 30건, 사람 승인 전 평가 준비본 |
| 표 탐색 | [`reporter-table-discovery-30/v1`](reporter-table-discovery-30/v1/README.md) | 기자 자연어 질문 30건·정답 계열 57개·단계별 정답, 사람 검수 전 공개 개발용 초안 |
| 표 탐색(동결) | [`reporter-table-discovery-30/v3`](reporter-table-discovery-30/v3/README.md) | 349개 표 1,104문항 구조 회귀셋. 2026-10-13 이후 사용하지 않음 |
| 표 탐색(동결) | [`reporter-table-discovery-30/v3-four-tables`](reporter-table-discovery-30/v3-four-tables/README.md) | 네 표 요청 36문항. 2026-10-13 이후 사용하지 않음 |
| 표 탐색 | [`reporter-table-discovery-30/v2.1`](reporter-table-discovery-30/v2.1/README.md) | v2 30문항을 보존하고 보고서 그림·표 주제로 증강한 200문항, 단계별 정답과 출처 해시, 사람 승인 전 |
| 전체 흐름 | [`end-to-end/v1`](end-to-end/v1/README.md) | 간행물 기반 150건·그래프 fixture 30건; 현재 측정값은 검색과 정답 계열을 지정한 로컬 그래프 데이터에 한정, 보류셋 개발 노출 |
| 전체 흐름 | [`end-to-end/v2`](end-to-end/v2/README.md) | v1 회귀 150건과 미작성 신규 사례 큐 50건; 출시 평가 준비 전 |
| 표 탐색+해석 | [`statbridge-golden/v1`](statbridge-golden/v1/README.md) | 자체 설계 계열(9계층·오답 함정·해석). 초기 시드 진행 중, 사람 승인 전 |
| 통합 흐름 | [`statbridge-e2e/v1`](statbridge-e2e/v1/README.md) | 한국은행 36유형 기반 공개 120문항(dev 48·test 72)과 질의만 공개한 holdout 40건. Tier A 8유형만 숫자 채점, 나머지는 계열·기간 채점. 사람 검수 전 |

`table-discovery/v1`·`v2`·`v3`의 원본은 ClaBi 작업 폴더의 `golden-set-v1`·`golden-set-v2`·`golden-set-v3/golden-set-v3`입니다. 사례·검토 파일은 그대로 복사했고, 이 저장소에서 관리 스크립트의 경로만 조정했습니다. 초안에 전체 파이프라인 필드가 있더라도 승인된 전체 흐름 정답이나 수치 평가로 해석하지 않습니다.

`end-to-end/v1`·`v2`는 기존 StatBridge-local의 `eval/golden-set-v1`·`golden-set-v2`를 이동한 것입니다. `table-discovery/v4.1`도 기존 `eval/golden-set-v4.1`을 이동했습니다. `eval/retrieval/`은 별도 검색 실험 자료입니다.

개발 중 v4.1 평가는 공개 dev/test만 사용합니다. 비공개 holdout 정답과 평가기는 `StatBridge-evaluation-private`에만 두고 공개 저장소로 복사하지 않습니다. 원자료 CSV가 필요한 구버전 관리 명령은 `data/tables/`을 별도로 공급한 환경에서 실행합니다.
