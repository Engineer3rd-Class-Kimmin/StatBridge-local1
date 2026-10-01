from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "src" / "agent"))
from evaluate_jev_hybrid_100 import hcx_classification, read_jsonl, summarize  # noqa: E402
from jev_series_client import JevSeriesClient  # noqa: E402
from jev_series_hybrid import apply_jev_series_decision  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the current narrow Jev prompt once over the frozen 100-case gold set.")
    parser.add_argument("--typesafe-env", type=Path, required=True)
    parser.add_argument("--hcx", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=ROOT / "eval" / "goldset" / "goldset_statbridge_hcx003_role_100.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "runs" / "20261001_jev_hybrid_live_100")
    args = parser.parse_args()
    load_dotenv(ROOT / "data" / "statbridge_mcp_server" / ".env", override=False)
    load_dotenv(args.typesafe_env, override=False)
    # Settings are read when the client is constructed, after both env files load.
    client = JevSeriesClient()
    if not client.configured:
        raise SystemExit("TYPESAFE_API_KEY is missing or Jev is disabled")
    gold = {x["case_id"]: x for x in read_jsonl(args.gold)}
    first_hcx = {}
    for row in read_jsonl(args.hcx):
        first_hcx.setdefault(row["case_id"], row)
    if len(gold) != 100 or len(first_hcx) != 100:
        raise SystemExit(f"Expected 100 paired cases; got gold={len(gold)}, HCX={len(first_hcx)}")
    rows = []
    for index, (case_id, expected) in enumerate(gold.items(), 1):
        hcx = first_hcx[case_id]
        classification = hcx_classification(hcx)
        started = time.perf_counter()
        fallback = False
        try:
            decision = client.classify_series_count(hcx["query"], classification)
            final, trace = apply_jev_series_decision(classification, decision)
            jev_count = decision["series_count"]
        except Exception as exc:
            fallback = True
            final = classification
            trace = {"series_action": "fallback", "error_type": type(exc).__name__}
            jev_count = None
        score = hcx.get("score") or {}
        final_count = len(final.get("series") or [])
        rows.append({
            "case_id": case_id, "query": hcx["query"], "valid": bool(hcx.get("valid")),
            "latency_s": float(hcx.get("latency_s") or 0) + time.perf_counter() - started,
            "domain_correct": bool(score.get("domain_correct")), "intent_f1": float(score.get("intent_f1") or 0),
            "condition_f1": float(score.get("qualifier_f1") or 0), "frequency_correct": score.get("frequency_correct"),
            "jev_series_count": jev_count, "final_series_count": final_count,
            "series_count_correct": final_count == int(expected.get("expected_series_count") or 0),
            "series_action": trace.get("series_action"), "jev_fallback_used": fallback,
        })
        print(f"{index}/100 {case_id} jev={jev_count} final={final_count} action={trace.get('series_action')}", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "hybrid_results.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    result = {"Hybrid-live-current-prompt": summarize(rows), "jev_fallback_count": sum(x["jev_fallback_used"] for x in rows), "method": "One live Jev call per frozen gold case, paired with saved HCX-003 repeat-1 output."}
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
