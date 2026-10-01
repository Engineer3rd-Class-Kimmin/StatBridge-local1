"""Exercise the running UI-facing API and output-agent boundary over HTTP."""
from __future__ import annotations
import json
import os
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[1]
(ROOT / "evaluation_runs").mkdir(parents=True, exist_ok=True)
base = os.getenv("STATBRIDGE_BASE_URL", "http://127.0.0.1:8000")
s = requests.Session(); s.trust_env = False
health = s.get(base + "/api/health", timeout=15).json()
catalog = s.get(base + "/api/catalog", timeout=15).json()
assert catalog["total"] == 349
cards = {t["tableId"]: t for major in catalog["categories"] for middle in major["children"] for t in middle["children"]}
results = []
for tid in ("DT_284Y001", "DT_284Y002", "DT_404Y017", "DT_121Y007"):
    card = cards[tid]
    choices = {d["apiParam"]: d["defaultValueId"] for d in card["dimensions"]}
    if tid in ("DT_284Y001", "DT_284Y002"):
        # A non-default institution must survive the user's period selection.
        institution = card["dimensions"][1]
        choices[institution["apiParam"]] = next(v["id"] for v in institution["valueOptions"] if v["name"] == "국내")
    response = s.post(base + "/api/query", json={"query": tid, "dimension_values": choices}, timeout=30)
    response.raise_for_status(); discovery = response.json()
    assert discovery["status"] == "need_period", discovery
    assert discovery["state"]["_dimension_values"] == choices
    period = discovery["availablePeriod"]
    response = s.post(base + "/api/query", json={"query": tid, "state": discovery["state"], "period_start": period["max"][:4] + "-01-01",
                                                "period_end": period["max"], "execute": True}, timeout=45)
    response.raise_for_status(); prepared = response.json()
    assert prepared["status"] == "need_output_config", prepared
    assert prepared["outputOptions"]["pointCount"] > 0
    response = s.post(base + "/api/output", json={"session_ids": [prepared["outputSessionId"]], "chart_type": "bar", "chart_mode": "combined",
                                                 "title": tid + " 검증", "show_legend": True}, timeout=30)
    response.raise_for_status(); rendered = response.json()
    assert rendered["status"] == "resolved" and rendered["chart"]
    points = sum(len(series["points"]) for series in rendered["chart"])
    assert points > 0
    result = {"table_id": tid, "discovery": discovery["status"], "prepared": prepared["status"], "output": rendered["status"],
              "chart_type": rendered["chartType"], "point_count": points, "chosen_codes": choices,
              "period": rendered["period"]}
    results.append(result); print(json.dumps(result, ensure_ascii=True), flush=True)
(ROOT / "evaluation_runs/349_http_verification.json").write_text(json.dumps({"health": health, "catalog_count": 349, "tables": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print("HTTP query -> period -> KOSIS -> output agent: PASS")
