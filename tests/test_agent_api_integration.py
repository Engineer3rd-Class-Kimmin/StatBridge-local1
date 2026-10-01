"""The imported Agent API is the UI-facing application."""

import json
from pathlib import Path


def test_agent_api_exposes_query_catalog_and_health():
    from bridge_api import app

    paths = {route.path for route in app.routes}
    assert {"/api/query", "/api/catalog", "/api/health"} <= paths


def test_dictionary_contains_supported_table_catalog():
    dictionary = Path(__file__).resolve().parents[1] / "src/agent/stat_dictionary/stat_language_dictionary.json"
    tables = json.loads(dictionary.read_text(encoding="utf-8"))["tables"]
    assert len(tables) == 349
    assert len({table["table_id"] for table in tables}) == 349


def test_catalog_only_table_uses_ui_table_shape(monkeypatch):
    import bridge_api

    monkeypatch.setattr(bridge_api.agent, "run", lambda **_: {
        "status": "catalog_only",
        "selected_table": {"table_id": "DT_284Y001", "table_name": "상세자금순환표"},
    })
    response = bridge_api.query(bridge_api.QueryRequest(query="상세자금순환표"))

    assert response["status"] == "catalog_only"
    assert set(response["tables"][0]) == {"tableId", "name", "source", "item", "unit"}
    assert response["tables"][0]["tableId"] == "DT_284Y001"


def test_execution_failure_is_not_reported_as_resolved(monkeypatch):
    import bridge_api

    plan = {
        "table_id": "DT_513Y001", "table_name": "경제심리지수",
        "frequency": "M", "start_period": "202001", "end_period": "202512",
    }
    resolution = {
        "status": "resolved", "api_plan": plan,
        "selected_table": {"table_id": "DT_513Y001"},
    }
    monkeypatch.setattr(bridge_api.agent, "run", lambda **_: resolution)
    monkeypatch.setattr(bridge_api.agent, "execute_resolution", lambda **_: {
        **resolution, "execution": {"status": "error", "error": "조회 실패"},
    })
    response = bridge_api.query(bridge_api.QueryRequest(
        query="경제심리지수", period_start="2021-01-01", period_end="2021-12-31",
    ))

    assert response["status"] == "data_unavailable"
    assert response["chart"] == []
    assert response["warnings"] == ["조회 실패"]
    assert response["lineage"][-1]["status"] == "active"
