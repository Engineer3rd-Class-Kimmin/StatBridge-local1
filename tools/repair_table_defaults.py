"""Repair only live-check failures using current KOSIS metadata/data.

Preserve existing synonyms and historical codes. Add authoritative missing
codes, and record a representative combination known to have numeric data.
"""
from __future__ import annotations
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "data/statbridge_mcp_server"), str(ROOT / "tools")]
from statbridge_mcp.kosis_client import KosisClient, META_URL
from sync_flow_of_funds import replace_csv, DATA, DICTIONARY


def main():
    report = json.loads((ROOT / "evaluation_runs/349_live_verification.json").read_text(encoding="utf-8"))
    failed = [r for r in report["tables"] if r["status"] != "ok"]
    dictionary = json.loads(DICTIONARY.read_text(encoding="utf-8"))
    tables = {t["table_id"]: t for t in dictionary["tables"]}
    backup = ROOT / "evaluation_runs" / ("349_defaults_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True)
    shutil.copy2(DICTIONARY, backup / DICTIONARY.name)
    for path in DATA.glob("*.csv"):
        shutil.copy2(path, backup / path.name)
    client = KosisClient(); client.session.trust_env = False
    repairs = []
    for failure in failed:
        tid = failure["table_id"]; table = tables[tid]
        live = client._get(META_URL, {"method": "getMeta", "type": "ITM", "apiKey": client.api_key,
                                    "orgId": table["org_id"], "tblId": tid, "format": "json", "jsonVD": "Y", "detail": "Y"})
        items = [r for r in live if r.get("OBJ_ID") == "ITEM"]
        classes = [r for r in live if r.get("OBJ_ID_SN")]
        groups = defaultdict(list)
        for r in classes: groups[int(r["OBJ_ID_SN"])].append(r)
        assert items and groups
        rows = client.get_statistics(table["org_id"], tid, items[0]["ITM_ID"], failure["frequency"],
                                     failure["period"], failure["period"], {f"objL{k}": "ALL" for k in groups})
        def is_numeric(r):
            try: float(str(r.get("DT", "")).replace(",", "")); return True
            except ValueError: return False
        row = next(r for r in rows if is_numeric(r))
        for level, group in groups.items():
            names = {r.get(f"C{level}"): r.get(f"C{level}_NM") for r in rows if r.get(f"C{level}_NM")}
            for r in group:
                r.setdefault("ITM_NM", names.get(r["ITM_ID"]) or r["ITM_ID"])
                r.setdefault("OBJ_NM", row.get(f"C{level}_OBJ_NM") or f"분류 {level}")
        table["item_ids"] = [r["ITM_ID"] for r in items]
        table["item_names"] = [r["ITM_NM"] for r in items]
        table["api_call_params"]["itmId"] = items[0]["ITM_ID"]
        for level, group in sorted(groups.items()):
            param = f"objL{level}"
            dimension = next((d for d in table["dimensions"] if d["api_param"] == param), None)
            if dimension is None:
                dimension = {"level": level, "id_column": f"C{level}", "name_column": f"C{level}_NM",
                             "api_param": param, "values": []}
                table["dimensions"].append(dimension)
            dimension_name = group[0].get("OBJ_NM") or row.get(f"C{level}_OBJ_NM") or f"분류 {level}"
            dimension.update(object_id=group[0].get("OBJ_ID", ""), object_name=dimension_name, dimension_name=dimension_name)
            existing = {v["value_id"] for v in dimension["values"]}
            for r in group:
                if r["ITM_ID"] not in existing:
                    dimension["values"].append({"value_id": r["ITM_ID"], "value_name": r["ITM_NM"], "normalized": r["ITM_NM"],
                                                 "aliases": [r["ITM_NM"]], "natural_language_terms": [r["ITM_NM"]],
                                                 "layman_terms": [], "related_concepts": [], "negative_terms": [], "value_question_terms": []})
            representative = row[f"C{level}"]
            assert representative in {v["value_id"] for v in dimension["values"]}
            dimension["representative_value_id"] = representative
            dimension["representative_verified_period"] = failure["period"]
            table["api_call_params"][param] = "+".join(v["value_id"] for v in dimension["values"])
        table["dimensions"].sort(key=lambda d: int(d["level"]))
        for r in items + classes: r.update(ORG_ID_BASE=table["org_id"], TBL_ID_BASE=tid)
        replace_csv(DATA / "bok_items.csv", tid, items)
        replace_csv(DATA / "bok_classifications.csv", tid, classes)
        repair = {"table_id": tid, "reason": failure, "representative_values": {d["api_param"]: d.get("representative_value_id") for d in table["dimensions"]},
                  "verified_period": row["PRD_DE"], "returned_rows": len(rows)}
        repairs.append(repair)
        print(json.dumps(repair, ensure_ascii=True), flush=True)
    dictionary["metadata"]["dimension_value_count"] = sum(len(d["values"]) for t in dictionary["tables"] for d in t["dimensions"])
    DICTIONARY.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "evaluation_runs/349_default_repairs.json").write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__": main()
