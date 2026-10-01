from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def numeric(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-root", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    golden_root, project_root = Path(args.golden_root), Path(args.project_root)
    os.environ.setdefault("STATBRIDGE_DATA_DIR", str(project_root / "runtime_data" / "processed"))
    os.environ.setdefault("STATBRIDGE_TABLES_DIR", str(project_root / "runtime_data" / "tables"))
    sys.path.insert(0, str(project_root / "statbridge_mcp_server"))
    from statbridge_mcp.statistics_service import StatisticsService

    cases: list[dict[str, Any]] = []
    for path in sorted((golden_root / "cases").glob("*.json")):
        cases.extend(json.loads(path.read_text(encoding="utf-8-sig")))
    api_predictions = {x["id"]: x for x in load_jsonl(project_root / "final_golden_api_plan_predictions.jsonl")}
    service = StatisticsService()
    results: list[dict[str, Any]] = []
    all_point_exact: list[bool] = []
    all_point_tolerance: list[bool] = []
    all_period: list[bool] = []
    all_unit: list[bool] = []
    all_mapping: list[bool] = []
    case_exact: list[bool] = []

    for case in cases:
        chart = case["expected"].get("chart")
        if not chart:
            continue
        fixture = json.loads((golden_root / chart["fixture"]).read_text(encoding="utf-8-sig"))
        plans = api_predictions[str(case["id"])].get("api_plans") or []
        plans_by_table = {str(x.get("table_id")): x for x in plans}
        series_rows: list[dict[str, Any]] = []
        this_case_exact = True
        for expected in fixture.get("series") or []:
            table_id = str(expected.get("table_id"))
            plan = plans_by_table.get(table_id)
            if not plan:
                series_rows.append({"table_id": table_id, "status": "missing_plan", "exact": False})
                this_case_exact = False
                all_mapping.append(False)
                continue
            try:
                output = service.get_statistics(
                    table_id=plan["table_id"], item_id=plan["item_id"], classifications=plan.get("classifications") or {},
                    frequency=plan.get("frequency"), start_period=plan.get("start_period"), end_period=plan.get("end_period"),
                    prefer_local=True, allow_fallback=True,
                )
                rows = output.get("rows") or []
                actual_by_period: dict[str, dict[str, Any]] = {}
                for row in rows:
                    period = str(row.get("PRD_DE") or row.get("period") or "")
                    if period:
                        actual_by_period[period] = row
                point_results = []
                for point in expected.get("points") or []:
                    period = str(point.get("period"))
                    actual = actual_by_period.get(period)
                    expected_value = numeric(point.get("value"))
                    actual_value = numeric((actual or {}).get("DT") if actual else None)
                    period_ok = actual is not None
                    exact_ok = period_ok and actual_value == expected_value
                    tolerance_ok = period_ok and actual_value is not None and expected_value is not None and math.isclose(actual_value, expected_value, rel_tol=1e-9, abs_tol=1e-9)
                    all_period.append(period_ok); all_point_exact.append(exact_ok); all_point_tolerance.append(tolerance_ok)
                    point_results.append({"period": period, "expected_value": expected_value, "actual_value": actual_value, "period_match": period_ok, "exact_match": exact_ok, "tolerance_match": tolerance_ok})
                sample = rows[0] if rows else {}
                actual_unit = str(sample.get("UNIT_NM") or sample.get("UNIT") or "")
                unit_ok = actual_unit == str(expected.get("unit") or "")
                mapping_ok = str(plan.get("item_id")) == str(expected.get("item_id")) and all(
                    str((plan.get("classifications") or {}).get(key)) == str(value) for key, value in (expected.get("classifications") or {}).items()
                )
                all_unit.append(unit_ok); all_mapping.append(mapping_ok)
                series_exact = mapping_ok and unit_ok and bool(point_results) and all(x["exact_match"] for x in point_results)
                this_case_exact = this_case_exact and series_exact
                series_rows.append({
                    "table_id": table_id, "status": output.get("status"), "source": output.get("source"),
                    "row_count": output.get("row_count", len(rows)), "mapping_match": mapping_ok,
                    "expected_unit": expected.get("unit") or "", "actual_unit": actual_unit, "unit_match": unit_ok,
                    "points": point_results, "exact": series_exact, "errors": output.get("errors") or [],
                })
            except Exception as exc:
                this_case_exact = False
                all_mapping.append(False)
                series_rows.append({"table_id": table_id, "status": "error", "error": f"{type(exc).__name__}: {exc}", "exact": False})
        case_exact.append(this_case_exact)
        results.append({
            "id": case["id"], "fixture": chart["fixture"], "chart_type": chart.get("type"), "layout": chart.get("layout"),
            "expected_series_count": len(fixture.get("series") or []), "predicted_series_count": len(plans),
            "series_count_match": len(fixture.get("series") or []) == len(plans), "series": series_rows, "chart_data_exact": this_case_exact,
        })

    def acc(values: list[bool]) -> float | None:
        return round(sum(values) / len(values), 6) if values else None

    summary = {
        "case_count": len(results), "runtime_path": "StatisticsService.get_statistics -> KOSIS/local fallback",
        "chart_data_exact_accuracy": acc(case_exact), "period_point_accuracy": acc(all_period),
        "numeric_exact_accuracy": acc(all_point_exact), "numeric_tolerance_accuracy": acc(all_point_tolerance),
        "unit_accuracy": acc(all_unit), "series_mapping_accuracy": acc(all_mapping),
        "executed_case_count": sum(any(s.get("source") in {"kosis_api", "local_csv"} for s in x["series"]) for x in results),
    }
    (project_root / "final_golden_chart_results.json").write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
