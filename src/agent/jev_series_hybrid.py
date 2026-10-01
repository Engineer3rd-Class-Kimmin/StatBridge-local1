from __future__ import annotations

import copy
import re
from typing import Any


_METRIC_MARKERS = (
    "금리", "지수", "률", "율", "비율", "비중", "인구", "소득", "임금", "가격", "물가",
    "생산", "수출", "수입", "고용", "실업", "취업", "대출", "예금", "잔액", "부채", "매출",
    "투자", "소비", "성장", "출생", "사망", "혼인", "가구", "주택", "거래", "GDP", "GNI",
)
_REGION_ONLY = re.compile(r"^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)(시|도)?$")


def _valid_series(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        query = str(item.get("query") or label).strip()
        if label and query:
            result.append({"label": label, "query": query})
    return result


def _safe_metric_candidates(classification: dict[str, Any], expected: int) -> list[dict[str, str]]:
    concepts = classification.get("concepts") or []
    measures = [str(x).strip() for x in (classification.get("measures") or []) if str(x).strip()]
    normalized = str(classification.get("normalized_query") or "").strip()
    candidates: list[str] = []
    if isinstance(concepts, list):
        candidates.extend(str(x).strip() for x in concepts if str(x).strip())
    candidates = list(dict.fromkeys(x for x in candidates if not _REGION_ONLY.fullmatch(x)))
    if len(candidates) != expected:
        return []
    # Every candidate must independently look like a statistical metric. Shared
    # measure suffixes (e.g. 기준/예금/대출 + 금리) may be supplied by HCX measures.
    shared_measure = measures[0] if len(measures) == 1 else ""
    result: list[dict[str, str]] = []
    for candidate in candidates:
        expanded = candidate
        if shared_measure and shared_measure not in candidate and not any(marker in candidate for marker in _METRIC_MARKERS):
            expanded = candidate + " " + shared_measure
        if not any(marker.lower() in expanded.lower() for marker in _METRIC_MARKERS):
            return []
        query = " ".join(x for x in (expanded, normalized) if x).strip()
        result.append({"label": candidate, "query": query})
    return result


def apply_jev_series_decision(
    classification: dict[str, Any], decision: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(classification)
    before = _valid_series(result.get("series"))
    count = int(decision["series_count"])
    trace = {
        "jev_series_count": count,
        "jev_probability": decision.get("probability"),
        "hcx_series_count_before": len(before),
        "final_series_count": len(before),
        "series_action": "kept",
    }
    if count == 0:
        result["series"] = []
        trace.update(final_series_count=0, series_action="cleared")
    elif len(before) == count:
        result["series"] = before
    elif len(before) < count:
        repaired = _safe_metric_candidates(result, count)
        if repaired:
            result["series"] = repaired
            trace.update(final_series_count=len(repaired), series_action="repaired")
        else:
            trace["series_action"] = "not_safe"
    else:
        # Jev only judges count; it cannot safely choose which HCX labels to drop.
        trace["series_action"] = "not_safe"
    return result, trace
