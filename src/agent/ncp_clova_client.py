from __future__ import annotations

import json
import os
import re
import uuid
import copy
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "data" / "statbridge_mcp_server" / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            return
    load_dotenv(override=False)


_load_env()


@dataclass(slots=True)
class NcpSettings:
    api_key: str = (
        os.getenv("NCP_CLOVA_API_KEY", "").strip()
        or os.getenv("NCP_API_KEY", "").strip()
        or os.getenv("CLOVA_STUDIO_API_KEY", "").strip()
    )
    base_url: str = os.getenv("NCP_CLOVA_BASE_URL", "https://clovastudio.stream.ntruss.com").rstrip("/")
    classifier_model: str = os.getenv("NCP_CLASSIFIER_MODEL", "HCX-003").strip() or "HCX-003"
    classifier_api_version: str = os.getenv("NCP_CLASSIFIER_API_VERSION", "v1").strip() or "v1"
    classifier_url: str = os.getenv("NCP_CLASSIFIER_URL", "").strip()
    main_model: str = os.getenv("NCP_MAIN_MODEL", "HCX-007").strip() or "HCX-007"
    main_api_version: str = os.getenv("NCP_MAIN_API_VERSION", "v3").strip() or "v3"
    main_url: str = os.getenv("NCP_MAIN_URL", "").strip()
    timeout: int = int(os.getenv("NCP_TIMEOUT", "60"))
    main_thinking_effort: str = os.getenv("NCP_MAIN_THINKING_EFFORT", "low").strip() or "low"


class NcpClovaError(RuntimeError):
    pass


class NcpClovaClient:
    """Small CLOVA Studio client used by StatBridge.

    HCX-003 uses Chat Completions v1 by default.
    HCX-007 uses Chat Completions v3 by default.
    """

    def __init__(self, settings: NcpSettings | None = None, session: requests.Session | None = None) -> None:
        self.settings = settings or NcpSettings()
        self.session = session or requests.Session()
        self._classification_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @property
    def configured(self) -> bool:
        return bool(self.settings.api_key)

    def _url(self, model: str, version: str, override: str = "") -> str:
        if override:
            return override.format(model=model)
        return f"{self.settings.base_url}/{version}/chat-completions/{model}"

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise NcpClovaError("NCP_CLOVA_API_KEY(NCP_API_KEY)가 설정되지 않았습니다.")
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        try:
            return str(payload["result"]["message"]["content"])
        except Exception as exc:
            raise NcpClovaError(f"CLOVA 응답에서 message.content를 찾지 못했습니다: {payload}") from exc

    def _post(self, url: str, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            response = self.session.post(url, headers=self._headers(), json=body, timeout=self.settings.timeout)
        except requests.RequestException as exc:
            raise NcpClovaError(f"CLOVA 요청 실패: {exc}") from exc
        if response.status_code >= 400:
            raise NcpClovaError(f"CLOVA HTTP {response.status_code}: {response.text[:1200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NcpClovaError(f"CLOVA JSON 응답 파싱 실패: {response.text[:1200]}") from exc
        return self._content(payload), payload

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        raw = text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                return {}
            try:
                value = json.loads(m.group(0))
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}

    def classify_stat_language(self, query: str) -> dict[str, Any]:
        """HCX-003 role: natural Korean -> compact statistical language only.

        It never chooses API IDs. IDs are selected only from the dictionary afterwards.
        """
        cache_key=str(query).strip()
        if cache_key in self._classification_cache:
            self._classification_cache.move_to_end(cache_key)
            return copy.deepcopy(self._classification_cache[cache_key])
        system = (
            "너는 StatBridge 통계언어 분류기다. 사용자의 일상 자연어를 통계 검색에 필요한 표현으로만 정규화한다. "
            "통계표 ID, itmId, objL 코드, 숫자 데이터는 절대 추측하지 마라. "
            "반드시 JSON 객체 하나만 출력한다."
        )
        user = f"""사용자 질문: {query}\n\n다음 JSON 형식으로 분류하라. 모르는 값은 빈 배열로 둔다. 비교할 지표가 2~5개이면 series에 각각 독립적으로 검색 가능한 구체적 통계 지표를 넣고, 단일 지표면 series는 빈 배열로 둔다. '예금금리'는 수신금리이고 고정·변동 대출비중이 아니다.\n{{\n  \"normalized_query\": \"통계 검색에 유리한 짧은 문장\",\n  \"concepts\": [\"핵심 통계개념\"],\n  \"subjects\": [\"대상/부문/지역\"],\n  \"measures\": [\"잔액/비중/지수/증가율 등\"],\n  \"time_terms\": [\"최근5년/월별/2024년 등\"],\n  \"comparison_terms\": [\"전년대비/전월대비/추이 등\"],\n  \"qualifiers\": [\"계절조정/원계열/말잔/평잔/신규취급액/실적/전망 등\"],\n  \"series\": [{{\"label\": \"화면에 표시할 짧은 이름\", \"query\": \"사전에서 한 통계표를 찾기 위한 구체적 검색어\"}}]\n}}"""
        version = self.settings.classifier_api_version
        url = self._url(self.settings.classifier_model, version, self.settings.classifier_url)
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # HCX-003 Chat Completions v1 requires temperature to be greater
            # than zero. Keep it near-deterministic while remaining valid.
            "temperature": 0.01,
            "topP": 0.1,
            "topK": 0,
            "repetitionPenalty": 1.05,
        }
        if version == "v3":
            body["maxTokens"] = 700
        else:
            body["maxTokens"] = 700
        text, payload = self._post(url, body)
        parsed = self._json_object(text)
        parsed["_model"] = self.settings.classifier_model
        parsed["_url"] = url
        parsed["_raw_text"] = text
        parsed["_usage"] = (payload.get("result") or {}).get("usage") or {}
        self._classification_cache[cache_key]=copy.deepcopy(parsed)
        if len(self._classification_cache)>256:
            self._classification_cache.popitem(last=False)
        return parsed

    def clarify_question(self, original_query: str, clarification: dict[str, Any], state: dict[str, Any]) -> str:
        """HCX-007 role: turn dictionary-generated ambiguity/options into one concise user question."""
        fallback = str(clarification.get("question") or "어떤 항목을 말씀하시나요?")
        if not self.configured:
            return fallback
        options = [str(o.get("label") or o.get("value")) for o in clarification.get("options", [])]
        system = (
            "너는 StatBridge 메인 에이전트다. 통계 후보를 임의로 선택하지 않는다. "
            "사전이 준 선택지만 사용해서 한국어 역질문 한 문장만 만든다. 선택지를 추가하거나 삭제하지 마라."
        )
        user = (
            f"원질문: {original_query}\n"
            f"사전의 기본 역질문: {fallback}\n"
            f"버튼 선택지: {json.dumps(options, ensure_ascii=False)}\n"
            f"이미 확정된 조건: {json.dumps(state.get('confirmed') or {}, ensure_ascii=False)}\n"
            "사용자에게 보여줄 짧고 자연스러운 역질문만 출력하라."
        )
        try:
            text, _ = self.chat_main(system, user, max_tokens=180, thinking_effort="none")
            text = text.strip().replace("\n", " ")
            return text or fallback
        except Exception:
            return fallback

    def answer_with_data(self, query: str, plan: dict[str, Any], execution: dict[str, Any]) -> str:
        """HCX-007 role: main-agent answer grounded only in returned statistics."""
        rows = execution.get("rows") or []
        if not self.configured:
            return ""
        system = (
            "너는 StatBridge 메인 통계 에이전트다. 제공된 통계 조회 결과만 근거로 답한다. "
            "없는 수치나 원인을 추측하지 않는다. 질문과 직접 관련된 결론을 먼저 말하고, 필요한 기간/단위/기준을 짧게 덧붙인다."
        )
        compact_rows = rows[:60]
        user = (
            f"사용자 질문: {query}\n"
            f"확정 API 계획: {json.dumps(plan, ensure_ascii=False)}\n"
            f"조회 결과({len(rows)}행 중 최대 60행): {json.dumps(compact_rows, ensure_ascii=False, default=str)}\n"
            "한국어로 이해하기 쉽게 답하라. 데이터에 없는 해석은 하지 마라."
        )
        text, _ = self.chat_main(system, user, max_tokens=1800, thinking_effort=self.settings.main_thinking_effort)
        return text.strip()

    def chat_main(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1800,
        thinking_effort: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        version = self.settings.main_api_version
        url = self._url(self.settings.main_model, version, self.settings.main_url)
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "topP": 0.8,
            "topK": 0,
            "temperature": 0.2,
            "repetitionPenalty": 1.05,
        }
        if version == "v3" and self.settings.main_model.upper() == "HCX-007":
            effort = (thinking_effort or self.settings.main_thinking_effort or "low").lower()
            body["thinking"] = {"effort": effort}
            body["maxCompletionTokens"] = max_tokens
        else:
            body["maxTokens"] = max_tokens
        return self._post(url, body)
