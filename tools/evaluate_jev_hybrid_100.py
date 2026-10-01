from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "agent"))
from jev_series_hybrid import apply_jev_series_decision  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    index = (len(values) - 1) * p
    lo, hi = int(index), min(int(index) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def hcx_classification(row: dict) -> dict:
    try:
        text = row["raw"]["result"]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def summarize(rows: list[dict]) -> dict:
    valid = [row for row in rows if row["valid"]]
    latencies = [float(row["latency_s"]) for row in rows]
    def avg(field: str, none_is_skip: bool = False) -> float:
        values = [row[field] for row in rows if not none_is_skip or row[field] is not None]
        return round(100 * sum(float(x) for x in values) / len(values), 2) if values else 0.0
    return {
        "n_cases": len({row["case_id"] for row in rows}),
        "n_runs": len(rows),
        "domain_accuracy_pct": avg("domain_correct"),
        "intent_f1_pct": avg("intent_f1"),
        "condition_f1_pct": avg("condition_f1"),
        "frequency_accuracy_pct": avg("frequency_correct", True),
        "series_count_accuracy_pct": avg("series_count_correct"),
        "valid_output_pct": round(100 * len(valid) / len(rows), 2),
        "api_error_pct": round(100 * sum(not row["valid"] for row in rows) / len(rows), 2),
        "latency_p50_s": round(statistics.median(latencies), 3),
        "latency_p95_s": round(percentile(latencies, 0.95), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce the HCX-only vs Jev-series hybrid 100-case comparison from saved API runs.")
    parser.add_argument("--gold", type=Path, default=ROOT / "eval" / "goldset" / "goldset_statbridge_hcx003_role_100.jsonl")
    parser.add_argument("--hcx", type=Path, required=True)
    parser.add_argument("--jev", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "runs" / "20261001_jev_hybrid_100")
    args = parser.parse_args()
    gold = {x["case_id"]: x for x in read_jsonl(args.gold)}
    hcx_rows = read_jsonl(args.hcx)
    jev_rows = {(x["case_id"], x["repeat"]): x for x in read_jsonl(args.jev)}
    if len(gold) != 100 or len(hcx_rows) != 300 or len(jev_rows) != 300:
        raise SystemExit(f"Expected 100 gold cases and 300 paired runs; got {len(gold)}, {len(hcx_rows)}, {len(jev_rows)}")
    output = []
    for hcx in hcx_rows:
        key = (hcx["case_id"], hcx["repeat"])
        jev = jev_rows[key]
        expected = int(gold[hcx["case_id"]].get("expected_series_count") or 0)
        valid = bool(hcx.get("valid"))
        classification = hcx_classification(hcx)
        jev_count = int((jev.get("pred") or {}).get("series_count") or 0)
        final, trace = apply_jev_series_decision(classification, {"series_count": jev_count, "probability": None})
        final_count = len(final.get("series") or [])
        score = hcx.get("score") or {}
        output.append({
            "case_id": hcx["case_id"], "repeat": hcx["repeat"], "query": hcx["query"],
            "valid": valid, "latency_s": float(hcx.get("latency_s") or 0) + float(jev.get("latency_s") or 0),
            "domain_correct": bool(score.get("domain_correct")), "intent_f1": float(score.get("intent_f1") or 0),
            "condition_f1": float(score.get("qualifier_f1") or 0), "frequency_correct": score.get("frequency_correct"),
            "hcx_series_count": int((hcx.get("pred") or {}).get("series_count") or 0), "jev_series_count": jev_count,
            "final_series_count": final_count, "series_action": trace["series_action"],
            "series_count_correct": final_count == expected,
        })
    hcx_normalized = []
    for row in hcx_rows:
        score = row.get("score") or {}
        hcx_normalized.append({
            "case_id": row["case_id"], "valid": bool(row.get("valid")), "latency_s": float(row.get("latency_s") or 0),
            "domain_correct": bool(score.get("domain_correct")), "intent_f1": float(score.get("intent_f1") or 0),
            "condition_f1": float(score.get("qualifier_f1") or 0), "frequency_correct": score.get("frequency_correct"),
            "series_count_correct": bool(score.get("series_count_correct")),
        })
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "hybrid_results.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in output) + "\n", encoding="utf-8")
    summary = {"HCX-only": summarize(hcx_normalized), "Hybrid": summarize(output), "provenance": {"gold": str(args.gold), "hcx": str(args.hcx), "jev": str(args.jev), "note": "Paired replay of previously saved 3-repeat live API responses; Hybrid latency is HCX plus Jev latency."}}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
