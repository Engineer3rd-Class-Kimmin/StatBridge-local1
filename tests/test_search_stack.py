from pathlib import Path

from statbridge_mcp.config import settings
from statbridge_mcp.metadata_store import MetadataStore
from statbridge_mcp.search_engine import SearchEngine
from statbridge_mcp.server import healthcheck
from statbridge_mcp.statistics_service import StatisticsService


def test_catalog_search_works_without_kosis_key(monkeypatch):
    monkeypatch.setattr(settings, "kosis_api_key", "")
    store = MetadataStore()
    service = StatisticsService(store=store)

    assert len(store.table_ids()) == 349
    assert len(store.available_supported_tables()) == 349
    result = service.search_tables("경제심리지수", top_k=5)
    assert result[0]["table_id"] == "DT_513Y001"
    assert result[0]["local_csv_available"] == bool(list(settings.tables_dir.glob("DT_513Y001*.csv")))


def test_search_handles_colloquial_multi_and_catalog_only_queries():
    search = SearchEngine(MetadataStore()).search
    assert "DT_513Y001" in [row["table_id"] for row in search("요즘 경기가 체감상 어떤지 경제심리 지표로 보여줘")]
    assert {"DT_121Y006", "DT_121Y002"} <= {
        row["table_id"] for row in search("예금은행 신규취급액 기준 대출금리와 수신금리를 비교해줘")
    }
    assert "DT_284Y001" in [row["table_id"] for row in search("상세자금순환표 잔액표", top_k=10)]
    assert search("서울 강남구 오늘 미세먼지 농도 알려줘") == []


def test_mcp_health_reports_catalog_and_configured_data_path():
    status = healthcheck()
    assert status["status"] == "ok"
    assert status["catalog_table_count"] == 349
    assert status["supported_table_count"] == 349
    assert Path(status["tables_dir"]) == settings.tables_dir
