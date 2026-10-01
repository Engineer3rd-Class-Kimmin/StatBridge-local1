from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    "최근 경제심리지수 추이 보여줘",
    "최근 5년 기준금리와 대출금리를 비교해줘",
    "기준금리, 예금금리, 대출금리를 최근 3년간 비교해줘",
    "서울과 부산의 소비자물가지수를 비교해줘",
]


def compact(result: dict) -> dict:
    classification = result.get("classification") or {}
    plans = result.get("api_plans") or ([result.get("api_plan")] if result.get("api_plan") else [])
    execution = result.get("execution") or {}
    return {
        "status": result.get("status"),
        "hcx": {key: classification.get(key) for key in ("normalized_query", "concepts", "subjects", "measures", "time_terms", "comparison_terms", "qualifiers", "series")},
        "jev_trace": classification.get("_jev_trace"),
        "tables": [{"table_id": p.get("table_id"), "table_name": p.get("table_name"), "series_label": p.get("series_label")} for p in plans if p],
        "mcp": {"status": execution.get("status"), "row_count": len(execution.get("rows") or []), "error": execution.get("error")},
        "clarification": result.get("question"),
        "orchestration": result.get("orchestration"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--typesafe-env", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "runs" / "20261001_jev_hybrid_e2e.json")
    args = parser.parse_args()
    load_dotenv(ROOT / "data" / "statbridge_mcp_server" / ".env", override=False)
    if args.typesafe_env:
        load_dotenv(args.typesafe_env, override=False)
    sys.path.insert(0, str(ROOT / "src" / "agent"))
    sys.path.insert(0, str(ROOT / "data" / "statbridge_mcp_server"))
    from bridge_api import agent

    records = []
    for query in QUESTIONS:
        started = time.perf_counter()
        result = agent.run(query=query, execute=False, generate_answer=False)
        records.append({"query": query, "elapsed_s": round(time.perf_counter() - started, 3), **compact(result)})

    # One trusted single-metric table is executed through the same MCP gateway to KOSIS.
    live = agent.run(query=QUESTIONS[0], execute=True, generate_answer=False, output_request={"chart_type": "line"})
    mcp_probe = {"query": QUESTIONS[0], **compact(live)}

    # Feature flag behavior is verified on a fresh client without mutating the live agent.
    from jev_series_client import JevSeriesClient, JevSettings
    disabled = JevSeriesClient(JevSettings(api_key="", endpoint="https://api.typesafe.ai", model="jev-preview", enabled=False, timeout=10))
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jev_key_configured": bool(os.getenv("TYPESAFE_API_KEY")),
        "records": records,
        "mcp_kosis_probe": mcp_probe,
        "disabled_probe": {"enabled": disabled.settings.enabled, "configured": disabled.configured},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
