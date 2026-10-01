from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from stat_dictionary.stat_language_resolver import StatLanguageResolver
from ncp_clova_client import NcpClovaClient
from hybrid_retriever import HybridStatRetriever
from langgraph_workflow import StatBridgeWorkflow
from output_agent import OutputAgent
from jev_series_client import JevSeriesClient
from jev_series_hybrid import apply_jev_series_decision


@dataclass(slots=True)
class ApiPlan:
    table_id: str
    table_name: str
    org_id: str
    item_id: str
    frequency: str
    start_period: str
    end_period: str
    classifications: dict[str, str]
    exact_params: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "table_name": self.table_name,
            "org_id": self.org_id,
            "item_id": self.item_id,
            "frequency": self.frequency,
            "start_period": self.start_period,
            "end_period": self.end_period,
            "classifications": self.classifications,
            "exact_params": self.exact_params,
        }


class StatBridgeAgent:
    """UI와 MCP 통계 서비스 사이의 오케스트레이션 계층.

    역할:
      1) 자연어 -> v5 통계언어 사전 검색
      2) 애매하면 한 번에 하나의 역질문 반환
      3) 버튼 선택은 confirmed hard constraint로 고정
      4) 재검색 후 실제 사전에 존재하는 API ID만 사용해 API plan 생성
      5) MCP StatisticsService.get_statistics를 호출
    """

    def __init__(self, service: Any, dictionary_path: str | Path | None = None, ncp_client: NcpClovaClient | None = None, jev_client: JevSeriesClient | None = None) -> None:
        self.service = service
        base = Path(__file__).resolve().parent
        self.dictionary_path = Path(dictionary_path or base / "stat_dictionary" / "stat_language_dictionary.json")
        self.resolver = StatLanguageResolver(self.dictionary_path)
        self.tables_by_id = {str(t["table_id"]): t for t in self.resolver.tables}
        self.ncp = ncp_client or NcpClovaClient()
        self.jev = jev_client or JevSeriesClient()
        self.hybrid = HybridStatRetriever(self.resolver)
        # Explicit client injection is used by offline tests/custom deployments;
        # do not silently create a second network client behind that boundary.
        if ncp_client is not None:
            self.hybrid.enabled = False
        self.output_agent = OutputAgent()
        self.workflow = StatBridgeWorkflow(self, self.output_agent)

    @staticmethod
    def _subtract_months(yyyymm: str, months: int) -> str:
        y, m = int(yyyymm[:4]), int(yyyymm[4:6])
        idx = y * 12 + (m - 1) - months
        return f"{idx // 12:04d}{idx % 12 + 1:02d}"

    @staticmethod
    def _subtract_quarters(yearq: str, quarters: int) -> str:
        # StatBridge/KOSIS canonical quarter is YYYY01..YYYY04. Accept YYYYQn too.
        m = re.fullmatch(r"(\d{4})(?:Q)?0?([1-4])", yearq, re.I)
        if not m:
            return yearq
        y, q = int(m.group(1)), int(m.group(2))
        idx = y * 4 + (q - 1) - quarters
        return f"{idx // 4:04d}{idx % 4 + 1:02d}"

    @staticmethod
    def _normalize_observed_period(raw: str, frequency: str, end: bool = False) -> str:
        s = re.sub(r"[^0-9Qq]", "", str(raw or ""))
        if frequency == "M":
            if len(s) >= 6:
                return s[:6]
            if len(s) == 4:
                return s + ("12" if end else "01")
        elif frequency == "Q":
            mq = re.fullmatch(r"(\d{4})[Qq]?0?([1-4])", s)
            if mq:
                return f"{mq.group(1)}{int(mq.group(2)):02d}"
            if len(s) == 6 and s[-2:] in {"01","02","03","04"}:
                return s
            if len(s) == 4:
                return s + ("04" if end else "01")
        elif frequency in {"Y", "A"}:
            return s[:4] if len(s) >= 4 else str(raw)
        return str(raw)

    def _periods(self, query: str, table: dict[str, Any]) -> tuple[str, str]:
        frequency = str(table.get("prd_se") or "Y").upper()
        observed_start = self._normalize_observed_period(str(table.get("period_start_observed") or ""), frequency)
        observed_end = self._normalize_observed_period(str(table.get("period_end_observed") or ""), frequency, end=True)

        # explicit range / 'YYYY년 이후'
        years = [int(x) for x in re.findall(r"((?:19|20)\d{2})\s*년?", query)]
        if years:
            start_y = min(years)
            end_y = max(years)
            if "이후" in query or "부터" in query and len(years) == 1:
                end_y = int(observed_end[:4]) if observed_end[:4].isdigit() else date.today().year
            if frequency == "M":
                return f"{start_y}01", observed_end if end_y == int(observed_end[:4] or end_y) else f"{end_y}12"
            if frequency == "Q":
                return f"{start_y}01", observed_end if end_y == int(observed_end[:4] or end_y) else f"{end_y}04"
            return str(start_y), str(end_y)

        # recent N unit
        m = re.search(r"최근\s*(\d+)\s*(년|개월|분기)", query)
        if m and observed_end:
            n, unit = int(m.group(1)), m.group(2)
            if frequency == "M":
                months = n * 12 if unit == "년" else n if unit == "개월" else n * 3
                return self._subtract_months(observed_end, max(0, months - 1)), observed_end
            if frequency == "Q":
                quarters = n * 4 if unit == "년" else n if unit == "분기" else max(1, (n + 2) // 3)
                return self._subtract_quarters(observed_end, max(0, quarters - 1)), observed_end
            if frequency in {"Y", "A"} and observed_end[:4].isdigit():
                return str(int(observed_end[:4]) - n + 1), observed_end[:4]

        # Safe default: enough observations for a trend without fetching an entire table.
        if observed_end:
            if frequency == "M":
                return self._subtract_months(observed_end, 11), observed_end
            if frequency == "Q":
                return self._subtract_quarters(observed_end, 7), observed_end
            if frequency in {"Y", "A"} and observed_end[:4].isdigit():
                return str(max(int(observed_start[:4] or observed_end[:4]), int(observed_end[:4]) - 4)), observed_end[:4]
        return observed_start or "", observed_end or ""

    @staticmethod
    def _merge_classifications(table: dict[str, Any], selected: dict[str, Any]) -> dict[str, str]:
        # A chart series must map to one value per dimension. Sending every code
        # produces a huge Cartesian response and then collapses different values
        # onto the same dates. Start from each dimension's validated representative
        # (normally total/overall), then replace it with explicit natural-language hits.
        classifications: dict[str, str] = {}
        for dimension in table.get("dimensions") or []:
            param = str(dimension.get("api_param") or "")
            values = dimension.get("values") or []
            first_id = str(values[0].get("value_id") or "") if values else ""
            if re.fullmatch(r"objL[1-8]", param) and first_id:
                classifications[param] = first_id
        # A natural-language dimension hit narrows the corresponding objL to one validated ID.
        for hit in selected.get("dimension_hits") or []:
            param = str(hit.get("api_param") or "")
            value_id = str(hit.get("value_id") or "")
            if re.fullmatch(r"objL[1-8]", param) and value_id:
                classifications[param] = value_id
        return classifications

    def build_api_plan(self, query: str, selected: dict[str, Any]) -> ApiPlan:
        table_id = str(selected["table_id"])
        table = self.tables_by_id[table_id]
        params = dict(table.get("api_call_params") or {})
        item_ids = list(table.get("item_ids") or [])
        item_id = str(params.get("itmId") or (item_ids[0] if item_ids else "ALL"))
        frequency = str(params.get("prdSe") or table.get("prd_se") or "Y")
        start_period, end_period = self._periods(query, table)
        classifications = self._merge_classifications(table, selected)
        if selected.get("_comparison_default"):
            for dimension in table.get("dimensions") or []:
                param = str(dimension.get("api_param") or "")
                values = dimension.get("values") or []
                if re.fullmatch(r"objL[1-8]", param) and values:
                    classifications[param] = str(values[0].get("value_id") or classifications.get(param) or "")
        org_id = str(params.get("orgId") or table.get("org_id") or "301")

        exact = {
            "method": "getList",
            "format": "json",
            "jsonVD": "Y",
            "smblChk": "Y",
            "orgId": org_id,
            "tblId": table_id,
            "itmId": item_id,
            "prdSe": frequency,
            "startPrdDe": start_period,
            "endPrdDe": end_period,
            **classifications,
        }
        return ApiPlan(
            table_id=table_id,
            table_name=str(table.get("table_name") or table_id),
            org_id=org_id,
            item_id=item_id,
            frequency=frequency,
            start_period=start_period,
            end_period=end_period,
            classifications=classifications,
            exact_params=exact,
        )

    @staticmethod
    def _comparison_series(classification: dict[str, Any]) -> list[dict[str, str]]:
        raw = classification.get("series") or []
        if not isinstance(raw, list) or not 2 <= len(raw) <= 5:
            return []
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            query = str(item.get("query") or label).strip()
            if label and query:
                result.append({"label": label, "query": query})
        return result if 2 <= len(result) <= 5 else []

    def _resolve_comparison(self, classification: dict[str, Any], dictionary_query: str) -> dict[str, Any] | None:
        series = self._comparison_series(classification)
        if not series:
            return None
        plans=[]; selected=[]; missing=[]
        used_table_ids=set()
        common_terms = " ".join(str(x) for x in (classification.get("qualifiers") or []) if str(x).strip())
        for item in series:
            ranked = self.hybrid.rank(f"{item['label']} {item['query']} {common_terms}".strip(), top_k=8)
            if not ranked:
                missing.append(item["label"])
                continue
            choice = dict(ranked[0]); choice["_comparison_default"] = True; choice["_series_label"] = item["label"]
            if str(choice["table_id"]) in used_table_ids:
                missing.append(item["label"])
                continue
            used_table_ids.add(str(choice["table_id"]))
            plan = self.build_api_plan(dictionary_query, choice).as_dict()
            plan["series_label"] = item["label"]
            plans.append(plan); selected.append(choice)
        if missing:
            return {
                "status": "no_match", "selected_table": None, "candidates": selected,
                "missing_series": missing,
                "state": {"original_query": dictionary_query, "confirmed": {}, "asked_clarifications": [],
                          "status": "no_match", "_classification": classification},
            }
        return {
            "status": "resolved", "selected_table": selected[0], "selected_tables": selected,
            "api_plan": plans[0], "api_plans": plans, "candidates": selected,
            "state": {"original_query": dictionary_query, "confirmed": {}, "asked_clarifications": [],
                      "status": "resolved", "_classification": classification},
        }

    @staticmethod
    def _classification_terms(classification: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        normalized = str(classification.get("normalized_query") or "").strip()
        if normalized:
            terms.append(normalized)
        for key in ("concepts", "subjects", "measures", "time_terms", "comparison_terms", "qualifiers"):
            value = classification.get(key) or []
            if isinstance(value, list):
                terms.extend(str(x).strip() for x in value if str(x).strip())
        # Stable de-duplication keeps the dictionary query compact.
        return list(dict.fromkeys(terms))

    def _classify(self, query: str) -> tuple[str, dict[str, Any]]:
        if not self.ncp.configured:
            return query, {"status": "disabled", "model": self.ncp.settings.classifier_model}
        try:
            classification = self.ncp.classify_stat_language(query)
            hcx_series_count = len(classification.get("series") or []) if isinstance(classification.get("series"), list) else 0
            trace = {
                "jev_enabled": self.jev.settings.enabled,
                "jev_model": self.jev.settings.model,
                "jev_status": "disabled" if not self.jev.settings.enabled else "not_configured",
                "jev_latency_ms": None,
                "jev_series_count": None,
                "jev_probability": None,
                "hcx_series_count_before": hcx_series_count,
                "final_series_count": hcx_series_count,
                "series_action": "kept",
                "jev_fallback_used": False,
            }
            if self.jev.settings.enabled and self.jev.settings.api_key:
                try:
                    decision = self.jev.classify_series_count(query, classification)
                    classification, repair_trace = apply_jev_series_decision(classification, decision)
                    trace.update(repair_trace)
                    trace.update(jev_status="success", jev_latency_ms=decision.get("latency_ms"))
                except Exception as exc:
                    trace.update(jev_status="error", jev_fallback_used=True, error=str(exc))
            elif self.jev.settings.enabled:
                trace["jev_fallback_used"] = True
            classification["_jev_trace"] = trace
            terms = self._classification_terms(classification)
            expanded = " ".join([query, *terms]).strip()
            return expanded, {"status": "success", **classification}
        except Exception as exc:
            # NCP failure must not make the deterministic dictionary unusable.
            return query, {"status": "error", "model": self.ncp.settings.classifier_model, "error": str(exc)}

    def resolve(self, query: str, state: dict[str, Any] | None = None, clarification: dict[str, str] | None = None) -> dict[str, Any]:
        prior_user_query = ""
        if state and clarification:
            # The resolver state already contains the HCX-003-expanded dictionary query.
            result = self.resolver.apply_clarification(
                state,
                clarification_id=str(clarification["clarification_id"]),
                value=str(clarification["value"]),
                top_k=8,
            )
            classification = dict(state.get("_classification") or {})
            dictionary_query = str(state.get("original_query") or query)
        elif state and state.get("confirmed"):
            # Continue a resolved conversation (period/layout selection) without
            # discarding the user's earlier clarification choices.
            classification = dict(state.get("_classification") or {})
            dictionary_query = str(state.get("original_query") or query)
            result = self.resolver.resolve(
                dictionary_query,
                confirmed=dict(state.get("confirmed") or {}),
                asked_clarifications=list(state.get("asked_clarifications") or []),
                top_k=8,
            )
        else:
            prior_user_query = str((state or {}).get("_user_query") or "")
            effective_query = self.resolver.merge_followup_query(prior_user_query, query) if prior_user_query else query
            # Everyday ambiguous concepts (for example bare "금리") must ask
            # the dictionary's button question before HCX expands them into
            # several speculative comparison series.
            preflight = self.resolver.resolve(effective_query, top_k=8)
            if preflight.get("status") == "need_clarification":
                result = preflight
                result["clarifications"] = self.resolver.collect_clarifications(effective_query, top_k=8)
                dictionary_query, classification = self._classify(effective_query)
                result["state"]["original_query"] = dictionary_query
            elif preflight.get("status") == "no_match" and preflight.get("missing_series"):
                # An explicitly named metric that is absent from the trusted
                # dictionary must not be replaced by HCX/vector similarity with
                # a different statistic (for example 기준금리 -> 대출금리).
                dictionary_query, classification = self._classify(effective_query)
                result = preflight
                result["state"]["original_query"] = dictionary_query
            else:
                selected=preflight.get("selected_table") or {}
                reasons=selected.get("reasons") or []
                deterministic_fast=bool(float(selected.get("score") or 0)>=150 and any(str(reason) in {"table_name","exact_table_phrase"} for reason in reasons))
                if deterministic_fast:
                    dictionary_query=effective_query
                    classification={"status":"deterministic_fast_path","normalized_query":effective_query,"series":[]}
                    if self.jev.settings.enabled:
                        classification["_jev_trace"] = {
                            "jev_enabled": True, "jev_model": self.jev.settings.model,
                            "jev_status": "skipped_deterministic_fast_path", "jev_latency_ms": 0,
                            "jev_series_count": None, "jev_probability": None,
                            "hcx_series_count_before": 0, "final_series_count": 0,
                            "series_action": "kept", "jev_fallback_used": False,
                        }
                else:
                    dictionary_query, classification = self._classify(effective_query)
                comparison = self._resolve_comparison(classification, dictionary_query)
                if comparison:
                    result = comparison
                else:
                    hybrid_candidates = self.hybrid.rank(dictionary_query, top_k=12)
                    # Keep deterministic ambiguity gates. Replace its ranking only after
                    # the resolver has decided whether a clarification is required.
                    result = self.resolver.resolve(dictionary_query, top_k=8)
                    if result.get("status") == "resolved" and hybrid_candidates:
                        result["candidates"] = hybrid_candidates[:8]
                        result["selected_table"] = hybrid_candidates[0]
                        result["retrieval_confident"] = self.hybrid.confident(hybrid_candidates)

        confirmed_choices = dict((result.get("state") or {}).get("confirmed") or (state or {}).get("confirmed") or {})
        # Explicit UI choices are hard constraints. HCX may suggest speculative
        # comparison series for the original broad question, but those must not
        # replace the table selected by the user's clarification buttons.
        if not confirmed_choices and result.get("status") == "resolved" and not result.get("api_plans"):
            comparison = self._resolve_comparison(classification, dictionary_query)
            if comparison:
                result = comparison

        if prior_user_query and result.get("status") != "need_clarification":
            followup_series=self.resolver.rank_followup(prior_user_query,query,top_k=12)
            if followup_series:
                selected=[]; plans=[]
                for item in followup_series[:5]:
                    choice=dict(item); choice["_comparison_default"]=True
                    selected.append(choice); plans.append(self.build_api_plan(dictionary_query,choice).as_dict())
                result={
                    "status":"resolved","selected_table":selected[0],"selected_tables":selected,
                    "api_plan":plans[0],"api_plans":plans,"candidates":selected,
                    "state":{"original_query":dictionary_query,"confirmed":{},"asked_clarifications":[],"status":"resolved"},
                }

        # Deterministic comparison fallback: when HCX is unavailable or does not emit
        # series, split only explicitly expressed concepts and select IDs from the
        # dictionary. This never manufactures table/item/dimension identifiers.
        if not confirmed_choices and result.get("status") == "resolved" and not result.get("api_plans"):
            series = self.resolver.rank_many(dictionary_query, top_k=12)
            if len(series) >= 2:
                selected=[]; plans=[]
                for item in series[:5]:
                    choice=dict(item); choice["_comparison_default"]=True
                    selected.append(choice); plans.append(self.build_api_plan(dictionary_query, choice).as_dict())
                result["selected_table"]=selected[0]
                result["selected_tables"]=selected
                result["api_plan"]=plans[0]
                result["api_plans"]=plans

        result["classification"] = classification
        result["dictionary_query"] = dictionary_query
        if isinstance(result.get("state"), dict):
            result["state"]["_user_query"] = query
            result["state"]["_classification"] = classification

        if result.get("status") == "need_clarification":
            # Options are deterministic dictionary values; HCX-007 only phrases the question.
            result["question"] = self.ncp.clarify_question(query, result, result.get("state") or {})
            return result

        if result.get("status") != "resolved" or not result.get("selected_table"):
            return result

        if result.get("api_plans"):
            return result
        selected = result["selected_table"]
        # Use the expanded statistical language for period hints while retaining the original UI question.
        plan = self.build_api_plan(dictionary_query, selected)
        result["api_plan"] = plan.as_dict()
        return result

    def run(
        self,
        query: str,
        state: dict[str, Any] | None = None,
        clarification: dict[str, str] | None = None,
        execute: bool = True,
        start_period: str | None = None,
        end_period: str | None = None,
        period_overrides: dict[str, tuple[str, str]] | None = None,
        generate_answer: bool = True,
        output_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.workflow.invoke(
            query=query,
            conversation_state=state,
            clarification=clarification,
            execute=execute,
            start_period=start_period,
            end_period=end_period,
            period_overrides=period_overrides,
            generate_answer=generate_answer,
            output_request=output_request,
        )

    def run_resolution(
        self,
        query: str,
        resolution: dict[str, Any],
        execute: bool = True,
        start_period: str | None = None,
        end_period: str | None = None,
        period_overrides: dict[str, tuple[str, str]] | None = None,
        generate_answer: bool = True,
        output_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume orchestration from a previously resolved, user-confirmed plan."""
        return self.workflow.invoke(
            query=query,
            resolution=resolution,
            execute=execute,
            start_period=start_period,
            end_period=end_period,
            period_overrides=period_overrides,
            generate_answer=generate_answer,
            output_request=output_request,
        )

    def execute_resolution(
        self,
        query: str,
        resolution: dict[str, Any],
        execute: bool = True,
        start_period: str | None = None,
        end_period: str | None = None,
        period_overrides: dict[str, tuple[str, str]] | None = None,
        generate_answer: bool = True,
    ) -> dict[str, Any]:
        """Execute an already resolved plan without repeating HCX/vector retrieval."""

        plans = resolution.get("api_plans") or [resolution["api_plan"]]
        for plan in plans:
            override = (period_overrides or {}).get(str(plan["table_id"]))
            plan_start = override[0] if override else start_period
            plan_end = override[1] if override else end_period
            if plan_start:
                plan["start_period"] = plan_start
                plan["exact_params"]["startPrdDe"] = plan_start
            if plan_end:
                plan["end_period"] = plan_end
                plan["exact_params"]["endPrdDe"] = plan_end
        if not execute:
            return {**resolution, "execution": {"status": "planned_only", "rows": [], "row_count": 0}, "answer": ""}

        try:
            all_rows=[]; sources=[]
            prefer_local = not bool(os.getenv("KOSIS_API_KEY", "").strip())
            for plan in plans:
                data = self.service.get_statistics(
                    table_id=plan["table_id"], item_id=plan["item_id"],
                    classifications=plan["classifications"], frequency=plan["frequency"],
                    start_period=plan["start_period"], end_period=plan["end_period"], prefer_local=prefer_local,
                    allow_fallback=False,
                )
                if data.get("status") != "success":
                    errors = data.get("errors") or []
                    detail = "; ".join(str(item.get("error") or item) for item in errors if item)
                    raise RuntimeError(detail or f"{plan['table_name']} 수치 데이터를 가져오지 못했습니다.")
                label = str(plan.get("series_label") or plan["table_name"])
                for row in data.get("rows") or []:
                    enriched=dict(row); enriched["_SERIES_LABEL"] = label; all_rows.append(enriched)
                sources.append({"table_id": plan["table_id"], "source": data.get("source"), "row_count": len(data.get("rows") or [])})
            execution = {"status": "success", "rows": all_rows, "row_count": len(all_rows), "sources": sources}
            answer = ""
            if generate_answer and os.getenv("STATBRIDGE_GENERATE_NARRATIVE", "1").lower() in {"1", "true", "on"}:
                try:
                    answer = self.ncp.answer_with_data(query, plans[0], execution) if self.ncp.configured else ""
                except Exception as answer_exc:
                    execution["ncp_answer_error"] = str(answer_exc)
            return {**resolution, "execution": execution, "answer": answer}
        except Exception as exc:
            # Preserve the exact plan so the UI/debugger can verify what would be sent to KOSIS.
            return {
                **resolution,
                "answer": "",
                "execution": {
                    "status": "error",
                    "error": str(exc),
                    "rows": [],
                    "row_count": 0,
                },
            }

    def render_output(self, result: dict[str, Any], output_request: dict[str, Any]) -> dict[str, Any]:
        """Resume the same LangGraph at the output boundary without re-running MCP."""
        return self.workflow.invoke_output(result=result, output_request=output_request)
