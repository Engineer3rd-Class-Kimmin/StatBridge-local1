from __future__ import annotations

import os
import re
import sys
import time
import calendar
from uuid import uuid4
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = ROOT / "data" / "statbridge_mcp_server"
DATA_DIR = ROOT / "data" / "runtime_data" / "processed"

os.environ.setdefault("STATBRIDGE_DATA_DIR", str(DATA_DIR))
os.environ.setdefault("STATBRIDGE_TABLES_DIR", str(ROOT / "data" / "runtime_data" / "tables"))
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from statbridge_mcp.statistics_service import StatisticsService
from agent_runtime import StatBridgeAgent
from mcp_gateway import McpToolGateway


class _NoKeyClient:
    """Keeps dictionary/MCP metadata planning usable until KOSIS_API_KEY is configured."""
    def get_prd_meta(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")

    def get_statistics(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")

app = FastAPI(title="StatBridge Agent API", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:5174", "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The agent is the only UI-facing orchestration layer. It calls the same service exposed as MCP tools.
try:
    service = StatisticsService()
except RuntimeError as exc:
    if "KOSIS_API_KEY" not in str(exc):
        raise
    service = StatisticsService(client=_NoKeyClient())
mcp_gateway = McpToolGateway(service)
agent = StatBridgeAgent(service=mcp_gateway)


class ClarificationSelection(BaseModel):
    clarification_id: str
    value: str


class QueryRequest(BaseModel):
    query: str
    state: dict[str, Any] | None = None
    clarification: ClarificationSelection | None = None
    selections: list[dict[str, Any]] | None = None
    execute: bool = True
    period_start: str | None = None
    period_end: str | None = None
    chart_mode: str | None = None
    chart_type: str | None = None
    chart_options: dict[str, Any] | None = None


class OutputRenderRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1)
    chart_type: str
    chart_mode: str = "combined"
    title: str | None = None
    show_legend: bool = True
    x_axis_label: str | None = None
    y_axis_label: str | None = None


OUTPUT_SESSIONS: dict[str, dict[str, Any]] = {}


def _table_card(table_id: str) -> dict[str, str]:
    try:
        meta = service.get_table_metadata(table_id, live_period_fallback=False)
        items = meta.get("items") or []
        item = str(items[0].get("item_name") or "통계표 후보") if items else "통계표 후보"
        return {
            "tableId": table_id,
            "name": str(meta.get("table_name") or table_id),
            "source": "한국은행 · KOSIS",
            "item": item,
            "unit": "-",
        }
    except Exception:
        return {"tableId": table_id, "name": table_id, "source": "한국은행 · KOSIS", "item": "통계표", "unit": "-"}


DOMAIN_LABELS = {
    "interest_rates": "금리", "money_liquidity": "통화·유동성", "deposits_loans": "예금·대출",
    "payments": "지급결제", "trade_currency": "결제통화", "national_accounts": "국민계정",
    "prices_trade": "물가·무역", "producer_prices": "생산자물가", "corporate_finance": "기업금융",
    "business_sentiment": "기업경기", "consumer_sentiment": "소비자동향", "economic_sentiment": "경제심리",
    "lending_survey": "대출행태", "balance_payments": "국제수지", "other": "기타"
}
MAJOR_DOMAINS = {
    "금융·통화": {"interest_rates", "money_liquidity", "deposits_loans", "payments", "trade_currency"},
    "국민경제·물가": {"national_accounts", "prices_trade", "producer_prices"},
    "기업·가계 경기": {"corporate_finance", "business_sentiment", "consumer_sentiment", "economic_sentiment", "lending_survey"},
    "대외경제": {"balance_payments"},
}


def _major_domain(domain: str) -> str:
    return next((major for major, domains in MAJOR_DOMAINS.items() if domain in domains), "기타")


def _friendly_dimension_name(name: str, examples: list[str], index: int) -> str:
    """Return a label meant for people, never an API parameter such as objL1."""
    cleaned = str(name or "").strip()
    joined = " ".join(examples)
    if any(token in joined for token in ("금액", "비중(%)", "비중")):
        return "표시 기준"
    if any(token in joined for token in ("달러", "유로", "엔화", "원화", "파운드", "위안")) and "(" in joined:
        return "국가·결제통화"
    if cleaned and cleaned not in {"계정코드별", "항목별", "분류별", "선택항목"}:
        return cleaned.removesuffix("별") or cleaned
    return f"분류 {index}"


def _catalog_card(table: dict[str, Any], metadata: Any | None = None) -> dict[str, Any]:
    dimensions = table.get("dimensions") or []
    frequency = str(table.get("prd_se") or "-")
    frequency_labels = {"D": "일", "M": "월", "Q": "분기", "H": "반기", "S": "반기", "Y": "년", "A": "년"}
    # Return complete metadata. Compact previews belong in the UI so the
    # lineage card can still expose every source value on demand.
    units = [str(x) for x in (table.get("units") or []) if str(x)]
    unit_scale = next((x for x in units if any(token in x for token in ("조", "억", "백만", "천", "원", "%", "지수"))), units[0] if units else "-")
    metadata_dimensions = list(getattr(metadata, "dimensions", None) or [])
    public_dimensions: list[dict[str, Any]] = []
    for index, dimension in enumerate(dimensions, start=1):
        values = dimension.get("values") or []
        dimension_values = [str(v.get("normalized") or v.get("value_name") or "") for v in values]
        meta_dimension = metadata_dimensions[index - 1] if index <= len(metadata_dimensions) else {}
        meta_values = meta_dimension.get("values") or []
        if meta_values:
            dimension_values = [str(v.get("class_name") or "") for v in meta_values]
        dimension_values = [value for value in dimension_values if value]
        raw_name = str(meta_dimension.get("obj_name") or dimension.get("dimension_name") or "")
        public_dimensions.append({
            "name": _friendly_dimension_name(raw_name, dimension_values, index),
            "count": len(meta_values) if meta_values else len(values),
            "values": dimension_values,
        })

    return {
        "tableId": str(table.get("table_id") or ""), "name": str(table.get("table_name") or ""),
        "organization": "한국은행 · KOSIS", "frequency": frequency,
        "frequencyLabel": frequency_labels.get(frequency, frequency), "unitScale": unit_scale,
        "periodStart": str(table.get("period_start_observed") or "-"),
        "periodEnd": str(table.get("period_end_observed") or "-"),
        "items": [str(x) for x in (table.get("item_names") or []) if str(x)],
        "units": units,
        "dimensions": public_dimensions,
    }


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    metadata_by_id = {str(meta.table_id): meta for meta in service.store.available_supported_tables()}
    supported = set(metadata_by_id)
    for table in agent.resolver.tables:
        table_id = str(table.get("table_id") or "")
        if table_id not in supported:
            continue
        domain = str((table.get("domains") or ["other"])[0])
        major = _major_domain(domain)
        middle = DOMAIN_LABELS.get(domain, domain)
        grouped.setdefault(major, {}).setdefault(middle, []).append(_catalog_card(table, metadata_by_id.get(table_id)))
    categories = []
    for major in ("금융·통화", "국민경제·물가", "기업·가계 경기", "대외경제", "기타"):
        middles = grouped.get(major) or {}
        if not middles:
            continue
        categories.append({"name": major, "count": sum(len(v) for v in middles.values()),
                           "children": [{"name": name, "count": len(cards),
                                         "children": sorted(cards, key=lambda x: (x["name"], x["tableId"]))}
                                        for name, cards in sorted(middles.items())]})
    return {"source": "StatBridge 검증 통계사전", "total": sum(x["count"] for x in categories), "categories": categories}


def _rows_preview(execution: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    rows = execution.get("rows") or []
    return [dict(x) for x in rows[:limit] if isinstance(x, dict)]


def _api_period(value: str | None, frequency: str, *, end: bool = False) -> str | None:
    """Convert an exact UI date (YYYY-MM-DD) to the KOSIS period format."""
    if not value:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not match:
        raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
    year, month = int(match.group(1)), int(match.group(2))
    if frequency == "D":
        return value.replace("-", "")
    if frequency == "M":
        return f"{year:04d}{month:02d}"
    if frequency == "Q":
        return f"{year:04d}{((month - 1) // 3) + 1:02d}"
    if frequency in {"H", "S"}:
        return f"{year:04d}{1 if month <= 6 else 2:02d}"
    return f"{year:04d}"


def _ui_period(value: str | None, frequency: str, *, end: bool = False) -> str | None:
    """Convert a KOSIS period into an inclusive HTML date boundary."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 4:
        return None
    year = int(digits[:4])
    if frequency == "M":
        month = max(1, min(12, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    if frequency == "Q":
        quarter = max(1, min(4, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        month = quarter * 3 if end else (quarter - 1) * 3 + 1
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    if frequency in {"H", "S"}:
        half = max(1, min(2, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        month = half * 6 if end else (half - 1) * 6 + 1
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{year:04d}-12-31" if end else f"{year:04d}-01-01"


def _availability_from_plans(plans: list[dict[str, Any]]) -> dict[str, str]:
    starts: list[str | None] = []
    ends: list[str | None] = []
    for plan in plans:
        table = agent.resolver.tables_by_id.get(str(plan.get("table_id") or "")) or {}
        frequency = str(plan.get("frequency") or table.get("prd_se") or "")
        starts.append(_ui_period(str(table.get("period_start_observed") or plan.get("start_period") or ""), frequency))
        ends.append(_ui_period(str(table.get("period_end_observed") or plan.get("end_period") or ""), frequency, end=True))
    valid_starts = [x for x in starts if x]
    valid_ends = [x for x in ends if x]
    return {"min": max(valid_starts) if valid_starts else "", "max": min(valid_ends) if valid_ends else ""}


def _availability_from_table_ids(table_ids: list[str]) -> dict[str, str]:
    plans: list[dict[str, Any]] = []
    for table_id in table_ids:
        table = agent.resolver.tables_by_id.get(str(table_id))
        if not table:
            continue
        plans.append({
            "frequency": str(table.get("prd_se") or ""),
            "start_period": str(table.get("period_start_observed") or ""),
            "end_period": str(table.get("period_end_observed") or ""),
        })
    return _availability_from_plans(plans)


def _resolve_selected_options(query_text: str, state: dict[str, Any], selections: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve button selections deterministically, without a second HCX/vector round-trip."""
    groups = [(str(group.get("clarification_id") or ""), [str(v) for v in (group.get("values") or []) if str(v)])
              for group in selections]
    groups = [(group_id, values) for group_id, values in groups if group_id and values]
    combinations = list(product(*[[(group_id, value) for value in values] for group_id, values in groups]))[:5]
    resolved_parts: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    for combination in combinations:
        next_state = dict(state)
        confirmed = dict(next_state.get("confirmed") or {})
        confirmed.update(dict(combination))
        next_state["confirmed"] = confirmed
        part = agent.run(query=query_text, state=next_state, execute=False, generate_answer=False)
        if part.get("status") != "resolved":
            continue
        plan = dict(part.get("api_plan") or {})
        if not plan:
            continue
        plan["series_label"] = " · ".join(value for _, value in combination)
        key = (str(plan.get("table_id")), str(plan.get("item_id")), tuple(sorted((plan.get("classifications") or {}).items())))
        if key not in seen:
            seen.add(key)
            plans.append(plan)
            resolved_parts.append(part)
    if not plans:
        return {"status": "no_match", "missing_series": [value for _, values in groups for value in values]}
    base = resolved_parts[0]
    base["api_plan"] = plans[0]
    base["api_plans"] = plans
    base["selected_table"] = base.get("selected_table") or {}
    return base


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "ui-agent-dictionary-mcp",
        "agent": "StatBridgeAgent-v6-ncp",
        "orchestration_engine": "langgraph",
        "orchestration_nodes": [
            "resolve_request", "await_clarification", "finish_unresolved", "execute_statistics",
            "await_output_selection", "prepare_output",
        ],
        "ncp_api_key_configured": agent.ncp.configured,
        "jev_enabled": agent.jev.settings.enabled,
        "jev_api_key_configured": bool(agent.jev.settings.api_key),
        "jev_model": agent.jev.settings.model,
        "classifier_model": agent.ncp.settings.classifier_model,
        "main_model": agent.ncp.settings.main_model,
        "dictionary_tables": len(agent.resolver.tables),
        "clarification_groups": len(agent.resolver.groups),
        "supported_tables": len(service.store.available_supported_tables()),
        "kosis_api_key_configured": bool(os.getenv("KOSIS_API_KEY", "").strip()),
        "data_dir": str(service.store.data_dir),
    }


@app.post("/api/query")
def query(payload: QueryRequest) -> dict[str, Any]:
    request_started = time.perf_counter()
    q = payload.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    # Resolve the table first. Numeric execution waits until the user has
    # explicitly chosen the chart period.
    selected_values = [str(value).strip() for group in (payload.selections or [])
                       for value in (group.get("values") or []) if str(value).strip()]
    agent_query = q
    agent_state = payload.state
    agent_clarification = payload.clarification.model_dump() if payload.clarification else None
    if selected_values:
        agent_query = f"{q} {' '.join(selected_values)}" + (" 비교" if len(selected_values) > 1 else "")
        result = _resolve_selected_options(q, dict(payload.state or {}), payload.selections or [])
    else:
        result = agent.run(
            query=agent_query,
            state=agent_state,
            clarification=agent_clarification,
            execute=False,
        )
    resolve_ms = round((time.perf_counter() - request_started) * 1000)

    if result.get("status") == "need_clarification":
        candidate_ids = [str(x.get("table_id") or "") for x in (result.get("state", {}).get("candidate_tables") or [])]
        available_period = _availability_from_table_ids(candidate_ids)
        raw_groups = result.get("clarifications") or [result]
        clarification_groups = [{
            "id": group["clarification_id"], "question": group["question"],
            "ui": "multi_select_buttons",
            "options": [{"label": o["label"], "value": o["value"]} for o in group.get("options", [])],
        } for group in raw_groups]
        return {
            "status": "need_clarification",
            "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "clarification": {
                "id": result["clarification_id"],
                "question": result["question"],
                "ui": result.get("ui", "single_select_buttons"),
                "options": [
                    {"label": o["label"], "value": o["value"]}
                    for o in result.get("options", [])
                ],
            },
            "clarifications": clarification_groups,
            "state": result["state"],
            "summary": "HCX-003가 자연어를 통계언어로 정리한 뒤 사전에서 여러 후보가 남았습니다. HCX-007이 사전 선택지를 바탕으로 한 번에 하나의 역질문을 합니다.",
            "period": {"start": "-", "end": "-"},
            "availablePeriod": available_period,
            "frequency": "확인 필요",
            "chart": [],
            "tables": [
                _table_card(x["table_id"])
                for x in (result.get("state", {}).get("candidate_tables") or [])[:4]
            ],
            "insights": ["Agent가 임의로 통계를 선택하지 않고 사용자 확인을 요청했습니다."],
            "lineage": [
                {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
                {"id": "hcx003", "title": "HCX-003 통계언어 분류", "description": str((result.get("classification") or {}).get("normalized_query") or q), "status": "complete"},
                {"id": "dictionary", "title": "통계언어 사전 검색", "description": "v5 사전에서 후보 생성", "status": "complete"},
                {"id": "hcx007", "title": "HCX-007 메인 Agent", "description": "사전 후보의 차이를 사용자에게 역질문", "status": "complete"},
                {"id": "clarify", "title": "역질문", "description": result["question"], "status": "active"},
            ],
            "warnings": [],
            "debug": {"timingMs": {"resolve": resolve_ms, "total": resolve_ms}},
        }

    if result.get("status") == "catalog_only":
        table = result.get("selected_table") or {}
        return {
            "status": "catalog_only", "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "summary": "통계표는 카탈로그에서 확인되지만 현재 로컬 조회 데이터가 없어 수치 조회를 중단했습니다.",
            "period": {"start": "-", "end": "-"}, "frequency": "-", "chart": [],
            "tables": [{**_table_card(str(table.get("table_id") or "")), "name": str(table.get("table_name") or table.get("table_id") or "통계표"), "source": "한국은행 KOSIS 카탈로그"}],
            "insights": [], "warnings": ["카탈로그 전용 표입니다. 로컬 메타데이터와 조회 파라미터가 확보되기 전에는 KOSIS 수치를 호출하지 않습니다."],
        }

    if result.get("status") != "resolved":
        missing = [str(x) for x in result.get("missing_series") or []]
        missing_text = f" 지원 데이터에서 찾지 못한 지표: {', '.join(missing)}." if missing else ""
        return {
            "status": "no_match",
            "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "summary": "요청한 모든 지표에 대응하는 통계표를 찾지 못했습니다." + missing_text,
            "period": {"start": "-", "end": "-"},
            "frequency": "-",
            "chart": [], "tables": [], "insights": [],
            "lineage": [
                {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
                {"id": "agent", "title": "Agent 사전 검색", "description": "후보 없음", "status": "active"},
            ],
            "warnings": ["일부 지표를 다른 표현으로 바꾸거나 지원되는 지표만 선택해 주세요."],
        }

    plan = result["api_plan"]
    plans = result.get("api_plans") or [plan]
    table_name = " · ".join(str(x.get("series_label") or x["table_name"]) for x in plans)
    frequencies = list(dict.fromkeys(str(x["frequency"]) for x in plans))
    frequency_label = "/".join(frequencies)
    if not payload.period_start or not payload.period_end:
        available_period = _availability_from_plans(plans)
        return {
            "status": "need_period",
            "query": q,
            "interpretedQuery": table_name,
            "summary": "통계표를 찾았습니다. 그래프로 볼 정확한 시작일과 종료일을 입력해 주세요.",
            "period": {"start": available_period["min"], "end": available_period["max"]},
            "availablePeriod": available_period,
            "frequency": frequency_label,
            "chart": [],
            "chartMode": payload.chart_mode or "combined",
            "tables": [_table_card(x["table_id"]) for x in plans],
            "insights": [],
            "lineage": [
                {"id": "table", "title": "통계표 확정", "description": table_name, "status": "complete"},
                {"id": "period", "title": "조회 기간", "description": "사용자 입력 대기", "status": "active"},
            ],
            "warnings": [],
            "state": result.get("state") or {},
            "debug": {"timingMs": {"resolve": resolve_ms, "total": resolve_ms}},
        }

    available_period = _availability_from_plans(plans)
    selected_start = payload.period_start
    selected_end = payload.period_end
    adjusted_period = False
    if available_period["min"] and selected_start < available_period["min"]:
        selected_start = available_period["min"]
        adjusted_period = True
    if available_period["max"] and selected_end > available_period["max"]:
        selected_end = available_period["max"]
        adjusted_period = True
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="선택한 통계표들이 함께 제공되는 기간이 없습니다.")

    period_overrides = {
        str(x["table_id"]): (
            str(_api_period(selected_start, str(x["frequency"])) or ""),
            str(_api_period(selected_end, str(x["frequency"]), end=True) or ""),
        ) for x in plans
    }
    if any(start > end for start, end in period_overrides.values()):
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 늦을 수 없습니다.")
    execution_started = time.perf_counter()
    result = agent.run_resolution(
        query=agent_query,
        resolution=result,
        execute=payload.execute,
        period_overrides=period_overrides,
        generate_answer=False,
        output_request={
            **(payload.chart_options or {}),
            "chart_type": payload.chart_type,
            "layout": payload.chart_mode or "combined",
        },
    )
    execute_ms = round((time.perf_counter() - execution_started) * 1000)

    plan = result["api_plan"]
    plans = result.get("api_plans") or [plan]
    table_name = " · ".join(str(x.get("series_label") or x["table_name"]) for x in plans)
    frequencies = list(dict.fromkeys(str(x["frequency"]) for x in plans))
    frequency_label = "/".join(frequencies)
    execution = result.get("execution") or {}
    selected = result["selected_table"]
    candidates = result.get("candidates") or []
    execution_status = execution.get("status")

    output = result.get("output") or {}
    visualization = output.get("visualization") or {}
    chart = visualization.get("series") or []
    if execution_status == "success" and not output:
        session_id = uuid4().hex
        OUTPUT_SESSIONS[session_id] = {
            "created": time.time(), "result": result, "query": q,
            "table_name": table_name, "frequency": frequency_label,
            "period": {"start": selected_start, "end": selected_end},
            "available_period": available_period, "adjusted_period": adjusted_period,
            "resolve_ms": resolve_ms, "execute_ms": execute_ms,
        }
        inspection = agent.output_agent.inspect(result)
        return {
            "status": "need_output_config",
            "query": q,
            "interpretedQuery": table_name,
            "summary": "MCP 데이터 조회가 끝났습니다. 출력할 그래프 종류와 편집 옵션을 선택해 주세요.",
            "period": {"start": selected_start, "end": selected_end},
            "availablePeriod": available_period,
            "frequency": frequency_label,
            "chart": [],
            "chartMode": None,
            "chartType": None,
            "seriesCount": inspection["seriesCount"],
            "outputSessionId": session_id,
            "outputOptions": inspection,
            "tables": [_table_card(x["table_id"]) for x in plans],
            "insights": [f"MCP 조회 완료: {execution.get('row_count', 0)}개 행"],
            "lineage": [
                {"id": "mcp", "title": "MCP 통계 데이터", "description": f"{execution.get('row_count', 0)}개 행 수신", "status": "complete"},
                {"id": "output", "title": "출력 Agent", "description": "그래프 주문 대기", "status": "active"},
            ],
            "warnings": [],
        }
    if execution_status == "success":
        summary = str(output.get("summary") or "출력 에이전트가 그래프 명세를 생성했습니다.")
        warnings: list[str] = ([f"선택 기간을 원자료 제공 범위({available_period['min']}–{available_period['max']})에 맞춰 조정했습니다."] if adjusted_period else [])
    elif execution_status == "planned_only":
        summary = f"Agent가 '{plan['table_name']}'을 선택해 정확한 KOSIS API 호출 파라미터까지 만들었습니다. 실행은 요청에 따라 생략했습니다."
        warnings = []
    else:
        summary = f"Agent가 '{plan['table_name']}'과 실제 사전의 API ID를 확정했습니다. KOSIS 실행 단계에서 오류가 발생했지만 생성된 요청 파라미터는 확인할 수 있습니다."
        warnings = [str(execution.get("error") or "KOSIS API 실행 오류")]

    return {
        "status": "data_unavailable" if execution_status not in {"success", "planned_only"} else "resolved",
        "query": q,
        "interpretedQuery": table_name,
        "summary": summary,
        "period": {"start": selected_start, "end": selected_end},
        "availablePeriod": available_period,
        "frequency": frequency_label,
        "chart": chart,
        "chartMode": visualization.get("layout") or payload.chart_mode or "combined",
        "chartType": visualization.get("chartType") or payload.chart_type or "line",
        "outputSpec": output,
        "tables": [_table_card(x["table_id"]) for x in plans],
        "insights": [
            f"선택 통계표: {len(plans)}개",
            *[f"{x.get('series_label') or x['table_name']}: {x['table_name']} ({x['table_id']})" for x in plans],
        ],
        "lineage": [
            {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
            {"id": "hcx003", "title": "HCX-003 자연어 → 통계언어", "description": str((result.get("classification") or {}).get("normalized_query") or result.get("dictionary_query") or q), "status": "complete"},
            {"id": "dictionary", "title": "통계언어 사전 검색", "description": f"347개 통계표 사전에서 {len(candidates)}개 후보 평가", "status": "complete"},
            {"id": "hcx007", "title": "HCX-007 메인 Agent", "description": f"{len(plans)}개 비교 계열 확정 및 답변 처리", "status": "complete"},
            {"id": "mcp", "title": "MCP 통계 서비스", "description": "검증된 table/item/objL/기간 파라미터 전달", "status": "complete" if execution_status in {"success", "planned_only"} else "active"},
            {"id": "kosis", "title": "KOSIS API", "description": execution_status or "unknown", "status": "complete" if execution_status == "success" else "active"},
            {"id": "output", "title": "출력 Agent", "description": f"{visualization.get('chartType') or 'chart'} 그래프 명세와 편집 옵션 생성", "status": "complete" if output.get("status") == "ready" else "active"},
        ],
        "warnings": warnings,
        "state": result.get("state") or payload.state or {},
        "debug": {
            "selectedTable": selected,
            "apiPlan": plan,
            "executionStatus": execution_status,
            "rowsPreview": _rows_preview(execution),
            "classification": result.get("classification"),
            "dictionaryQuery": result.get("dictionary_query"),
            "ncpModels": {"classifier": agent.ncp.settings.classifier_model, "main": agent.ncp.settings.main_model},
            "timingMs": {"resolve": resolve_ms, "execute": execute_ms, "total": round((time.perf_counter() - request_started) * 1000)},
        },
    }


@app.post("/api/output")
def render_output(payload: OutputRenderRequest) -> dict[str, Any]:
    sessions = []
    for session_id in payload.session_ids:
        session = OUTPUT_SESSIONS.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="output session not found or expired")
        sessions.append(session)

    base = sessions[0]
    results = [session["result"] for session in sessions]
    merged = dict(results[0])
    merged_execution = {
        "status": "success",
        "rows": [row for result in results for row in ((result.get("execution") or {}).get("rows") or [])],
        "row_count": sum(int((result.get("execution") or {}).get("row_count") or 0) for result in results),
        "sources": [source for result in results for source in ((result.get("execution") or {}).get("sources") or [])],
    }
    merged["execution"] = merged_execution
    merged["api_plans"] = [plan for result in results for plan in (result.get("api_plans") or [result.get("api_plan")]) if plan]
    merged["candidates"] = [candidate for result in results for candidate in (result.get("candidates") or [])]
    output_request = {
        "chart_type": payload.chart_type,
        "layout": payload.chart_mode,
        "title": payload.title,
        "show_legend": payload.show_legend,
        "x_axis_label": payload.x_axis_label,
        "y_axis_label": payload.y_axis_label,
    }
    rendered = agent.render_output(merged, output_request)
    output = rendered.get("output") or {}
    visualization = output.get("visualization") or {}
    chart = visualization.get("series") or []
    plans = rendered.get("api_plans") or [rendered.get("api_plan")]
    plans = [plan for plan in plans if plan]
    table_name = " · ".join(str(session["table_name"]) for session in sessions)
    starts = sorted(str(session["period"]["start"]) for session in sessions)
    ends = sorted(str(session["period"]["end"]) for session in sessions)
    frequencies = list(dict.fromkeys(str(session["frequency"]) for session in sessions))
    candidates = rendered.get("candidates") or []
    adjusted = any(bool(session.get("adjusted_period")) for session in sessions)
    warnings = (["선택 기간 일부를 원자료 제공 범위에 맞춰 조정했습니다."] if adjusted else [])
    response = {
        "status": "resolved",
        "query": " · ".join(str(session["query"]) for session in sessions),
        "interpretedQuery": table_name,
        "summary": str(output.get("summary") or "출력 에이전트가 그래프 명세를 생성했습니다."),
        "period": {"start": starts[0], "end": ends[-1]},
        "frequency": " / ".join(frequencies),
        "chart": chart,
        "chartMode": visualization.get("layout") or payload.chart_mode,
        "chartType": visualization.get("chartType") or payload.chart_type,
        "outputSpec": output,
        "seriesCount": len(chart),
        "tables": [_table_card(str(plan["table_id"])) for plan in plans],
        "insights": [
            f"출력 Agent가 MCP {merged_execution['row_count']}개 행을 {len(chart)}개 계열로 변환",
            *[f"{plan.get('series_label') or plan['table_name']}: {plan['table_id']}" for plan in plans],
        ],
        "lineage": [
            {"id": "input", "title": "입력 Agent", "description": "통계표·기간 확정", "status": "complete"},
            {"id": "mcp", "title": "MCP 통계 서비스", "description": f"{merged_execution['row_count']}개 행 전달", "status": "complete"},
            {"id": "output", "title": "출력 Agent", "description": f"{visualization.get('chartType')} 그래프와 편집 명세 생성", "status": "complete"},
        ],
        "warnings": warnings,
        "debug": {
            "executionStatus": "success",
            "orchestration": rendered.get("orchestration"),
            "candidateCount": len(candidates),
        },
    }
    for session_id in payload.session_ids:
        OUTPUT_SESSIONS.pop(session_id, None)
    return response
