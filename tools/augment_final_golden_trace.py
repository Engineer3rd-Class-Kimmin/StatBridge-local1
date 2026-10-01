from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "agent"))
from stat_dictionary.stat_language_resolver import StatLanguageResolver  # noqa: E402


project = ROOT.parent
resolver = StatLanguageResolver(ROOT / "src" / "agent" / "stat_dictionary" / "stat_language_dictionary.json")
traces = [json.loads(line) for line in (project / "final_golden_trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
api = {row["id"]: row for row in (json.loads(line) for line in (project / "final_golden_api_plan_predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
for row in traces:
    parsed = resolver.parse_query_state(str(row["query"]))
    plans = api[row["id"]].get("api_plans") or []
    row["prior_turns"] = []
    row["retrieval_mode"] = "rule_embedding_reranker" if row.get("retrieval_path") == "full_hybrid" else row.get("retrieval_path")
    row["parsed_intent"] = row.get("classification") or {}
    row["query_state"] = asdict(parsed)
    row["parsed_operations"] = []
    row["series_requests"] = (row.get("classification") or {}).get("series") or parsed.series
    row["selected_series"] = row.get("predicted_table_ids") or []
    row["api_plan"] = plans[0] if plans else {
        "table_id": None, "item_id": None, "frequency": None,
        "start_period": None, "end_period": None, "classifications": {},
    }
(project / "final_golden_trace.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in traces), encoding="utf-8", newline="\n")
print(json.dumps({"augmented": len(traces)}, ensure_ascii=False))
