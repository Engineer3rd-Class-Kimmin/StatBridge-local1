from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from ncp_clova_client import _load_env


_load_env()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(slots=True)
class JevSettings:
    api_key: str = field(default_factory=lambda: _env("TYPESAFE_API_KEY"))
    endpoint: str = field(default_factory=lambda: _env("TYPESAFE_ENDPOINT", "https://api.typesafe.ai").rstrip("/"))
    model: str = field(default_factory=lambda: _env("STATBRIDGE_JEV_MODEL", "jev-preview") or "jev-preview")
    enabled: bool = field(default_factory=lambda: _env("STATBRIDGE_JEV_ENABLED", "1").lower() not in {"0", "false", "no", "off"})
    timeout: float = field(default_factory=lambda: float(_env("STATBRIDGE_JEV_TIMEOUT", "10") or "10"))


class JevSeriesError(RuntimeError):
    pass


class JevSeriesClient:
    """TypeSafe Jev client restricted to independent metric-count classification."""

    ALLOWED_COUNTS = {0, 2, 3, 4, 5}

    def __init__(self, settings: JevSettings | None = None, session: requests.Session | None = None) -> None:
        self.settings = settings or JevSettings()
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return self.settings.enabled and bool(self.settings.api_key)

    @property
    def url(self) -> str:
        endpoint = self.settings.endpoint.rstrip("/")
        return endpoint if endpoint.endswith("/v1/systemone") else endpoint + "/v1/systemone"

    def _state(self, query: str, classification: dict[str, Any]) -> str:
        return (
            f"원래 사용자 질문(판단의 최우선 근거): {query}\n"
            f"HCX concepts(보조 근거): {classification.get('concepts') or []}\n"
            f"HCX measures(보조 근거): {classification.get('measures') or []}\n"
            f"HCX series(보조 근거): {classification.get('series') or []}"
        )

    def request_body(self, query: str, classification: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.settings.model,
            "state": self._state(query, classification),
            "questions": {
                "series_count": {
                    "type": "choice",
                    "instructions": (
                        "원래 사용자 질문에 서로 다른 이름으로 명시된 통계 지표의 개수만 고른다. "
                        "서울과 부산의 고용률처럼 같은 지표를 여러 지역/대상에 적용하면 반드시 0이다. "
                        "서울, 부산, 대구의 소비자물가지수도 반드시 0이다. 전년 대비 증가율 같은 계산 요청도 0이다. "
                        "기준금리와 대출금리처럼 서로 다른 지표 이름이 둘 이상일 때만 2~5를 고른다. "
                        "기업경기 실적과 기업경기 전망처럼 별도 통계표로 독립 검색하는 실적/전망 계열은 2이다. "
                        "단일 지표는 1이 아니라 0이다. HCX series 개수보다 원래 질문의 지표 이름을 우선한다."
                    ),
                    "criteria": {
                        "0": "통계지표 이름이 하나. 지역/대상/시점이 여러 개여도 같은 지표면 이 항목",
                        "2": "서로 다른 통계지표 이름이 정확히 2개. 같은 지표의 두 지역 비교는 제외",
                        "3": "독립적으로 검색해야 할 통계지표가 3개",
                        "4": "독립적으로 검색해야 할 통계지표가 4개",
                        "5": "독립적으로 검색해야 할 통계지표가 5개",
                    },
                }
            },
        }

    def classify_series_count(self, query: str, classification: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.enabled:
            raise JevSeriesError("Jev is disabled")
        if not self.settings.api_key:
            raise JevSeriesError("TYPESAFE_API_KEY is not configured")
        started = time.perf_counter()
        try:
            response = self.session.post(
                self.url,
                headers={"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
                json=self.request_body(query, classification),
                timeout=self.settings.timeout,
            )
        except requests.RequestException as exc:
            raise JevSeriesError(f"TypeSafe request failed: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if response.status_code >= 400:
            raise JevSeriesError(f"TypeSafe HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
            answer = payload["answers"]["series_count"]
            choice = int(answer["choice"])
        except (ValueError, TypeError, KeyError) as exc:
            raise JevSeriesError("TypeSafe response has no valid series_count choice") from exc
        if choice not in self.ALLOWED_COUNTS:
            raise JevSeriesError(f"Unexpected TypeSafe series_count choice: {choice}")
        probabilities = answer.get("probabilities") or {}
        probability = probabilities.get(str(choice), answer.get("confidence"))
        try:
            probability = float(probability) if probability is not None else None
        except (TypeError, ValueError):
            probability = None
        return {"series_count": choice, "probability": probability, "latency_ms": latency_ms}
