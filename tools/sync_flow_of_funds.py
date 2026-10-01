"""Refresh the two newly available tables from KOSIS, without inventing codes.

Run with .venv_runtime/Scripts/python.exe tools/sync_flow_of_funds.py.
This is an idempotent metadata migration. Original files are copied to a dated
backup before replacing rows. Numeric observations are verified, not persisted.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data/statbridge_mcp_server"))
from statbridge_mcp.kosis_client import KosisClient, META_URL

DICTIONARY = ROOT / "src/agent/stat_dictionary/stat_language_dictionary.json"
DATA = ROOT / "data/runtime_data/processed"
TABLES = {"DT_284Y001": "잔액", "DT_284Y002": "거래"}
VALUE_ALIASES = {
    "합계": ["전체", "총합", "총계"], "국내": ["국내 전체", "국내경제"],
    "국외": ["해외", "외국"], "한국은행": ["중앙은행", "한은"],
    "일반정부": ["정부"], "가계 및 비영리단체": ["가계와 비영리단체"],
    "비금융법인": ["비금융기업"], "금융법인": ["금융기관"],
    "현금 발행 및 예수금 합계": ["현금 발행과 예수금 합계"],
    "한국은행 현금 발행액 및 예수금": ["한은 현금 발행액 및 예수금"],
}


def replace_csv(path, table_id, new_rows):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    def tid(row):
        return row.get("TBL_ID") or row.get("TBL_ID_BASE")
    rows = [r for r in rows if tid(r) != table_id] + new_rows
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    client = KosisClient()
    client.session.trust_env = False
    dictionary = json.loads(DICTIONARY.read_text(encoding="utf-8"))
    backup = ROOT / "evaluation_runs" / ("349_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True)
    shutil.copy2(DICTIONARY, backup / DICTIONARY.name)
    extension = DICTIONARY.with_name("clarification_extensions.json")
    shutil.copy2(extension, backup / extension.name)
    for path in DATA.glob("*.csv"):
        shutil.copy2(path, backup / path.name)
    evidence = []
    # Fetch and validate both tables before any metadata file is replaced.
    fetched = {}
    for tid, basis in TABLES.items():
        rows = client._get(META_URL, {"method": "getMeta", "type": "ITM", "apiKey": client.api_key,
                                    "orgId": "301", "tblId": tid, "format": "json", "jsonVD": "Y", "detail": "Y"})
        assert isinstance(rows, list) and rows
        items = [r for r in rows if r.get("OBJ_ID") == "ITEM"]
        classes = [r for r in rows if r.get("OBJ_ID_SN")]
        prd = client.get_prd_meta("301", tid)
        years = sorted(str(r["PRD_DE"]) for r in prd if r.get("PRD_DE"))
        assert len(items) == 1 and years and len(classes) == 64
        numeric = client.get_statistics("301", tid, items[0]["ITM_ID"], "Y", years[0], years[-1],
                                        {"objL1": "ALL", "objL2": "ALL"})
        assert numeric and all(str(r.get("DT", "")).strip() for r in numeric)
        fetched[tid] = (items, classes, years, numeric)
    for tid, basis in TABLES.items():
        items, classes, years, numeric = fetched[tid]
        groups = defaultdict(list)
        for r in classes:
            groups[int(r["OBJ_ID_SN"])].append(r)
        dimensions = []
        for level, group in sorted(groups.items()):
            # Totals are the deterministic chart default, not the first leaf.
            observed_order = list(dict.fromkeys(r[f"C{level}"] for r in numeric))
            group.sort(key=lambda r: observed_order.index(r["ITM_ID"]))
            values = []
            for r in group:
                name = r["ITM_NM"]
                aliases = list(dict.fromkeys([name, *VALUE_ALIASES.get(name, [])]))
                values.append({"value_id": r["ITM_ID"], "value_name": name, "normalized": name,
                               "aliases": aliases, "natural_language_terms": aliases, "layman_terms": [],
                               "related_concepts": [], "negative_terms": [], "value_question_terms": []})
            dimensions.append({"level": level, "id_column": f"C{level}", "name_column": f"C{level}_NM",
                               "api_param": f"objL{level}", "object_id": group[0]["OBJ_ID"],
                               "object_name": group[0]["OBJ_NM"], "dimension_name": group[0]["OBJ_NM"], "values": values})
        table = {"table_id": tid, "table_name": items[0]["ITM_NM"], "domains": ["flow_of_funds"],
                 "org_id": "301", "prd_se": "A", "period_start_observed": years[0], "period_end_observed": years[-1],
                 "item_ids": [items[0]["ITM_ID"]], "item_names": [items[0]["ITM_NM"]],
                 "units": list(dict.fromkeys(r["UNIT_NM"] for r in numeric if r.get("UNIT_NM"))),
                 "dimensions": dimensions,
                 "aliases": [f"상세자금순환표 {basis}표", f"상세 자금순환표 {basis}표", f"상세자금순환 {basis}",
                             f"2008 SNA 상세자금순환 {basis}", f"상세자금순환표(2018~) {basis}표"],
                 "semantic_aliases": (["상세 자금순환 금융자산 부채 잔액", "상세자금순환 자산 부채 보유액"] if basis == "잔액"
                                      else ["상세 자금순환 금융거래", "상세자금순환 순거래", "상세자금순환 거래액"]),
                 "public_query_terms": [f"상세자금순환표 {basis} 추이", f"제도부문별 상세자금순환 {basis}"],
                 "keywords": ["상세자금순환표", "계정항목별", "제도부문별", basis],
                 "api_endpoint": "https://kosis.kr/openapi/Param/statisticsParameterData.do",
                 "api_call_params": {"method": "getList", "apiKey": "${KOSIS_API_KEY}", "format": "json", "jsonVD": "Y",
                                     "orgId": "301", "tblId": tid, "itmId": items[0]["ITM_ID"], "prdSe": "Y", "newEstPrdCnt": "1",
                                     **{d["api_param"]: "+".join(v["value_id"] for v in d["values"]) for d in dimensions}},
                 "source_kind": "KOSIS live ITM/PRD/data verified 2026-10-01; aliases curated; exact codes unchanged",
                 "clarification_tags": []}
        dictionary["tables"] = [t for t in dictionary["tables"] if t["table_id"] != tid] + [table]
        for r in items + classes:
            r.update(ORG_ID_BASE="301", TBL_ID_BASE=tid)
        replace_csv(DATA / "bok_items.csv", tid, items)
        replace_csv(DATA / "bok_classifications.csv", tid, classes)
        replace_csv(DATA / "bok_periods.csv", tid, [{"PRD_SE": "년", "STRT_PRD_DE": years[0], "END_PRD_DE": years[-1], "ORG_ID_BASE": "301", "TBL_ID_BASE": tid}])
        with (DATA / "bok_table_master.csv").open(encoding="utf-8-sig", newline="") as f:
            master = next(r for r in csv.DictReader(f) if r["TBL_ID"] == tid)
        master.update(START_PERIOD=years[0], END_PERIOD=years[-1], PERIOD_COUNT=str(len(years)), ITEM_COUNT=str(len(items)),
                      CLASS_ROW_COUNT=str(len(classes)), ITEM_NAMES=items[0]["ITM_NM"],
                      CLASS_NAMES=" || ".join(dict.fromkeys(r["OBJ_NM"] for r in classes)), UNITS=" || ".join(table["units"]))
        replace_csv(DATA / "bok_table_master.csv", tid, [master])
        evidence.append({"table_id": tid, "periods": years, "item_count": len(items),
                         "dimension_counts": [len(d["values"]) for d in dimensions], "numeric_rows": len(numeric)})
    dictionary["tables"].sort(key=lambda t: t["table_id"])
    assert len(dictionary["tables"]) == 349 and len({t["table_id"] for t in dictionary["tables"]}) == 349
    dictionary["metadata"].update(version="5.1", table_count=349,
                                  dimension_value_count=sum(len(d["values"]) for t in dictionary["tables"] for d in t["dimensions"]),
                                  availability_update="Two detailed flow-of-funds tables verified against live KOSIS on 2026-10-01")
    DICTIONARY.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ext = json.loads(extension.read_text(encoding="utf-8"))
    ext["catalog_only_tables"] = [t for t in ext.get("catalog_only_tables", []) if t["table_id"] not in TABLES]
    extension.write_text(json.dumps(ext, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "evaluation_runs/349_metadata_sync.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"table_count": 349, "backup": str(backup), "evidence": evidence}, ensure_ascii=True))


if __name__ == "__main__":
    main()
