# StatBridge Frontend

한국은행 통계 탐색·비교·근거 추적 흐름을 보여주는 반응형 React/Vite UI입니다.

## 실행

```bash
cd src/agent/frontend
pnpm install
pnpm dev
```

기본값은 `src/api/mock.ts`의 데모 응답을 사용하므로 백엔드 없이도 실행됩니다.

## 백엔드 연결

`.env.example`을 `.env`로 복사한 뒤 아래처럼 설정합니다.

```env
VITE_API_BASE_URL=/api
VITE_USE_MOCK=false
```

프론트엔드는 `POST /api/query`를 호출합니다.

```json
{
  "query": "2020년 이후 한국의 기준금리와 소비자물가상승률을 비교해줘"
}
```

응답 타입은 `src/api/types.ts`의 `QueryResponse`를 기준으로 합니다. 실제 백엔드 응답이 다른 경우 계약 파일을 변경하지 말고 `src/api/client.ts`에서 어댑트하세요. 로컬 개발 프록시는 `vite.config.ts`에서 백엔드 주소를 변경할 수 있습니다.

## 반응형 기준

- 1180px 이하: 결과 영역 축소, 기능 카드 2열
- 920px 이하: 사이드바를 모바일 드로어로 전환, 결과 근거 카드 2열
- 680px 이하: 단일 열, 가로 스크롤형 차트·표·추천 질문
