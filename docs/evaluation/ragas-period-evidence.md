# RAGAS 입력의 수록·조회·표시 기간 구분

ai-assisted. 이 문서는 평가 근거 구성 도구의 계약이며, 제품 관측값이나 평가 정답을 변경하지 않는다.

공식 표 메타데이터의 최초 수록 시점, API가 조회한 시점, 이번 화면에 실제 표시한 시점은 서로 다를 수 있다. 예를 들어 전체 수록은 2010년부터여도 사용자가 2027~2028년을 표시할 수 있다. 이동 통계는 표시 시작 이전의 원자료도 조회한다. 메타데이터 수록 시작을 화면 표시 시작의 근거로 사용하지 않는다.

`tools/ragas_execution_scope.py`의 `build_execution_scope(final, evidence, metadata)`는 구조화된 출력의 모든 표시 시점·값을 같은 실행의 공식 API 원응답 또는 실제 계산 기록과 대조한다. 일반 계열은 표·항목·분류·주기까지 일치해야 한다. 계산 계열은 실제 계획의 그래프 출력 노드이고 결과 값이 일치해야 한다. 근거가 없거나 값·시점·선택·주기가 다르면 `ScopeEvidenceError`로 평가 준비를 중단한다. 차트가 없는 확인 질문에는 표시 근거를 만들지 않는다.

검증 후 다음 필드를 독립된 실행 범위 근거로 전달한다.

- `catalog_available_periods`: 사전에 고정한 공식 메타데이터의 전체 수록기간
- `fetch_periods`: 실제 API 또는 계산의 조회기간과 선행 이력
- `configured_display_period`: 이번 실행의 화면 표시 범위
- `displayed_observation_periods`: 실제 구조화된 출력의 관측 시점 목록
- `first_displayed_observation_period`, `last_displayed_observation_period`: 이번 출력의 첫·마지막 관측 시점
- `point_source_bindings`: 각 표시 값을 원응답의 행 또는 계산 결과 노드에 연결하는 위치

이 모듈은 질문, 정답, 생성 요약, Judge 판정, 문항 ID를 입력받지 않는다. 표시 범위는 실행 상태에 관한 근거이며 통계 수치나 경제학적 정의의 독립 출처를 대신하지 않는다. 공식 원자료와 메타데이터는 계속 별도 근거로 유지한다.

평가 어댑터는 원출력, 원응답·계산 캡처, 고정 메타데이터의 실행·문항 ID와 SHA-256을 먼저 검증한다. 생성한 범위 근거에는 이 캡처들의 해시와 경로를 연결한다. 사후 검증기는 동일 입력으로 모듈을 다시 실행해 근거가 재현되는지 확인한다. 실행에 사용한 모듈 사본과 해시도 저장한다.

이 변경은 `request_response_metadata_scope_v3`라는 새 평가 입력 버전으로 기록한다. 표준 RAGAS Faithfulness의 주장 추출·NLI 프롬프트와 모델 설정은 변경하지 않는다. 전체 평가 집단에 같은 방식으로 적용하고 기존 응답·입력·판정·점수를 덮어쓰지 않는다. 동일 응답에 대한 입력 보완 후 점수 변화는 제품 성능 개선과 구분해 보고한다.

합성 회귀 검사:

```powershell
$env:PYTHONPATH='src/backend;src/agent'
python -m pytest -q tests/test_ragas_execution_scope.py
```
