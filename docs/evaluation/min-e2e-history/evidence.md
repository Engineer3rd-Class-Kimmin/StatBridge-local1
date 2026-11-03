# 근거 파일·누락 자료와 대조 범위

점검일 2026-10-29 · ai-assisted · 아래 경로는 이 저장소 루트 기준의 로컬 식별자다.

## 이번에 직접 확인한 범위

선택한 서술 보고서와 점수 파일의 문항 ID 중복·수량·판정을 대조했다. 아래 SHA-256은 이번 점검 시점의 파일 바이트를 식별한다. 실행 당시 동결 manifest의 모든 원응답·화면·원자료 해시를 다시 검증했다는 뜻은 아니다. 원시 점수/예측과 API 원응답은 PR에 넣지 않는다. `records/`에는 공개 개발 평가의 서술 보고서만 보존했다.

| 원시 점수 파일(로컬만) | 고유 문항 수 | 이번 대조 집계 |
|---|---:|---|
| `evaluation_runs/team-v2-evaluation-20261027/scores.json` | 60 | `{'pass': 54, 'fail': 6}` |
| `evaluation_runs/team-v2-patched-20261027/scores.json` | 60 | `{'pass': 60}` |
| `evaluation_runs/v21-evaluation-20261027/scores.json` | 60 | `{'미통과': 44, '통과': 16}` |
| `evaluation_runs/v21-patched-final-20261027/scores.json` | 60 | `{'통과': 60}` |
| `evaluation_runs/v14-query-only-20261027/scores.json` | 50 | `{'pass': 16, 'fail': 33, 'pending': 1}` |
| `evaluation_runs/v14-output-final-20261027/scores-v2.json` | 50 | `{'pass': 46, 'fail': 3, 'pending': 1}` |
| `evaluation_runs/v14-patch-results-20261027/latest-results.json` | 50 | `{'pass': 49, 'pending': 1}` |
| `evaluation_runs/v14-me14-41-clarified-20261028/scores-v2.json` | 1 | `{'pass': 1}` |

v2.1은 최초 DA/CQ/QT 통과 5/6/5, 최종 20/20/20을 영역별로 확인했다. v1.1 추가 분류 summary는 total=20, passed=20, failed=0이다. v1·v1.1 UI 수치는 아래 누락 때문에 기존 main 보고서를 인용한 수준이다.

## 확인하지 못한 자료

| 보고서가 참조하는 경로 | 현재 상태 | 영향 |
|---|---|---|
| `evaluation_runs/min-e2e-v1/ui-full-20261023/baseline-summary.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/release-final/scores.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/release-final/run-manifest.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/release-final/assistant-review.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/release-final/file/visual-contact-1.png` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/full-regression-release-final.xml` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1/ui-full-20261023/full-regression-release-final.log` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/baseline/responses.jsonl` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/release2/ui` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/release2/responses.jsonl` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/release2/scores.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/release2/source-manifest.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/scorer-only/latest-official.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |
| `evaluation_runs/min-e2e-v1.1/completion.json` | 미확보 | 과거 원관측·화면·채점 또는 회귀를 이번에 재검증할 수 없음 |

두 원시 실행 상위 폴더도 없다. 확인한 공개 자료 ZIP(`output/statbridge4.zip`, `output/distribution/statbridge3.zip`, `output/clarification-20-20261023.zip`, 루트의 `데이터 조회 평가 재평가.zip`)에서는 ZIP 내부 파일명 기준으로 v1/v1.1 실행 폴더, `assistant-review.json`, `baseline-summary.json`을 찾지 못했다. 현재 저장소와 인접 `StatBridge-pull`의 해당 두 경로도 미확보다. 모든 디스크·백업을 검색한 것은 아니므로 영구 삭제로 단정하지 않는다. 비공개 저장소와 holdout 정답은 열거나 검색하지 않았다.

사용자에게 누락 사실을 작업 중 알렸고, 별도 보관 위치는 모른다는 답변을 받았다. 자료를 찾으면 원본 해시·실행 시점·입력/제품 버전을 먼저 대조한 뒤 별도 보완해야 한다. 이번 PR은 결과를 추정하거나 새 실행으로 과거 증거를 대체하지 않는다.

## 입력·실행기와 승인 상태

- v1·v1.1 기존 질의/정답·UI 보고서는 main에 있다. 추가 분류 자료와 고난도 v2.1 데이터셋은 로컬 미추적 자료/공개 준비 브랜치에 존재한다.
- v2 원본과 운영 보완본은 `evaluation_runs/team-v2-review-20261027/`, `evaluation_runs/team-v2-evaluation-20261027/dataset/`에 존재한다. 준비 검사와 모델/API 실제 실행을 구분한다.
- v2/v2.1의 최초·패치 실행기, 질문·예측·점수, v1.4의 최초·전수·부분·보완 질의 실행 자료는 로컬에 존재한다. 전체 실행 산출물이 빠짐없이 보존됐다고 주장하지 않는다.
- v1.4 전달 질문/정답·rubric·승인 기록의 공개 준비 사본은 `.venv/cache/golden-records-worktree/eval/min-e2e/v1.4/`에서 확인했다. main의 기존 보완 질의와 구분한다.
- 사람 정답 승인, 참고 자료 승인, 제품 출력의 정성 승인, 코드 리뷰 승인을 분리한다. 사람의 최종 제품 출력 정성평가 완료 파일은 확인하지 못했다.
- 정성 관련 근거는 v1 보고서의 16개 이미지/문서 assistant 검수 기록(원본 미확보), v2 보고서의 23개 그림/문서 검수, v1.4의 표본 가독성·DOM/Plotly 검수 기록이다. AI 검수를 사람 승인으로 바꾸지 않는다.

## 확인한 원본의 SHA-256

기존 v1/v1.1 문서는 PR 기준 main의 추적 파일, 나머지는 현재 로컬 원본의 바이트다. 보존본은 LF와 원문 상대 링크의 경로 텍스트 변환 때문에 원본과 바이트가 다를 수 있다. 원본 해시는 당시 평가 시점의 제품·원자료 무결성을 대신 보증하지 않는다.

| 원본 경로 | 이번 점검 SHA-256 | PR 내 처리 |
|---|---|---|
| `evaluation_runs/query-type-v11-20261027-121419/REPORT.md` | `bcc222798050a62111effa897894d0a71e63deaa7da498214205916150b478b9` | [서술 보존본](records/v1.1-query-type-20261027.md) |
| `evaluation_runs/team-v2-review-20261027/REVIEW.md` | `98f376e4364e5e3943fb57788f118a9659485c5871c008d30414bca036bbffb8` | [서술 보존본](records/v2-independent-review-20261027.md) |
| `evaluation_runs/team-v2-evaluation-20261027/평가결과.md` | `e171ca199b8b598c18a64f28bfcad797bd2c26e343f4976bb3e6a5489a8e338c` | [서술 보존본](records/v2-initial-20261027.md) |
| `evaluation_runs/v21-evaluation-20261027/평가결과.md` | `d4da44add9bc5353c0965aad9ae5fccbec9f941b05f5134e2ef6bc14d18b8fad` | [서술 보존본](records/v2.1-initial-20261027.md) |
| `evaluation_runs/patch-validation-final-20261027/패치및재평가결과.md` | `cc3d311e3e484a1117547548851ecfcddd41e100d8eaf0773c8ce3db30e64462` | [서술 보존본](records/patch-reevaluation-20261027.md) |
| `evaluation_runs/v14-query-only-20261027/평가결과.md` | `5d48bf56b52241923d89f35b17ea3536cebc4bfea014c9b59d471d4f8eb2047d` | [서술 보존본](records/v1.4-initial-20261027.md) |
| `evaluation_runs/v14-patch-results-20261027/패치_재검증_결과.md` | `421cdfcc50fb38e79d28b901c2f7a7fc66eb2b9091cf7a8e12946bf2c4de6b37` | [서술 보존본](records/v1.4-patched-20261027.md) |
| `evaluation_runs/v14-me14-41-clarified-20261028/ME14-41_보완_결과.md` | `e70b095aa2c178528743b8d94ec67c3b02e2d5b8f91e44f693db1168403f5ad6` | [서술 보존본](records/v1.4-ME14-41-20261028.md) |
| `.venv/cache/golden-records-worktree/eval/min-e2e/v1.4/승인_기록.md` | `c6d90fe187a4c9a2b21ccff15649c88bdaf0cbf1ce5b61ae1b180419148116d3` | [서술 보존본](records/v1.4-reference-approval-20261027.md) |
| `eval/min-e2e/v1/FULL_UI_REPORT.md` | `4658ef3353c724a58dd9c5f3549c49778cdb8e7ab468ca90fe9cd45ff8beb2f9` | 기존 main 추적 문서 |
| `eval/min-e2e/v1/질의와_정답_60문항.md` | `25cd6bc56f7198edbf357886184dde6dc97b557cbbe98cb8233025c9c618e46a` | 기존 main 추적 문서 |
| `eval/min-e2e/v1.1/FULL_UI_REPORT.md` | `13bf8b22c6e3a57c669d88551641907a35b490b0d503c07613cfb251578fc617` | 기존 main 추적 문서 |
| `eval/min-e2e/v1.1/질의와_정답_40문항.md` | `20e66e275856fbbbc944c89e3e6c5cd075463b5e49fd44dc8468f0e8f80b6556` | 기존 main 추적 문서 |
| `evaluation_runs/team-v2-evaluation-20261027/scores.json` | `083b0fe584e2e372443276b7e5908250daaf77c5277fa2800a19638f49c750af` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/team-v2-patched-20261027/scores.json` | `13a26b1384d9631de98f7b754491846a8d5d222752368b2cd3dd645d376ae3ef` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v21-evaluation-20261027/scores.json` | `67db742a775377fcac91791f5af78ce2c2d2d76e9c15a1f26a72fee62cf35b8b` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v21-patched-final-20261027/scores.json` | `b3993039e20a86e6266797e22d9b5aae0df029b9b185983097511d7e59dabe35` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v14-query-only-20261027/scores.json` | `c0bd3d3e98519e590e1a97e018686ae7d8e9cf1a40ce7edce82dfd72cac048cb` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v14-output-final-20261027/scores-v2.json` | `82ca06c35d0603761ae95e5413d4c0623164909e76f61520acddfdaf644cb64b` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v14-patch-results-20261027/latest-results.json` | `5d1799bd0992f530b26bd6dd090e7c391eb6ed80f287f788285547e8e6b7e5e4` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/v14-me14-41-clarified-20261028/scores-v2.json` | `5f0036e2855d0c20e68663b99061bf23cf052471dd0b964a602178ba197049d6` | 로컬 참조만, PR 미포함 |
| `evaluation_runs/query-type-v11-20261027-121419/output/summary.json` | `1a2d6285b2cec417e928d679ea84c3dbc9b140c5df49ec7ffb551db098b9efbe` | 로컬 참조만, PR 미포함 |
