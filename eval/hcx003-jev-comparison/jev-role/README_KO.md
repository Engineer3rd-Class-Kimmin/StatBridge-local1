# Jev 전용 StatBridge 역할 평가 v2

이 패키지는 **HCX-003을 다시 호출하지 않습니다.**
기존 HCX-003 평가 데이터는 그대로 두고, 동일한 100문항 Gold Set으로 아래 두 모델만 평가합니다.

- jev-latest
- jev-preview

## 수정한 핵심 오류

이전 평가기는 Choice 질문을 `options`로 전송해서 HTTP 422가 발생했습니다.
현재 TypeSafe System One API는 Choice 질문에 `criteria` 맵이 필요합니다.

v2는 다음 형태로 전송합니다.

```json
{
  "type": "choice",
  "instructions": "...",
  "criteria": {
    "A": "설명 A",
    "B": "설명 B"
  }
}
```

Noul(yes/no)은 `instructions`를 사용합니다.

## 실행

1. `.env.example`을 `.env`로 복사
2. `TYPESAFE_API_KEY` 값 입력
3. 먼저:

```powershell
.\run_jev_smoke.ps1
```

정상 기준:
- jev-latest HTTP 200
- jev-preview HTTP 200
- `valid=True`

4. smoke가 정상일 때 전체 평가:

```powershell
.\run_jev_full.ps1
```

전체 평가는 100문항 × 3회 × 2모델 = 600 API 요청입니다.

결과:
`results\jev_only_YYYYMMDD_HHMMSS\`

- `jev-latest.jsonl`
- `jev-preview.jsonl`
- `summary.json`
- `comparison_jev_only.md`

## 기존 HCX-003과 합쳐 비교

기존 HCX 결과 파일을 절대 덮어쓰지 않습니다.

예:

```powershell
python .\compare_with_existing_hcx.py `
  --hcx-jsonl "C:\...\results\20261001_120355\HCX-003.jsonl" `
  --jev-summary ".\results\jev_only_YYYYMMDD_HHMMSS\summary.json" `
  --out ".\HCX003_vs_JEV_final.md"
```

이 명령은 읽기만 하며 기존 HCX 파일을 수정하지 않습니다.

## 채점 조건

이전 HCX 평가와 동일한 Gold Set 및 동일한 핵심 지표를 사용합니다.

- 통계 분야 파악 정확도
- 분석 의도 F1
- 조건/기준 추출 F1
- 자료 주기 정확도
- 다중 지표 분리 개수 정확도
- 유효 출력률
- 3회 반복 일관성
- p50/p95 응답시간
- API 오류율

따라서 새 Jev 결과가 정상적으로 생성되면 기존 HCX 결과와 같은 표에서 비교할 수 있습니다.
