from __future__ import annotations

import calendar
import copy
import json
import os
import re
import hashlib
from threading import RLock
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
from request_match_guard import COMBINED_RATE_PATTERN, structure_request, table_evidence, catalog_mentions, scoped_metric_query, unsupported_quantity_terms
from canonical_request_planner import instruction_text
from catalog_request_planner import selections as catalog_selections, unavailable as unavailable_catalog_quantity
from request_period import explicit_period


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
        if dictionary_path is None:
            from statbridge_mcp.metadata_store import MetadataStore
            from catalog_classifications import refresh_classifications
            store=getattr(getattr(service,'service',service),'store',None)
            if isinstance(store,MetadataStore):
                refresh_classifications(self.resolver.tables,store)
        self.tables_by_id = {str(t["table_id"]): t for t in self.resolver.tables}
        self._catalog_selection_cache = {}
        self._catalog_selection_lock = RLock()
        self.ncp = ncp_client or NcpClovaClient()
        self.jev = jev_client or JevSeriesClient()
        self.hybrid = HybridStatRetriever(self.resolver)
        # Explicit client injection is used by offline tests/custom deployments;
        # do not silently create a second network client behind that boundary.
        if ncp_client is not None:
            self.hybrid.enabled = False
        self.output_agent = OutputAgent(self.ncp)
        self.workflow = StatBridgeWorkflow(self, self.output_agent)

    def _catalog_candidates(self, query):
        # Reuse only metadata-owned selections, never observations or user state.
        # Bind to the full current catalog so in-process metadata edits invalidate.
        stamp=hashlib.sha256(json.dumps(self.resolver.tables,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        key=(query,stamp)
        with self._catalog_selection_lock:
            if key in self._catalog_selection_cache:
                return copy.deepcopy(self._catalog_selection_cache[key])
        selected=catalog_selections(query,self.resolver.tables)
        with self._catalog_selection_lock:
            self._catalog_selection_cache[key]=copy.deepcopy(selected)
            while len(self._catalog_selection_cache)>64:
                self._catalog_selection_cache.pop(next(iter(self._catalog_selection_cache)))
        return selected

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

        query = instruction_text(query, self.tables_by_id.values())
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
            representative = str(dimension.get("representative_value_id") or "")
            if representative and representative in {str(v.get("value_id")) for v in values}:
                first_id = representative
            if re.fullmatch(r"objL[1-8]", param) and first_id:
                classifications[param] = first_id
        # A natural-language dimension hit narrows the corresponding objL to one validated ID.
        for hit in selected.get("dimension_hits") or []:
            param = str(hit.get("api_param") or "")
            value_id = str(hit.get("value_id") or "")
            if re.fullmatch(r"objL[1-8]", param) and value_id:
                classifications[param] = value_id
        return classifications

    def _validated_followup_plans(self, state: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]] | None:
        """Recheck client-carried prior plans against their original request and catalog."""
        if not isinstance(state, dict):
            return None
        prior_query = str(state.get("_validated_request_query") or "").strip()
        plans = state.get("_validated_api_plans")
        if not prior_query or not isinstance(plans, list) or not plans:
            return None
        plans = copy.deepcopy(plans)
        candidates = [self.resolver.candidate_shell(str(plan.get("table_id") or ""))
                      for plan in plans if str(plan.get("table_id") or "") in self.tables_by_id]
        if len(candidates) != len(plans):
            return None
        for candidate, plan in zip(candidates, plans):
            table = self.tables_by_id[candidate["table_id"]]
            values = {str(value.get("value_id")): value
                      for dimension in table.get("dimensions") or []
                      for value in dimension.get("values") or []}
            hits = []
            for param, value_id in (plan.get("classifications") or {}).items():
                value = values.get(str(value_id))
                if not value:
                    return None
                hits.append({"api_param": str(param), "value_id": str(value_id),
                             "value_name": str(value.get("value_name") or "")})
            candidate["dimension_hits"] = hits
        prior_state = {"confirmed": copy.deepcopy(state.get("confirmed") or {})}
        prior_result = {"status": "resolved", "selected_table": candidates[0],
                        "selected_tables": candidates, "api_plan": plans[0],
                        "api_plans": plans, "state": prior_state}
        checked = self.validate_resolution(prior_query, prior_result)
        if checked.get("status") != "resolved" or not (checked.get("request_validation") or {}).get("valid"):
            return None
        return prior_query, plans

    def _followup_anchor_query(self, plan: dict[str, Any]) -> str:
        """Turn a revalidated plan into a catalog-only anchor for follow-up parsing."""
        table = self.tables_by_id[str(plan["table_id"])]
        parts = [str(table.get("table_name") or "")]
        item_id = str(plan.get("item_id") or "")
        item_names = table.get("item_names") or []
        item_ids = [str(value) for value in table.get("item_ids") or []]
        if item_id in item_ids and item_ids.index(item_id) < len(item_names):
            parts.append(str(item_names[item_ids.index(item_id)]))
        for dimension in table.get("dimensions") or []:
            value_id = str((plan.get("classifications") or {}).get(dimension.get("api_param")) or "")
            value = next((value for value in dimension.get("values") or []
                          if str(value.get("value_id")) == value_id), None)
            if value:
                parts.append(str(value.get("value_name") or ""))
        parts.append(str(plan.get("frequency") or ""))
        return " ".join(part for part in parts if part)

    @staticmethod
    def _explicit_dimension_value(query: str, dimension: dict[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any] | None:
        from semantic_catalog_planner import matches, norm, value_terms

        excluded = exclude or set()
        candidates = [value for value in matches(query, dimension)
                      if str(value.get("value_id")) not in excluded]
        if not candidates:
            return None
        normalized_query = norm(query)
        def score(value):
            explicit = max((len(norm(term)) for term in value_terms(value.get("value_name") or "")
                            if norm(term) and norm(term) in normalized_query), default=0)
            return explicit, len(norm(value.get("value_name") or ""))
        candidates.sort(key=score, reverse=True)
        best = score(candidates[0])
        tied = [value for value in candidates if score(value) == best]
        return candidates[0] if len(tied) == 1 else None

    def _compute_followup_plans(self, query: str, prior_query: str,
                                prior_plans: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Apply explicit catalog mutations to an already validated plan."""
        additive = bool(re.search(r"(추가|같이|함께|비교해\s*줘|함께\s*보여)", query))
        replacement = bool(re.search(r"(아니|바꿔|바꾸|변경)", query))
        if not additive and not replacement:
            return None

        base_ids = {str(plan.get("table_id") or "") for plan in prior_plans}
        selected = self.resolver.rank_followup(self._followup_anchor_query(prior_plans[0]), query, top_k=12)
        selected_ids = list(dict.fromkeys(str(item.get("table_id") or "") for item in selected
                                          if str(item.get("table_id") or "") in self.tables_by_id))

        def create_plan(table_id: str, previous: dict[str, Any] | None) -> dict[str, Any] | None:
            table = self.tables_by_id.get(table_id)
            if not table:
                return None
            candidate = self.resolver.candidate_shell(table_id)
            dimension_query = instruction_text(query, [table])
            hits = []
            changed = False
            for dimension in table.get("dimensions") or []:
                param = str(dimension.get("api_param") or "")
                old_code = str((previous or {}).get("classifications", {}).get(param) or "")
                target = self._explicit_dimension_value(dimension_query, dimension,
                                                        exclude={old_code} if additive and table_id in base_ids else None)
                if target:
                    value_id = str(target.get("value_id") or "")
                    changed = changed or value_id != old_code
                elif table_id in base_ids and previous:
                    value_id = old_code
                else:
                    values = dimension.get("values") or []
                    representative = str(dimension.get("representative_value_id") or "")
                    value_id = representative if any(str(value.get("value_id")) == representative for value in values) else str(values[0].get("value_id") or "") if values else ""
                value = next((value for value in dimension.get("values") or []
                              if str(value.get("value_id")) == value_id), None)
                if value:
                    hits.append({"api_param": param, "value_id": value_id,
                                 "value_name": str(value.get("value_name") or "")})
            if table_id in base_ids and not changed:
                return None
            candidate["dimension_hits"] = hits
            return self.build_api_plan(prior_query, candidate).as_dict()

        if additive:
            result = copy.deepcopy(prior_plans)
            for prior in prior_plans:
                table_id = str(prior.get("table_id") or "")
                changed = create_plan(table_id, prior)
                if changed and changed not in result:
                    result.append(changed)
            for table_id in selected_ids:
                if table_id in base_ids:
                    continue
                added = create_plan(table_id, None)
                if added and added not in result:
                    result.append(added)
            return result[:5] if len(result) > len(prior_plans) else None

        target_ids = [table_id for table_id in selected_ids if table_id not in base_ids]
        target_id = target_ids[-1] if target_ids else (selected_ids[-1] if selected_ids else "")
        previous = next((plan for plan in prior_plans if str(plan.get("table_id")) == target_id), None)
        if not target_id:
            return None
        replacement_plan = create_plan(target_id, previous)
        if replacement_plan is None and target_id in base_ids:
            # Table-name corrections can retain the same table while changing an
            # item/dimension that has a clear catalog value in the user's turn.
            candidate = self.resolver.candidate_shell(target_id)
            dimension_query = instruction_text(query, [self.tables_by_id[target_id]])
            for dimension in self.tables_by_id[target_id].get("dimensions") or []:
                target = self._explicit_dimension_value(dimension_query, dimension)
                if target:
                    candidate["dimension_hits"].append({"api_param": dimension["api_param"],
                        "value_id": str(target["value_id"]), "value_name": str(target.get("value_name") or "")})
            replacement_plan = self.build_api_plan(prior_query, candidate).as_dict()
            if all(replacement_plan.get("classifications", {}).get(k) == v
                   for k, v in (previous or {}).get("classifications", {}).items()):
                return None
        return [replacement_plan] if replacement_plan else None

    @staticmethod
    def _period_only_followup(query: str) -> bool:
        request = structure_request(query)
        period_intent = bool(re.search(r"(조회\s*기간|기간(?:을|으로|만|은)|조회\s*범위)", query))
        has_boundary = bool(re.search(r"(?:부터|이후|까지|~|～|–|—)", query))
        return (not request["metrics"] and not request["explicit_ids"] and period_intent
                and has_boundary and explicit_period(query) is not None)

    def build_api_plan(self, query: str, selected: dict[str, Any]) -> ApiPlan:
        table_id = str(selected["table_id"])
        table = self.tables_by_id[table_id]
        params = dict(table.get("api_call_params") or {})
        item_ids = list(table.get("item_ids") or [])
        item_id = str(params.get("itmId") or (item_ids[0] if item_ids else "ALL"))
        frequency = str(selected.get("_frequency") or params.get("prdSe") or table.get("prd_se") or "Y")
        start_period, end_period = self._periods(query, {**table, "prd_se": frequency})
        classifications = self._merge_classifications(table, selected)
        if selected.get("_comparison_default"):
            classifications = self._merge_classifications(table, {})
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

    def _current_account_component_plans(self, query: str, selected: dict[str, Any], dictionary_query: str) -> list[dict[str, Any]]:
        """Expand explicitly requested components using this table's verified IDs."""
        if (str(selected.get("table_id")) != "DT_301Y017" or "경상수지" not in query or
                not re.search(r"구성\s*(?:항목|요소)|세부\s*항목", query)):
            return []
        table = self.tables_by_id["DT_301Y017"]
        dimension = next((item for item in table.get("dimensions") or [] if item.get("api_param") == "objL1"), None)
        if not dimension:
            return []
        labels = ("경상수지", "상품수지", "서비스수지", "본원소득수지", "이전소득수지")
        values = {str(value.get("value_name")): str(value.get("value_id")) for value in dimension.get("values") or []}
        if any(not values.get(label) for label in labels):
            return []
        other_hits = [hit for hit in selected.get("dimension_hits") or [] if hit.get("api_param") != "objL1"]
        plans = []
        for label in labels:
            choice = {**selected, "dimension_hits": [*other_hits, {"api_param": "objL1", "value_id": values[label]}]}
            plan = self.build_api_plan(dictionary_query, choice).as_dict()
            plan["series_label"] = label
            plans.append(plan)
        return plans

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
            ranked = [candidate for candidate in ranked if table_evidence(item['query'], self.tables_by_id.get(str(candidate.get('table_id')), {}))[0]
                      and self.resolver._matches_metric_intent(item['query'], self.tables_by_id.get(str(candidate.get('table_id')), {}))]
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

    def _scoped_comparison(self, query, state, clarification):
        # Uncertainty describes alternatives, rather than independent requested series.
        if any(word in query for word in ['모르겠','어느 쪽','어느쪽']):return None
        if '보유' in query and '예금취급기관' in query:return None
        # A choice for one compared metric must not filter the other metric.
        text=re.sub(r'(20\d{2})년\s*(?:과|와|부터)\s*(20\d{2})년까지(?:의)?', ' ', query)
        from canonical_request_planner import references
        spans=list({(start,end):(start,end,table) for start,end,table in references(text,self.resolver.tables)}.values())
        protected={}
        for i,(start,end,table) in enumerate(sorted(spans,key=lambda s:s[0],reverse=True)):
            token=f'@CATALOG{i}@';protected[token]=text[start:end];text=text[:start]+token+text[end:]
        parts=[p.strip() for p in re.split(r'그리고|\s+및\s+|와\s+|과\s+',text) if p.strip()]
        for token,title in protected.items():parts=[p.replace(token,title) for p in parts]
        # Copy the whole clause so both rates inherit institution, basis and dates.
        parts=[COMBINED_RATE_PATTERN.sub(rate, part)
               for part in parts
               for rate in (('대출금리', '수신금리') if COMBINED_RATE_PATTERN.search(part) else ('',))]
        if not 2<=len(parts)<=5 or not all(structure_request(p)['metrics'] or catalog_mentions(p,self.resolver.tables) for p in parts):
            return None
        years=re.findall(r'20\d{2}',query)
        period=f' {years[0]}년부터 {years[-1]}년까지' if len(years)>=2 else ''
        confirmed=dict((state or {}).get('confirmed') or {})
        if clarification:confirmed[str(clarification['clarification_id'])]=str(clarification['value'])
        plans=[]; selected=[]
        store=getattr(getattr(self.service,'service',self.service),'store',None)
        supported={str(t.table_id) for t in store.available_supported_tables()} if store else set(self.tables_by_id)
        def viable(candidate):
            table=self.tables_by_id.get(candidate['table_id'],{})
            if candidate['table_id'] not in supported:return False
            bounds=[table,*[self.tables_by_id[p['table_id']] for p in plans]]
            def boundary(t,end=False):
                raw=str(t.get('period_end_observed' if end else 'period_start_observed') or '')
                digits=re.sub(r'\D','',raw)
                if len(digits)<4:return ''
                year=int(digits[:4]);frequency=str(t.get('prd_se') or '')
                part=int(digits[4:6] or '1') if len(digits)>=6 else 1
                month=(part*3 if end else (part-1)*3+1) if frequency=='Q' else (part*6 if end else (part-1)*6+1) if frequency in {'H','S'} else part if frequency=='M' else 12 if end else 1
                day=calendar.monthrange(year,month)[1] if end else 1
                return date(year,month,day).isoformat()
            starts=[boundary(t) for t in bounds];ends=[boundary(t,True) for t in bounds]
            from request_period import explicit_period
            requested=explicit_period(query,latest=min((e for e in ends if e),default=None))
            if requested:starts.append(requested['start']);ends.append(requested['end'])
            return max((s for s in starts if s),default='')<=min((e for e in ends if e),default='9999')
        for index,part in enumerate(parts):
            part=re.sub(r'신규\s*대출','신규취급액',part)
            prefix=f'comparison:{index}:'
            local={k[len(prefix):]:v for k,v in confirmed.items() if k.startswith(prefix)}
            scoped_catalog=self._catalog_candidates(part+period) if not local else []
            if scoped_catalog and all(not c.get('_missing_dimensions') and viable(c) for c in scoped_catalog):
                selected.extend(scoped_catalog)
                plans.extend(self.build_api_plan(part+period,c).as_dict() for c in scoped_catalog)
                continue
            result=self.resolver.resolve(part+period,confirmed=local,top_k=8)
            next_state={'original_query':query,'_request_query':query,'_user_query':query,'confirmed':confirmed}
            if result['status']=='need_clarification':
                options=[]
                for option in result.get('options',[]):
                    probe={**local,result['clarification_id']:option['value']}
                    matches=self.resolver.rank(part+period,confirmed=probe,top_k=40)
                    if any(viable(c) and (not str(option['value']).startswith('DT_') or c['table_id']==option['value']) for c in matches):
                        options.append(option)
                if not options:return {'status':'no_match','missing_series':[part],'state':next_state}
                return {**result,'clarification_id':prefix+result['clarification_id'],
                        'options':options,'question':f'「{part}」: '+result['question'],'state':next_state}
            if result['status']!='resolved':return {**result,'state':next_state}
            candidates=[c for c in (result.get('candidates') or []) if viable(c)]
            if '대출' in part and '금리' not in part:
                candidates=[c for c in candidates if '금리' not in c['table_name']]
                if '신규취급액' in part:
                    candidates=[c for c in candidates if '신규취급액' in c['table_name']]
                if not any(word in part for word in ('비중','비율','구성비')):
                    candidates=[c for c in candidates if '비중' not in c['table_name']]
            for product in ('주택담보','신용대출'):
                if product in part:
                    candidates=[c for c in candidates if product in c['table_name'] or any(product in str(h.get('value_name') or '') for h in c.get('dimension_hits',[]))]
            if not candidates:return {'status':'no_match','missing_series':[part],'state':next_state}
            chosen=next((c for c in candidates if c['table_id']==local.get('table')),None)
            if local.get('table') and not chosen:
                return {'status':'no_match','missing_series':[part],'state':next_state}
            if not chosen and len(candidates)>1:
                return {'status':'need_clarification','clarification_id':prefix+'table',
                        'question':f'「{part}」에 사용할 통계표를 선택해 주세요. '+('은행 구분과 금리 기준을 확인해 주세요.' if '금리' in part else '차주당 금액과 전체 금액은 다릅니다.' if '대출' in part else '통계표의 기준과 항목을 확인해 주세요.'),
                        'options':[{'label':c['table_name']+' ['+c['table_id']+']','value':c['table_id'],'confirmed_terms':[]} for c in candidates[:5]],
                        'state':next_state}
            chosen=chosen or candidates[0]
            selected.append(chosen);plans.append(self.build_api_plan(part+period,chosen).as_dict())
        return self.validate_resolution(query,{'status':'resolved','selected_table':selected[0],
            'selected_tables':selected,'api_plan':plans[0],'api_plans':plans,'candidates':selected,
            'state':{'original_query':query,'_request_query':query,'_user_query':query,'confirmed':confirmed},
            'dictionary_query':query,'request_query':query,'classification':{'status':'scoped_comparison'}})

    def resolve(self, query: str, state: dict[str, Any] | None = None, clarification: dict[str, str] | None = None) -> dict[str, Any]:
        prior_context = self._validated_followup_plans(state)
        # The period picker resubmits the same question and its validated state.
        # Re-expanding that question with a table-name anchor can duplicate a
        # canonical reference and invalidate the already checked plan.
        same_request = bool(prior_context and query.strip() == prior_context[0].strip())
        if prior_context and not clarification and (same_request or self._period_only_followup(query)):
            prior_query, plans = prior_context
            candidates = [self.resolver.candidate_shell(str(plan["table_id"])) for plan in plans]
            for candidate, plan in zip(candidates, plans):
                table = self.tables_by_id[candidate["table_id"]]
                values = {str(value.get("value_id")): value
                          for dimension in table.get("dimensions") or []
                          for value in dimension.get("values") or []}
                candidate["dimension_hits"] = [
                    {"api_param": str(param), "value_id": str(value_id),
                     "value_name": str(values[str(value_id)].get("value_name") or "")}
                    for param, value_id in (plan.get("classifications") or {}).items()
                ]
            next_state = {**state, "original_query": state.get("original_query") or prior_query,
                          "_request_query": prior_query, "_user_query": query,
                          "_validated_request_query": prior_query,
                          "_validated_api_plans": copy.deepcopy(plans), "status": "resolved"}
            return self.validate_resolution(prior_query, {
                "status": "resolved", "selected_table": candidates[0],
                "selected_tables": candidates, "api_plan": plans[0],
                "api_plans": plans, "candidates": candidates,
                "dictionary_query": state.get("original_query") or prior_query,
                "classification": state.get("_classification") or {},
                "state": next_state,
            })
        if prior_context and not clarification:
            prior_query, prior_plans = prior_context
            updated_plans = self._compute_followup_plans(query, prior_query, prior_plans)
            if updated_plans:
                candidates = []
                for plan in updated_plans:
                    candidate = self.resolver.candidate_shell(str(plan["table_id"]))
                    table = self.tables_by_id[candidate["table_id"]]
                    values = {str(value.get("value_id")): value
                              for dimension in table.get("dimensions") or []
                              for value in dimension.get("values") or []}
                    candidate["dimension_hits"] = [
                        {"api_param": str(param), "value_id": str(value_id),
                         "value_name": str(values[str(value_id)].get("value_name") or "")}
                        for param, value_id in (plan.get("classifications") or {}).items()
                    ]
                    candidates.append(candidate)
                next_state = {**state, "original_query": state.get("original_query") or prior_query,
                              "_request_query": prior_query, "_user_query": query,
                              "_followup_base_request_query": prior_query,
                              "_followup_base_api_plans": copy.deepcopy(prior_plans),
                              "_followup_query": query, "status": "resolved"}
                return self.validate_resolution(query, {
                    "status": "resolved", "selected_table": candidates[0],
                    "selected_tables": candidates, "api_plan": updated_plans[0],
                    "api_plans": updated_plans, "candidates": candidates,
                    "dictionary_query": self.resolver.merge_followup_query(
                        self._followup_anchor_query(prior_plans[0]), query),
                    "classification": state.get("_classification") or {},
                    "state": next_state,
                })
        repeat_grounded_request = bool(
            state and state.get('_catalog_grounded')
            and state.get('_user_query') == query and not state.get('confirmed')
        )
        if not clarification and (not state or repeat_grounded_request):
            unknown = unsupported_quantity_terms(query, self.resolver.tables)
            if unknown:
                return {'status': 'no_match', 'missing_series': unknown,
                        'state': {'original_query': query}}
            missing = unavailable_catalog_quantity(query, self.resolver.tables)
            if missing:
                return {'status': 'no_match', 'missing_series': [missing],
                        'state': {'original_query': query}}
            if COMBINED_RATE_PATTERN.search(query) or re.search(r'(?:^|까지(?:의)?\s+|(?:최근|현재)\s+)금리(?:와|과)\s',query):
                scoped=self._scoped_comparison(query,state,clarification)
                if scoped is not None:return scoped
            from query_semantics import active_request
            semantic_query=active_request(query)
            if '금리' in semantic_query and not any(word in semantic_query for word in ['신규','잔액','비중','구성비']):
                preflight=self.resolver.resolve(query,top_k=8)
                if preflight.get('status')=='need_clarification':
                    scoped=self._scoped_comparison(query,state,clarification)
                    if scoped is not None:return scoped
                    return preflight
            selected = self._catalog_candidates(query)
            if selected:
                plans = [self.build_api_plan(query, item).as_dict() for item in selected]
                return self.validate_resolution(query, {
                    'status': 'resolved', 'selected_table': selected[0],
                    'selected_tables': selected, 'api_plan': plans[0], 'api_plans': plans,
                    'candidates': selected, 'dictionary_query': query,
                    'classification': {'status': 'catalog_measured_concept', 'normalized_query': query},
                    'state': {**(state or {}), 'original_query': query, '_request_query': query,
                              '_user_query': query, '_catalog_grounded': True, 'confirmed': {}},
                })
        scoped=self._scoped_comparison(query,state,clarification)
        if scoped is not None:return scoped
        request_query = query
        if state and not structure_request(query)["metrics"] and not structure_request(query)["explicit_ids"]:
            request_query = str(state.get("_request_query") or state.get("_user_query") or query)
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
            prior_user_query = (self._followup_anchor_query(prior_context[1][0])
                                if prior_context else str((state or {}).get("_user_query") or ""))
            effective_query = self.resolver.merge_followup_query(prior_user_query, query) if prior_user_query else query
            if prior_context:
                request_query = effective_query
            # Everyday ambiguous concepts (for example bare "금리") must ask
            # the dictionary's button question before HCX expands them into
            # several speculative comparison series.
            preflight = self.resolver.resolve(effective_query, top_k=8)
            if preflight.get("canonical_name_match"):
                result=preflight
                dictionary_query=effective_query
                classification={"status":"canonical_name_match","normalized_query":effective_query,"series":[]}
            elif preflight.get("status") == "need_clarification":
                result = preflight
                if str(preflight.get('clarification_id') or '').startswith('canonical_table:'):
                    dictionary_query=effective_query
                    classification={"status":"canonical_name_ambiguity","normalized_query":effective_query,"series":[]}
                    result['clarifications']=[dict(preflight)]
                else:
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
                deterministic_fast=bool(float(selected.get("score") or 0)>=150 and any(str(reason) in {"table_id","table_name","exact_table_phrase"} for reason in reasons))
                deterministic_fast = deterministic_fast or bool(preflight.get("status")=="resolved" and len(preflight.get("candidates") or [])==1 and structure_request(effective_query)["metrics"] and table_evidence(effective_query,self.tables_by_id.get(str(selected.get("table_id")),{}))[0])
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
                comparison = self._resolve_comparison(classification, dictionary_query) if structure_request(effective_query)['comparison'] else None
                if comparison:
                    result = comparison
                else:
                    hybrid_candidates = self.hybrid.rank(dictionary_query, top_k=12)
                    mentions = catalog_mentions(effective_query, self.resolver.tables)
                    eligible = []
                    for candidate in hybrid_candidates:
                        table = self.tables_by_id.get(str(candidate.get('table_id')), {})
                        if not table:
                            continue
                        scoped = scoped_metric_query(effective_query, table, mentions)
                        if table_evidence(scoped, table)[0] and self.resolver._matches_metric_intent(scoped, table):
                            eligible.append(candidate)
                    hybrid_candidates = eligible
                    # Keep deterministic ambiguity gates. Replace its ranking only after
                    # the resolver has decided whether a clarification is required.
                    # A model's search expansion must not rewrite an already
                    # grounded quantity or suppress its clarification gate.
                    result = preflight if preflight.get("status") == "resolved" else self.resolver.resolve(dictionary_query, top_k=8)
                    if preflight.get("status") == "resolved":
                        # Search hints are not user-selected dates, dimensions
                        # or measures when constructing the actual API plan.
                        dictionary_query = effective_query
                    if result.get("status") == "resolved" and hybrid_candidates:
                        result["candidates"] = hybrid_candidates[:8]
                        result["selected_table"] = hybrid_candidates[0]
                        result["retrieval_confident"] = self.hybrid.confident(hybrid_candidates)

        confirmed_choices = dict((result.get("state") or {}).get("confirmed") or (state or {}).get("confirmed") or {})
        # Explicit UI choices are hard constraints. HCX may suggest speculative
        # comparison series for the original broad question, but those must not
        # replace the table selected by the user's clarification buttons.
        if not confirmed_choices and result.get("status") == "resolved" and not result.get("api_plans") and structure_request(request_query)['comparison']:
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

        if result.get('canonical_name_match') and not result.get('api_plans') and len(result.get('selected_tables') or [])>1:
            choices=result['selected_tables']
            plans=[self.build_api_plan(dictionary_query,choice).as_dict() for choice in choices]
            result['api_plan']=plans[0]
            result['api_plans']=plans

        result["classification"] = classification
        result["dictionary_query"] = dictionary_query
        result["request_query"] = request_query
        if isinstance(result.get("state"), dict):
            result["state"]["_user_query"] = query
            result["state"]["_classification"] = classification
            result["state"]["_request_query"] = request_query

        if result.get("status") == "need_clarification":
            # Options are deterministic dictionary values; HCX-007 only phrases the question.
            result["question"] = self.ncp.clarify_question(query, result, result.get("state") or {})
            return result

        if result.get("status") != "resolved" or not result.get("selected_table"):
            return result

        if result.get("api_plans"):
            return self.validate_resolution(request_query, result)
        selected = result["selected_table"]
        # Use the expanded statistical language for period hints while retaining the original UI question.
        plan = self.build_api_plan(dictionary_query, selected)
        result["api_plan"] = plan.as_dict()
        component_plans = self._current_account_component_plans(query, selected, dictionary_query)
        if component_plans:
            result["api_plan"] = component_plans[0]
            result["api_plans"] = component_plans
        return self.validate_resolution(request_query, result)

    def validate_resolution(self, query: str, resolution: dict[str, Any], *,
                            confirmed_periods: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
        """Check original intent and canonical IDs again after model/vector ranking."""
        grounded = self._catalog_candidates(query)
        grounded_plans = [self.build_api_plan(query, item).as_dict() for item in grounded]
        # Only a server-owned output session supplies these boundaries. All
        # table, item, classification and non-period parameters stay canonical.
        for plan in grounded_plans:
            period = (confirmed_periods or {}).get(str(plan["table_id"]))
            if period:
                plan.update(start_period=period[0], end_period=period[1])
                plan["exact_params"].update(startPrdDe=period[0], endPrdDe=period[1])
        observed = resolution.get('api_plans') or ([resolution['api_plan']] if resolution.get('api_plan') else [])
        confirmed = (resolution.get('state') or {}).get('confirmed') or {}

        def canonical(plans):
            return sorted(json.dumps(p, sort_keys=True, ensure_ascii=False) for p in plans)

        # A period-only follow-up does not restate the measured concept. Accept
        # only the byte-for-byte plans that revalidate against the saved original
        # request; the bridge API performs this validation again at its boundary.
        carried = self._validated_followup_plans(resolution.get("state"))
        carried_plans = copy.deepcopy(carried[1]) if carried else []
        for prior in carried_plans:
            period = (confirmed_periods or {}).get(str(prior['table_id']))
            if period:
                prior.update(start_period=period[0], end_period=period[1])
                prior['exact_params'].update(startPrdDe=period[0], endPrdDe=period[1])
        if (carried and self._period_only_followup(query)
                and canonical(observed) == canonical(carried_plans)):
            return {**resolution, 'request_query': query, 'request_validation': {
                'valid': True, 'errors': [], 'table_ids': [p['table_id'] for p in observed],
                'evidence': 'catalog_revalidated_prior_plan_for_period_followup',
                'prior_request_query': carried[0],
            }}

        state = resolution.get("state") or {}
        base_query = str(state.get("_followup_base_request_query") or "")
        base_plans = state.get("_followup_base_api_plans")
        followup_query = str(state.get("_followup_query") or "")
        if (base_query and isinstance(base_plans, list) and base_plans
                and followup_query == query):
            base_context = self._validated_followup_plans({
                "_validated_request_query": base_query,
                "_validated_api_plans": base_plans,
            })
            expected_followup = self._compute_followup_plans(query, base_query, base_plans) if base_context else None
            if expected_followup and canonical(observed) == canonical(expected_followup):
                context_query = " ".join(self._followup_anchor_query(plan) for plan in observed)
                validated_state = {**state, "_validated_request_query": context_query,
                                   "_validated_api_plans": copy.deepcopy(observed),
                                   "_request_query": context_query}
                return {**resolution, "state": validated_state, "request_query": query,
                        "request_validation": {
                            "valid": True, "errors": [],
                            "table_ids": [plan["table_id"] for plan in observed],
                            "evidence": "prior_plan_revalidated_and_explicit_catalog_mutation",
                        }}

        # Reconstruct complete plans from current metadata rather than trusting
        # a model flag, table-name overlap, or only one member of a comparison.
        from request_match_guard import missing_requested_scopes
        incomplete=missing_requested_scopes(query,observed,self.resolver.tables)
        if (not incomplete and grounded_plans and canonical(grounded_plans) == canonical(observed)
                and all(self.resolver._matches_confirmed_filters(self.tables_by_id[p['table_id']], confirmed)
                        for p in observed)):
            validated_state = {**(resolution.get('state') or {}),
                               '_validated_request_query': query,
                               '_validated_api_plans': copy.deepcopy(observed)}
            return {**resolution, 'state': validated_state, 'request_query': query, 'request_validation': {
                'valid': True, 'errors': [], 'table_ids': [p['table_id'] for p in observed],
                'evidence': 'measured_concept_and_each_catalog_dimension',
            }}
        if grounded_plans and all('exact_params' in p for p in observed):
            return {**resolution, 'status': 'no_match', 'selected_table': None,
                    'selected_tables': [], 'api_plan': None, 'api_plans': [],
                    'request_validation': {'valid': False, 'errors': [
                        '요청한 모든 계열과 정본 조회 계획 또는 확정 조건이 일치하지 않습니다.'
                    ], 'table_ids': [p.get('table_id') for p in observed]},
                    'state': {**(resolution.get('state') or {}), 'status': 'no_match'}}
        request = structure_request(query)
        mentions=catalog_mentions(query,self.tables_by_id.values())
        plans = resolution.get("api_plans") or ([resolution["api_plan"]] if resolution.get("api_plan") else [])
        errors = []; covered = set(); covered_products=set()
        requested_products={p for p in ('주택담보대출','신용대출') if p in re.sub(r'\s+','',query)}
        unknown=unsupported_quantity_terms(query,self.tables_by_id.values())
        if unknown:
            errors.append('정본에서 확인할 수 없는 요청 지표: '+', '.join(unknown))
        confirmed = (resolution.get("state") or {}).get("confirmed") or {}
        selected_ids={str(p.get('table_id') or '') for p in plans}
        missing_ids={tid.upper() for tid in request['explicit_ids']} - {tid.upper() for tid in selected_ids}
        if missing_ids:
            errors.append('누락된 요청 통계표 ID: '+', '.join(sorted(missing_ids)))
        for mention in mentions:
            if not selected_ids.intersection(mention['table_ids']):
                errors.append('요청한 통계표 누락: '+mention['text'])
        for plan in plans:
            table = self.tables_by_id.get(str(plan.get("table_id") or ""))
            if not table:
                errors.append("정본 사전에 없는 통계표 ID입니다."); continue
            scoped=scoped_metric_query(query,table,mentions)
            valid, hits, _ = table_evidence(scoped, table)
            covered.update(hits)
            if not valid or not self.resolver._matches_metric_intent(scoped, table) or not self.resolver._matches_confirmed_filters(table, confirmed):
                errors.append(f"요청과 다른 통계표: {table['table_name']} ({table['table_id']})")
            if str(plan.get("table_name")) != str(table["table_name"]):
                errors.append("통계표 이름과 ID가 정본 사전에서 일치하지 않습니다.")
            measured_products=str(table['table_name'])
            for dimension in table.get('dimensions') or []:
                codes=str((plan.get('classifications') or {}).get(dimension.get('api_param')) or '').split('+')
                measured_products+=' '+' '.join(str(v.get('normalized') or v.get('value_name') or '') for v in dimension.get('values') or [] if str(v.get('value_id')) in codes)
            covered_products.update(p for p in requested_products if p in re.sub(r'\s+','',measured_products))
        if not plans: errors.append("검증할 통계표가 없습니다.")
        missing = set(request["metrics"]) - covered
        missing_products=requested_products-covered_products
        if missing_products:errors.append('조회 항목에 없는 대출 종류: '+', '.join(sorted(missing_products)))
        if missing: errors.append("누락된 요청 지표: " + ", ".join(sorted(missing)))
        validation = {"valid":not errors, "request":request, "errors":errors,
                      "table_ids":[p.get("table_id") for p in plans]}
        if errors:
            return {**resolution,"status":"no_match","selected_table":None,"selected_tables":[],
                    "api_plan":None,"api_plans":[],"request_validation":validation,
                    "missing_series":sorted(missing) or request["metrics"],
                    "state":{**(resolution.get("state") or {}),"status":"no_match"}}
        validated_state = {**(resolution.get("state") or {}),
                           "_validated_request_query": query,
                           "_validated_api_plans": copy.deepcopy(plans)}
        return {**resolution, "state": validated_state, "request_query": query, "request_validation": validation}

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
        confirmed_periods: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute an already resolved plan without repeating HCX/vector retrieval."""
        resolution = self.validate_resolution(query, resolution, confirmed_periods=confirmed_periods)
        if resolution.get("status") != "resolved":
            return {**resolution,"execution":{"status":"request_mismatch","rows":[],"row_count":0},"answer":""}
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
            requests=[dict(request_id=str(i),table_id=p['table_id'],item_id=p['item_id'],classifications=p['classifications'],frequency=p['frequency'],start_period=p['start_period'],end_period=p['end_period'],prefer_local=prefer_local,allow_fallback=False) for i,p in enumerate(plans)]
            from level_yoy_comparison import measurement_mode,comparison_modes,with_comparison_history,append_yoy
            # Validated raw-data plans need no further catalog lookup. Only
            # explicit derived measures require per-table query scoping.
            modes={str(p['table_id']):'raw' for p in plans}
            if measurement_mode(query)!='raw':
                modes=comparison_modes(query,plans,self.resolver.tables)
            level_yoy=any(mode!='raw' for mode in modes.values())
            if level_yoy:requests=[with_comparison_history([req])[0] if modes[req['table_id']]!='raw' else req for req in requests]
            batch=getattr(self.service,'get_statistics_batch',None)
            outcomes=batch(requests)['results'] if callable(batch) else None
            for index,plan in enumerate(plans):
                if outcomes is not None:
                    outcome=outcomes[index]
                    if outcome['status']!='success':
                        detail=outcome.get('error','조회 실패')
                        raise RuntimeError(f"{plan['table_name']}: {detail}")
                    data=outcome['data']
                else:
                    data=self.service.get_statistics(**{k:v for k,v in requests[index].items() if k!='request_id'})
                if data.get("status") != "success":
                    errors = data.get("errors") or []
                    detail = "; ".join(str(item.get("error") or item) for item in errors if item)
                    raise RuntimeError(detail or f"{plan['table_name']} 수치 데이터를 가져오지 못했습니다.")
                label = str(plan.get("series_label") or plan["table_name"])
                for row in data.get("rows") or []:
                    if row.get("TBL_ID") and str(row["TBL_ID"]) != str(plan["table_id"]):
                        raise ValueError("KOSIS 응답의 통계표 ID가 요청한 표와 다릅니다.")
                    enriched = dict(row)
                    enriched["_SERIES_LABEL"] = label
                    enriched["_SOURCE_SERIES_ID"] = str(plan["table_id"])
                    enriched["_FREQUENCY"] = str(plan["frequency"])
                    all_rows.append(enriched)
                sources.append({"table_id": plan["table_id"], "source": data.get("source"), "row_count": len(data.get("rows") or [])})
            calculations=[];missing=[]
            if level_yoy:all_rows,calculations,missing=append_yoy(all_rows,plans,modes)
            execution = {"status": "success", "rows": all_rows, "row_count": len(all_rows), "sources": sources}
            if calculations:execution.update(calculations=calculations,missingComparisonPeriods=missing,
                comparisonFetchPeriods=[{'table_id':req['table_id'],'start':req['start_period'],'end':req['end_period']} for req in requests if modes[req['table_id']]!='raw'])
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
