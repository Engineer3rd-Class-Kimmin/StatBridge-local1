from __future__ import annotations

import random
import time
from collections import deque
from typing import Any

import requests

from .config import settings

PARAM_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
META_URL = "https://kosis.kr/openapi/statisticsData.do"


class KosisApiError(RuntimeError):
    def __init__(self, code: str, msg: str, payload: Any = None):
        super().__init__(f"KOSIS 오류 {code}: {msg}")
        self.code = str(code)
        self.msg = str(msg)
        self.payload = payload


class RateLimiter:
    def __init__(self, per_minute: int, min_delay: float = 0.35, max_delay: float = 0.65):
        self.per_minute = max(1, per_minute)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.calls = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.per_minute:
            sec = 60 - (now - self.calls[0]) + random.uniform(0.2, 0.8)
            time.sleep(max(0.0, sec))
        time.sleep(random.uniform(self.min_delay, self.max_delay))
        self.calls.append(time.monotonic())


class KosisClient:
    def __init__(self) -> None:
        if not settings.kosis_api_key:
            raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")
        self.api_key = settings.kosis_api_key
        self.timeout = settings.timeout
        self.session = requests.Session()
        self.limiter = RateLimiter(settings.rate_limit_per_minute)

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        self.limiter.wait()
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        # KOSIS JSON is UTF-8, but the response content type can omit a charset.
        # requests may otherwise decode Korean labels as ISO-8859-1 mojibake.
        response.encoding = "utf-8"
        payload = response.json()
        if isinstance(payload, dict) and "err" in payload:
            raise KosisApiError(payload.get("err", ""), payload.get("errMsg", ""), payload)
        return payload

    def get_prd_meta(self, org_id: str, table_id: str) -> list[dict[str, Any]]:
        params = {
            "method": "getMeta",
            "type": "PRD",
            "apiKey": self.api_key,
            "orgId": org_id,
            "tblId": table_id,
            "format": "json",
            "jsonVD": "Y",
            "detail": "Y",
        }
        payload = self._get(META_URL, params)
        return payload if isinstance(payload, list) else []

    def get_statistics(
        self,
        org_id: str,
        table_id: str,
        item_id: str,
        frequency: str,
        start_period: str,
        end_period: str,
        classifications: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "method": "getList",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "smblChk": "Y",
            "orgId": org_id,
            "tblId": table_id,
            "itmId": item_id,
            "prdSe": frequency,
            "startPrdDe": start_period,
            "endPrdDe": end_period,
        }

        # 사용하지 않는 objL은 절대로 빈 문자열로 보내지 않는다.
        for key, value in (classifications or {}).items():
            if value not in ("", None):
                params[key] = value

        payload = self._get(PARAM_URL, params)
        if not isinstance(payload, list):
            raise RuntimeError(f"예상치 못한 응답 타입: {type(payload).__name__}")
        return payload
