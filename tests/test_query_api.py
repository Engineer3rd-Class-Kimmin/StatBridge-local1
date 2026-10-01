import pytest
from fastapi import HTTPException

from bridge_api import QueryRequest, catalog, health, query


def test_previous_api_entrypoint_uses_agent_application():
    from bridge_api import app
    from query_api import app as entrypoint

    assert entrypoint is app


def test_agent_query_uses_dictionary_without_network_keys(monkeypatch):
    from bridge_api import agent
    from statbridge_mcp.config import settings

    monkeypatch.setattr(settings, "kosis_api_key", "")
    monkeypatch.setattr(agent.ncp.settings, "api_key", "")
    response = query(QueryRequest(query="경제심리지수 추이", execute=False))
    assert response["status"] == "need_period"
    assert response["chart"] == []
    assert response["tables"]


def test_empty_query_is_rejected():
    with pytest.raises(HTTPException) as error:
        query(QueryRequest(query="  "))
    assert error.value.status_code == 400


def test_catalog_and_health_show_supported_tables():
    assert catalog()["total"] == 349
    assert health()["dictionary_tables"] == 349
