from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "agent"))

from statbridge_agent import StatBridgeAgent  # noqa: E402


def compact_candidate(item: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "table_id": item.get("table_id"),
        "table_name": item.get("table_name"),
        "rule_score": item.get("rule_score", item.get("score")),
        "vector_score": item.get("vector_score"),
        "reranker_score": item.get("rerank_score"),
        "final_score": item.get("final_score", item.get("score")),
        "retrieval_path": item.get("retrieval_path"),
        "reasons": item.get("reasons") or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Blind query-only StatBridge final Golden Set baseline")
    parser.add_argument("--queries", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--retrieval", required=True)
    parser.add_argument("--api-plans", required=True)
    args = parser.parse_args()

    query_path = Path(args.queries)
    queries = [json.loads(line) for line in query_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    agent = StatBridgeAgent(object())
    mode = {
        "agent_ncp_configured": agent.ncp.configured,
        "hybrid_enabled": agent.hybrid.enabled,
        "retrieval_client_configured": agent.hybrid.client.configured,
        "vector_path_exists": agent.hybrid.path.exists(),
        "vector_collection_count": len(agent.hybrid._load()),
    }
    if not all((mode["agent_ncp_configured"], mode["hybrid_enabled"], mode["retrieval_client_configured"], mode["vector_path_exists"])):
        raise RuntimeError(f"Full hybrid baseline unavailable: {mode}")

    predictions: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    retrieval_rows: list[dict[str, Any]] = []
    api_rows: list[dict[str, Any]] = []
    state: dict[str, Any] | None = None
    previous_id: str | None = None

    for index, case in enumerate(queries, start=1):
        started = time.perf_counter()
        query = str(case["query"])
        case_id = str(case["id"])
        # Only explicit query-only context may flow between cases. The supplied
        # final set has no prior_turns, so every case starts with a clean state.
        prior_turns = case.get("prior_turns") or []
        if not prior_turns:
            state = None
            previous_id = None
        try:
            rule_started = time.perf_counter()
            rule_candidates = agent.resolver.rank(query, top_k=12)
            rule_finished = time.perf_counter()
            result = agent.resolve(query, state=state)
            finished = time.perf_counter()
            status = {"resolved": "select", "need_clarification": "clarify"}.get(
                str(result.get("status") or "no_match"), str(result.get("status") or "no_match")
            )
            selected = result.get("selected_tables") or ([result["selected_table"]] if result.get("selected_table") else [])
            selected_ids = list(dict.fromkeys(str(item.get("table_id")) for item in selected if item.get("table_id")))
            hybrid_candidates = list(result.get("candidates") or [])
            classification = dict(result.get("classification") or {})
            plans = list(result.get("api_plans") or ([result["api_plan"]] if result.get("api_plan") else []))
            retrieval_path = "full_hybrid"
            if hybrid_candidates and all(item.get("vector_score") in (None, 0, 0.0) for item in hybrid_candidates):
                retrieval_path = str(hybrid_candidates[0].get("retrieval_path") or "rule_fallback_or_fast_path")
            predictions.append({
                "id": case_id,
                "predicted_status": status,
                "predicted_table_ids": selected_ids,
            })
            traces.append({
                "id": case_id,
                "query": query,
                "case_index": index,
                "previous_case_id": previous_id,
                "predicted_status": status,
                "predicted_table_ids": selected_ids,
                "dictionary_query": result.get("dictionary_query"),
                "classification_status": classification.get("status"),
                "classification_model": classification.get("model"),
                "classification": classification,
                "clarifications": result.get("clarifications") or [],
                "retrieval_path": retrieval_path,
                "raw_top_k": [compact_candidate(item, rank) for rank, item in enumerate(hybrid_candidates[:12], 1)],
                "api_plan_count": len(plans),
                "error": None,
                "timings_ms": {
                    "rule_only_rank": round((rule_finished - rule_started) * 1000, 3),
                    "agent_resolve": round((finished - rule_finished) * 1000, 3),
                    "total": round((finished - started) * 1000, 3),
                },
            })
            retrieval_rows.append({
                "id": case_id,
                "rule_only_top_k": [compact_candidate(item, rank) for rank, item in enumerate(rule_candidates[:12], 1)],
                "full_hybrid_top_k": [compact_candidate(item, rank) for rank, item in enumerate(hybrid_candidates[:12], 1)],
                "full_hybrid_execution_path": retrieval_path,
            })
            api_rows.append({"id": case_id, "predicted_status": status, "predicted_table_ids": selected_ids, "api_plans": plans})
            state = result.get("state") if prior_turns else None
            previous_id = case_id
        except Exception as exc:  # preserve failures rather than silently changing modes
            finished = time.perf_counter()
            predictions.append({"id": case_id, "predicted_status": "error", "predicted_table_ids": []})
            traces.append({
                "id": case_id, "query": query, "case_index": index,
                "predicted_status": "error", "predicted_table_ids": [], "raw_top_k": [],
                "api_plan_count": 0, "error": f"{type(exc).__name__}: {exc}",
                "timings_ms": {"total": round((finished - started) * 1000, 3)},
            })
            retrieval_rows.append({"id": case_id, "rule_only_top_k": [], "full_hybrid_top_k": [], "error": f"{type(exc).__name__}: {exc}"})
            api_rows.append({"id": case_id, "predicted_status": "error", "predicted_table_ids": [], "api_plans": [], "error": f"{type(exc).__name__}: {exc}"})

    def write_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
        Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")

    write_jsonl(args.predictions, predictions)
    write_jsonl(args.trace, traces)
    write_jsonl(args.api_plans, api_rows)
    Path(args.retrieval).write_text(json.dumps({"mode": mode, "cases": retrieval_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(predictions), "errors": sum(row["predicted_status"] == "error" for row in predictions), "mode": mode}, ensure_ascii=False))


if __name__ == "__main__":
    main()
