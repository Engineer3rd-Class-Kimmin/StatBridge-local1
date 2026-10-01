from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

from statbridge_mcp.statistics_service import StatisticsService

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "validation_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT = OUT_DIR / "validation_checkpoint.json"
RESULT_JSON = OUT_DIR / "validation_results.json"
RESULT_CSV = OUT_DIR / "validation_results.csv"
SUMMARY_JSON = OUT_DIR / "validation_summary.json"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def save_state(results: list[dict[str, Any]], next_index: int) -> None:
    RESULT_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(RESULT_CSV, results)

    CHECKPOINT.write_text(
        json.dumps(
            {
                "next_index": next_index,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_existing() -> tuple[list[dict[str, Any]], int]:
    results: list[dict[str, Any]] = []
    next_index = 0

    if RESULT_JSON.exists():
        try:
            results = json.loads(RESULT_JSON.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 손상된 결과 파일은 빈 결과로 리셋
            results = []

    if CHECKPOINT.exists():
        try:
            cp = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            next_index = int(cp.get("next_index", len(results)))
        except Exception:  # noqa: BLE001 - 손상된 체크포인트는 처음부터 재개
            next_index = len(results)

    return results, next_index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="StatBridge 347개 KOSIS 통계표 전체 smoke test"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="시작 index. 생략하면 checkpoint에서 이어서 진행",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="테스트 개수. 0이면 끝까지",
    )
    parser.add_argument(
        "--prefer-local",
        action="store_true",
        help="로컬 CSV를 우선 사용. 기본은 KOSIS API 직접 검증",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="기존 checkpoint/results를 무시하고 0부터 새로 시작",
    )
    args = parser.parse_args()

    service = StatisticsService()
    metas = service.store.available_supported_tables()

    if args.fresh:
        results: list[dict[str, Any]] = []
        start_index = args.start or 0
    else:
        results, saved_index = load_existing()
        start_index = args.start if args.start is not None else saved_index

    end_index = len(metas)
    if args.limit and args.limit > 0:
        end_index = min(end_index, start_index + args.limit)

    print("=" * 90)
    print("StatBridge KOSIS 전체 검증")
    print(f"전체 지원 표: {len(metas)}")
    print(f"이번 실행: index {start_index} ~ {end_index - 1}")
    print(f"prefer_local: {args.prefer_local}")
    print(f"결과 폴더: {OUT_DIR}")
    print("=" * 90)

    started = time.time()

    for idx in range(start_index, end_index):
        meta = metas[idx]
        t0 = time.time()

        try:
            md = service.get_table_metadata(
                meta.table_id,
                live_period_fallback=True,
            )

            freqs = md.get("frequencies") or []
            freq = freqs[0] if freqs else "Y"

            periods_by_freq = md.get("periods_by_freq") or {}
            periods = periods_by_freq.get(freq) or []
            latest = periods[-1] if periods else md.get("end_period")

            if not latest:
                row = {
                    "index": idx,
                    "table_id": meta.table_id,
                    "table_name": meta.table_name,
                    "status": "failed",
                    "reason": "period_not_found",
                    "frequency": freq,
                    "period": "",
                    "attempt": "",
                    "row_count": 0,
                    "elapsed_sec": round(time.time() - t0, 2),
                }
            else:
                item_id = meta.items[0]["item_id"] if meta.items else "ALL"
                classes = service._first_real_classifications(meta.table_id)

                res = service.get_statistics(
                    table_id=meta.table_id,
                    item_id=item_id,
                    classifications=classes,
                    frequency=freq,
                    start_period=latest,
                    end_period=latest,
                    prefer_local=args.prefer_local,
                )

                ok = res.get("row_count", 0) > 0

                row = {
                    "index": idx,
                    "table_id": meta.table_id,
                    "table_name": meta.table_name,
                    "status": "success" if ok else "failed",
                    "reason": "" if ok else "no_rows",
                    "frequency": freq,
                    "period": latest,
                    "attempt": res.get("attempt", res.get("source", "")),
                    "row_count": res.get("row_count", 0),
                    "elapsed_sec": round(time.time() - t0, 2),
                    "errors": json.dumps(
                        res.get("errors", []),
                        ensure_ascii=False,
                    ) if not ok else "",
                }

        except Exception as e:  # noqa: BLE001 - 표 단위 실패를 행으로 기록해 전체 실행 유지
            row = {
                "index": idx,
                "table_id": meta.table_id,
                "table_name": meta.table_name,
                "status": "failed",
                "reason": str(e),
                "frequency": "",
                "period": "",
                "attempt": "",
                "row_count": 0,
                "elapsed_sec": round(time.time() - t0, 2),
            }

        # 이미 해당 index 결과가 있으면 교체, 아니면 append
        results = [r for r in results if int(r.get("index", -1)) != idx]
        results.append(row)
        results.sort(key=lambda x: int(x.get("index", 0)))

        save_state(results, idx + 1)

        mark = "OK" if row["status"] == "success" else "FAIL"
        print(
            f"[{idx + 1:03d}/{len(metas)}] {mark:<4} "
            f"{meta.table_id:<14} {meta.table_name[:35]:<35} "
            f"{row.get('frequency','')}/{row.get('period','')} "
            f"attempt={row.get('attempt','')} "
            f"rows={row.get('row_count',0)} "
            f"{row.get('elapsed_sec',0)}s"
        )

    tested = [r for r in results if int(r.get("index", -1)) < end_index]
    success_rows = [r for r in tested if r.get("status") == "success"]
    failed_rows = [r for r in tested if r.get("status") == "failed"]
    fallback_rows = [
        r for r in success_rows
        if r.get("attempt") not in ("requested", "local_csv", "")
    ]

    summary = {
        "total_available": len(metas),
        "tested": len(tested),
        "success": len(success_rows),
        "fallback_success": len(fallback_rows),
        "failed": len(failed_rows),
        "success_rate": round(
            len(success_rows) / len(tested) * 100, 2
        ) if tested else 0,
        "next_index": end_index,
        "finished_all": end_index >= len(metas),
        "elapsed_total_sec": round(time.time() - started, 2),
        "failed_tables": [
            {
                "table_id": r.get("table_id"),
                "table_name": r.get("table_name"),
                "reason": r.get("reason"),
                "errors": r.get("errors", ""),
            }
            for r in failed_rows
        ],
    }

    SUMMARY_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("=" * 90)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("=" * 90)


if __name__ == "__main__":
    main()
