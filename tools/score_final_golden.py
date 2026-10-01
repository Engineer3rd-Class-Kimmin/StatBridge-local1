from __future__ import annotations

import argparse
import collections
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def ratio(a: int | float, b: int | float) -> float | None:
    return round(float(a) / float(b), 6) if b else None


def expected_status(raw: str) -> str:
    return {"candidate_found": "select", "need_clarification": "clarify"}.get(raw, raw)


def group_metrics(groups: list[list[str]], ranked: list[str], k: int) -> tuple[int, int]:
    top = set(ranked[:k])
    return sum(bool(top.intersection(group)) for group in groups), len(groups)


def final_set_score(groups: list[list[str]], predicted: list[str]) -> dict[str, Any]:
    predicted_set = set(predicted)
    union = set().union(*(set(group) for group in groups)) if groups else set()
    matched = sum(bool(predicted_set.intersection(group)) for group in groups)
    tp = sum(1 for value in predicted_set if value in union)
    precision = ratio(tp, len(predicted_set)) if predicted_set else (1.0 if not groups else 0.0)
    recall = ratio(matched, len(groups)) if groups else (1.0 if not predicted_set else 0.0)
    f1 = ratio(2 * precision * recall, precision + recall) if precision is not None and recall is not None and precision + recall else 0.0
    exact = matched == len(groups) and len(predicted_set) == len(groups) and predicted_set.issubset(union)
    return {"exact": exact, "precision": precision, "recall": recall, "f1": f1}


def retrieval_metrics(rows: list[dict[str, Any]], mode_key: str, cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hits = {1: 0, 3: 0, 5: 0}
    denominator = 0
    reciprocal: list[float] = []
    case_top1 = 0
    case_count = 0
    for row in rows:
        gold = cases[row["id"]]["expected"].get("retrieval_groups") or []
        if not gold:
            continue
        ranked = [str(x.get("table_id")) for x in row.get(mode_key, []) if x.get("table_id")]
        denominator += len(gold)
        case_count += 1
        case_top1 += int(all(set(ranked[:1]).intersection(group) for group in gold))
        for k in hits:
            found, _ = group_metrics(gold, ranked, k)
            hits[k] += found
        for group in gold:
            rank = next((i for i, table_id in enumerate(ranked, 1) if table_id in group), None)
            reciprocal.append(1.0 / rank if rank else 0.0)
    return {
        "case_count": case_count,
        "required_group_count": denominator,
        "recall_at_1": ratio(hits[1], denominator),
        "recall_at_3": ratio(hits[3], denominator),
        "recall_at_5": ratio(hits[5], denominator),
        "mrr": round(statistics.fmean(reciprocal), 6) if reciprocal else None,
        "case_all_groups_top1": ratio(case_top1, case_count),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--golden-root", required=True)
    p.add_argument("--project-root", required=True)
    args = p.parse_args()
    golden_root, project_root = Path(args.golden_root), Path(args.project_root)
    rows: list[dict[str, Any]] = []
    for path in sorted((golden_root / "cases").glob("*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8-sig")))
    cases = {str(row["id"]): row for row in rows}
    predictions = {row["id"]: row for row in load_jsonl(project_root / "final_golden_predictions.jsonl")}
    traces = {row["id"]: row for row in load_jsonl(project_root / "final_golden_trace.jsonl")}
    api_predictions = {row["id"]: row for row in load_jsonl(project_root / "final_golden_api_plan_predictions.jsonl")}
    retrieval_doc = json.loads((project_root / "final_golden_retrieval_predictions.json").read_text(encoding="utf-8"))
    retrieval_rows = retrieval_doc["cases"]

    scored: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    category_counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"total": 0, "status_correct": 0, "exact": 0})
    status_correct = exact_count = single_exact = multi_exact = 0
    single_count = multi_count = 0
    multi_precision: list[float] = []
    multi_recall: list[float] = []
    multi_f1: list[float] = []
    clarify_correct = no_match_correct = 0
    clarify_count = no_match_count = 0

    for row in rows:
        case_id = str(row["id"])
        pred = predictions[case_id]
        gold_status = expected_status(str(row["expected"]["status"]))
        pred_status = str(pred["predicted_status"])
        groups = row["expected"].get("retrieval_groups") or []
        final = final_set_score(groups, list(pred.get("predicted_table_ids") or []))
        status_ok = pred_status == gold_status
        overall_exact = status_ok and (final["exact"] if gold_status == "select" else True)
        category = str(row["category"])
        category_counts[category]["total"] += 1
        category_counts[category]["status_correct"] += int(status_ok)
        category_counts[category]["exact"] += int(overall_exact)
        status_correct += int(status_ok)
        exact_count += int(overall_exact)
        if gold_status == "select":
            if len(groups) > 1:
                multi_count += 1; multi_exact += int(final["exact"])
                multi_precision.append(float(final["precision"])); multi_recall.append(float(final["recall"])); multi_f1.append(float(final["f1"]))
            else:
                single_count += 1; single_exact += int(final["exact"])
        elif gold_status == "clarify":
            clarify_count += 1; clarify_correct += int(status_ok)
        elif gold_status == "no_match":
            no_match_count += 1; no_match_correct += int(status_ok)
        detail = {
            "id": case_id, "category": category, "query": row["query"],
            "expected_status": gold_status, "predicted_status": pred_status,
            "expected_retrieval_groups": groups, "predicted_table_ids": pred.get("predicted_table_ids") or [],
            "status_correct": status_ok, "table_set": final, "overall_exact": overall_exact,
        }
        scored.append(detail)
        if not overall_exact:
            ranked = traces[case_id].get("raw_top_k") or []
            union = set().union(*(set(group) for group in groups)) if groups else set()
            in_top5 = bool(union.intersection(str(x.get("table_id")) for x in ranked[:5]))
            predicted_set = set(pred.get("predicted_table_ids") or [])
            if gold_status in {"clarify", "no_match"} or pred_status in {"clarify", "no_match"}:
                layer = "H. clarify/no_match"
            elif len(groups) > 1 and final["recall"] < 1:
                layer = "F. multi-series missing"
            elif len(groups) > 1 and final["precision"] < 1:
                layer = "G. extra series"
            elif not in_top5:
                layer = "C. retrieval candidate miss"
            elif ranked and str(ranked[0].get("table_id")) not in union:
                layer = "D. sibling Top-1 error"
            elif predicted_set - union:
                layer = "E. condition / dimension error"
            elif category == "alias":
                layer = "B. alias/general language"
            else:
                layer = "A. core metric understanding"
            failures.append({
                "id": case_id, "query": row["query"], "expected_status": gold_status,
                "predicted_status": pred_status, "expected_table_groups": groups,
                "predicted_table_ids": list(predicted_set), "raw_top_k": ranked,
                "failure_layer": layer,
                "suspected_general_cause": "정답 후보가 Top-5에 없음" if not in_top5 and groups else "후보 순위 또는 최종 상태/집합 결정 불일치",
            })

    category_metrics = {
        key: {**value, "status_accuracy": ratio(value["status_correct"], value["total"]), "exact_accuracy": ratio(value["exact"], value["total"])}
        for key, value in sorted(category_counts.items())
    }
    final_metrics = {
        "case_count": len(rows), "status_accuracy": ratio(status_correct, len(rows)),
        "table_set_or_decision_exact": ratio(exact_count, len(rows)),
        "single_exact": ratio(single_exact, single_count), "single_count": single_count,
        "multi_exact": ratio(multi_exact, multi_count), "multi_count": multi_count,
        "multi_precision": round(statistics.fmean(multi_precision), 6) if multi_precision else None,
        "multi_recall": round(statistics.fmean(multi_recall), 6) if multi_recall else None,
        "multi_f1": round(statistics.fmean(multi_f1), 6) if multi_f1 else None,
        "clarify_accuracy": ratio(clarify_correct, clarify_count), "clarify_count": clarify_count,
        "no_match_accuracy": ratio(no_match_correct, no_match_count), "no_match_count": no_match_count,
        "by_category": category_metrics,
    }

    rule_metrics = retrieval_metrics(retrieval_rows, "rule_only_top_k", cases)
    hybrid_metrics = retrieval_metrics(retrieval_rows, "full_hybrid_top_k", cases)
    trace_times = [float(x.get("timings_ms", {}).get("total") or 0) for x in traces.values()]
    rule_times = [float(x.get("timings_ms", {}).get("rule_only_rank") or 0) for x in traces.values()]
    rule_metrics["mean_latency_ms"] = round(statistics.fmean(rule_times), 3)
    hybrid_metrics["mean_agent_total_latency_ms"] = round(statistics.fmean(trace_times), 3)
    paths = collections.Counter(str(x.get("retrieval_path") or "unknown") for x in traces.values())
    ablation = {
        "dataset": "final_golden_150_blind",
        "modes": {
            "rule_only": {"available": True, "retrieval_mode": "rule_only", **rule_metrics},
            "rule_embedding": {"available": False, "reason": "Production has no supported reranker-off switch or separately frozen fusion; no synthetic weighting was introduced."},
            "rule_embedding_reranker": {"available": True, "retrieval_mode": "rule_embedding_reranker", **hybrid_metrics, "execution_paths": dict(paths)},
        },
    }

    api_results: list[dict[str, Any]] = []
    chart_results: list[dict[str, Any]] = []
    api_field_totals: collections.Counter[str] = collections.Counter()
    api_field_hits: collections.Counter[str] = collections.Counter()
    golden_issues: list[dict[str, Any]] = []
    for row in rows:
        chart = row["expected"].get("chart")
        if not chart:
            continue
        fixture_path = golden_root / str(chart["fixture"])
        fixture = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
        plans = api_predictions[str(row["id"])].get("api_plans") or []
        plans_by_table = {str(x.get("table_id")): x for x in plans}
        series_results = []
        for series in fixture.get("series") or []:
            table_id = str(series.get("table_id"))
            plan = plans_by_table.get(table_id)
            fields = {
                "table_id": bool(plan),
                "item_id": bool(plan and str(plan.get("item_id")) == str(series.get("item_id"))),
                "frequency": bool(plan and str(plan.get("frequency")) == str(series.get("frequency"))),
                "period_start": bool(plan and str(plan.get("start_period")) == str((series.get("points") or [{}])[0].get("period"))),
                "period_end": bool(plan and str(plan.get("end_period")) == str((series.get("points") or [{}])[-1].get("period"))),
                "classifications": bool(plan and all(str((plan.get("classifications") or {}).get(k)) == str(v) for k, v in (series.get("classifications") or {}).items())),
            }
            for key, value in fields.items():
                api_field_totals[key] += 1; api_field_hits[key] += int(value)
            series_results.append({"table_id": table_id, "predicted_plan": plan, "field_matches": fields})
        api_results.append({"id": row["id"], "fixture": chart["fixture"], "series": series_results})
        chart_results.append({
            "id": row["id"], "fixture": chart["fixture"], "requested_chart_type": chart.get("type"),
            "requested_layout": chart.get("layout"), "expected_series_count": len(fixture.get("series") or []),
            "predicted_series_count": len(plans), "series_count_match": len(plans) == len(fixture.get("series") or []),
            "identifier_contract_match": all(all(x["field_matches"].values()) for x in series_results),
            "numeric_execution": "not_executed",
            "numeric_reason": "Frozen prediction stage produced API plans only; no verified local table rows exist in the StatBridge package. Fixture values were not copied into predictions or represented as runtime output.",
        })
        for series in fixture.get("series") or []:
            points = series.get("points") or []
            periods = [str(x.get("period")) for x in points]
            if len(periods) != len(set(periods)):
                golden_issues.append({"id": row["id"], "type": "duplicate_fixture_period", "table_id": series.get("table_id")})

    # Dataset integrity checks that do not alter the source set.
    by_query: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_query[str(row["query"])].append(row)
        if not (row.get("source") or {}).get("url"):
            golden_issues.append({"id": row["id"], "type": "source_provenance_missing_url"})
    for query, duplicates in by_query.items():
        if len(duplicates) > 1:
            signatures = {json.dumps(x["expected"], sort_keys=True, ensure_ascii=False) for x in duplicates}
            golden_issues.append({"ids": [x["id"] for x in duplicates], "type": "duplicate_query", "conflicting_gold": len(signatures) > 1, "query": query})

    api_summary = {key: {"correct": api_field_hits[key], "total": api_field_totals[key], "accuracy": ratio(api_field_hits[key], api_field_totals[key])} for key in api_field_totals}
    scored_doc = {
        "blind_prediction_sha256": hashlib.sha256((project_root / "final_golden_predictions.jsonl").read_bytes()).hexdigest(),
        "metrics": final_metrics, "cases": scored, "golden_data_issues": golden_issues,
    }
    (project_root / "final_golden_scored_results.json").write_text(json.dumps(scored_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (project_root / "final_golden_failures.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in failures), encoding="utf-8", newline="\n")
    (project_root / "final_golden_retrieval_ablation.json").write_text(json.dumps(ablation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (project_root / "final_golden_api_plan_results.json").write_text(json.dumps({"summary": api_summary, "cases": api_results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    chart_summary = {
        "case_count": len(chart_results),
        "series_count_accuracy": ratio(sum(x["series_count_match"] for x in chart_results), len(chart_results)),
        "identifier_contract_accuracy": ratio(sum(x["identifier_contract_match"] for x in chart_results), len(chart_results)),
        "numeric_value_accuracy": None,
        "chart_data_accuracy": None,
        "limitation": "Numeric/chart row execution was not performed; null is reported instead of treating fixture presence as a passing runtime result.",
    }
    (project_root / "final_golden_chart_results.json").write_text(json.dumps({"summary": chart_summary, "cases": chart_results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    failure_counts = collections.Counter(x["failure_layer"] for x in failures)
    report = f"""# StatBridge Final Golden Set Blind Baseline Report

## 평가 무결성

- 현재 프로젝트는 Git 저장소가 아니므로 commit hash는 `null`이다. 파일 해시로 동결 상태를 기록했다.
- Golden Set hash: `88ad686f2c80026272d578f74ebde3689d8e9134b652f9e564809d8a2ebd7272`
- 총 {len(rows)}건을 query-only 입력으로 1회 실행했다. 정답은 예측 파일 생성 및 SHA-256 고정 후에만 scorer가 읽었다.
- 이번 결과는 production 코드 수정 전 baseline이다. 평가 중 production/runtime 수정은 0건이다.
- 기존 v4.1 regression: public 120/120, 과거 private 30/30 final regression 통과(기존 고정 산출물 확인).

## 데이터셋

- category: {json.dumps(dict(collections.Counter(str(x['category']) for x in rows)), ensure_ascii=False)}
- split: {json.dumps(dict(collections.Counter(str(x['split']) for x in rows)), ensure_ascii=False)}
- chart/numeric fixture: {len(chart_results)}건

## Retrieval

| Mode | R@1 | R@3 | R@5 | MRR | Latency |
|---|---:|---:|---:|---:|---:|
| Rule only | {rule_metrics['recall_at_1']} | {rule_metrics['recall_at_3']} | {rule_metrics['recall_at_5']} | {rule_metrics['mrr']} | {rule_metrics['mean_latency_ms']} ms |
| Rule + Embedding | N/A | N/A | N/A | N/A | N/A |
| Rule + Embedding + Reranker | {hybrid_metrics['recall_at_1']} | {hybrid_metrics['recall_at_3']} | {hybrid_metrics['recall_at_5']} | {hybrid_metrics['mrr']} | {hybrid_metrics['mean_agent_total_latency_ms']} ms |

`Rule + Embedding` 단독 mode는 production에 리랭커만 끄는 공식 경로와 고정 fusion식이 없어 임의 가중치를 만들지 않았다. Full hybrid는 실제 HCX 분류, Embedding, Reranker 및 3개 vector collection이 설정된 상태로 실행했으며 케이스별 실제 execution path는 ablation JSON에 기록했다.

## Agent 최종 결과

- status accuracy: {final_metrics['status_accuracy']}
- table-set/decision exact: {final_metrics['table_set_or_decision_exact']}
- single exact: {final_metrics['single_exact']} ({single_count}건)
- multi exact: {final_metrics['multi_exact']} ({multi_count}건)
- multi precision / recall / F1: {final_metrics['multi_precision']} / {final_metrics['multi_recall']} / {final_metrics['multi_f1']}
- clarify accuracy: {final_metrics['clarify_accuracy']} ({clarify_count}건)
- no_match accuracy: {final_metrics['no_match_accuracy']} ({no_match_count}건)
- category별: `{json.dumps(category_metrics, ensure_ascii=False)}`

## API plan

- 필드별 정확도: `{json.dumps(api_summary, ensure_ascii=False)}`
- table_id가 맞더라도 item/frequency/period/classification은 각각 독립 채점했다.

## Numeric 및 chart fixture

- fixture case: {len(chart_results)}건
- series-count accuracy: {chart_summary['series_count_accuracy']}
- identifier contract accuracy: {chart_summary['identifier_contract_accuracy']}
- numeric value accuracy: N/A
- chart data accuracy: N/A
- 이유: 패키지에 검증된 local table row가 없고 prediction 단계는 API plan까지만 생성했다. Golden fixture 수치를 runtime 출력으로 복사해 성공 처리하지 않았다. 따라서 실제 MCP/KOSIS row 비교가 없는 값은 정직하게 `null`로 남겼다.

## Latency

- Rule-only mean: {rule_metrics['mean_latency_ms']} ms
- Actual Agent full-path mean: {hybrid_metrics['mean_agent_total_latency_ms']} ms

## 실패 분석

- 실패 case: {len(failures)}건
- 실패 유형: `{json.dumps(dict(failure_counts), ensure_ascii=False)}`
- Golden 자체 오류/주의 후보: {len(golden_issues)}건. duplicate query는 데이터 품질 신호로 별도 기록했으며 자동 수정하지 않았다.

## 결론 및 제한

- Production 코드 수정: 없음.
- Golden 정답 leakage: prediction 생성 단계 없음. query-only 파일에는 id/query/split/category/tags만 있었고 prediction/trace에 gold 필드를 포함하지 않았다.
- 점수가 낮은 실패도 제거하거나 재실행해 선택하지 않았다.
- 이 baseline 결과를 바탕으로 production 패치를 자동 시작하지 않는다.
"""
    (project_root / "FINAL_GOLDEN_BLIND_BASELINE_REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    print(json.dumps({"final_metrics": final_metrics, "retrieval": ablation["modes"], "api": api_summary, "chart": chart_summary, "failures": len(failures), "golden_issues": len(golden_issues)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
