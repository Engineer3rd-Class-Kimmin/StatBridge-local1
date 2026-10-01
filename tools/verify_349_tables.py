"""Verify every registered table with a real small KOSIS request.

Results are resumable in evaluation_runs/349_live_verification.json. No API keys
or raw observations are written. A registered table is not a live-test pass.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("STATBRIDGE_DATA_DIR", str(ROOT / "data/runtime_data/processed"))
sys.path[:0] = [str(ROOT / "data/statbridge_mcp_server"), str(ROOT / "src/agent")]
from statbridge_mcp.kosis_client import KosisClient, KosisApiError
from statbridge_mcp.metadata_store import MetadataStore
from stat_dictionary.stat_language_resolver import StatLanguageResolver
from statbridge_agent import StatBridgeAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    resolver = StatLanguageResolver(ROOT / "src/agent/stat_dictionary/stat_language_dictionary.json")
    store = MetadataStore()
    assert len(resolver.tables) == 349
    assert set(resolver.tables_by_id) == {t.table_id for t in store.available_supported_tables()}
    client = KosisClient()
    client.session.trust_env = False
    report_path = ROOT / "evaluation_runs/349_live_verification.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    records = json.loads(report_path.read_text(encoding="utf-8"))["tables"] if args.resume and report_path.exists() else []
    completed = {r["table_id"] for r in records if r["status"] == "ok"}
    records = [r for r in records if r["table_id"] in completed]
    def save():
        result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "catalog_count": 349,
                  "checked": len(records), "passed": sum(r["status"] == "ok" for r in records), "tables": records}
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for table in resolver.tables:
        tid = table["table_id"]
        if tid in completed:
            continue
        selected = resolver.resolve(tid)
        assert selected["status"] == "resolved" and selected["selected_table"]["table_id"] == tid
        classes = StatBridgeAgent._merge_classifications(table, selected["selected_table"])
        frequency = table["api_call_params"].get("prdSe") or table["prd_se"]
        period = table["period_end_observed"]
        record = {"table_id": tid, "status": "error", "period": period, "frequency": frequency}
        try:
            rows = client.get_statistics(table.get("org_id", "301"), tid, table["item_ids"][0], frequency, period, period, classes)
            numeric = []
            for row in rows:
                try:
                    numeric.append(float(str(row.get("DT", "")).replace(",", "")))
                except ValueError:
                    pass
            record.update(status="ok" if numeric else "no_numeric_data", rows=len(rows), numeric_rows=len(numeric))
        except KosisApiError as exc:
            record.update(error_code=exc.code, error_message=exc.msg)
        except Exception as exc:
            # Network exception strings can contain a credential-bearing URL.
            record.update(error_type=type(exc).__name__)
        records.append(record)
        save()
        if len(records) % 10 == 0 or record["status"] != "ok":
            print(json.dumps({"checked": len(records), "passed": sum(r["status"] == "ok" for r in records), "last": record}, ensure_ascii=True), flush=True)
    save()
    print(json.dumps({"checked": len(records), "passed": sum(r["status"] == "ok" for r in records)}, ensure_ascii=True))
    raise SystemExit(0 if len(records) == 349 and all(r["status"] == "ok" for r in records) else 1)


if __name__ == "__main__":
    main()
