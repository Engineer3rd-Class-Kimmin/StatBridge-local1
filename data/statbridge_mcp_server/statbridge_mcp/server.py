from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server import MCPServer

from .config import settings
from .statistics_service import StatisticsService

mcp = MCPServer("StatBridge-MCP")


@lru_cache(maxsize=1)
def get_service() -> StatisticsService:
    return StatisticsService()


@mcp.tool()
def healthcheck() -> dict[str, Any]:
    """StatBridge MCP 서버 상태와 데이터 경로를 확인한다."""
    service = get_service()
    return {
        "status": "ok",
        "sdk": "mcp-2.x",
        "supported_table_count": len(service.store.available_supported_tables()),
        "data_dir": str(service.store.data_dir),
        "tables_dir": str(service.tables_dir),
        "kosis_api_key_configured": bool(settings.kosis_api_key),
    }


@mcp.tool()
def search_tables(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """자연어 또는 키워드와 관련된 한국은행 KOSIS 통계표 후보를 검색한다."""
    return get_service().search_tables(query=query, top_k=top_k)


@mcp.tool()
def get_table_metadata(
    table_id: str,
    live_period_fallback: bool = True,
) -> dict[str, Any]:
    """통계표의 항목, 분류, 주기, 기간, 주석 등 메타데이터를 조회한다."""
    return get_service().get_table_metadata(
        table_id=table_id,
        live_period_fallback=live_period_fallback,
    )


@mcp.tool()
def get_statistics(
    table_id: str,
    item_id: str = "ALL",
    classifications: dict[str, str] | None = None,
    frequency: str | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
    prefer_local: bool = True,
) -> dict[str, Any]:
    """실제 통계 데이터를 조회한다. 로컬 CSV를 우선하고 KOSIS OpenAPI로 fallback한다."""
    return get_service().get_statistics(
        table_id=table_id,
        item_id=item_id,
        classifications=classifications,
        frequency=frequency,
        start_period=start_period,
        end_period=end_period,
        prefer_local=prefer_local,
    )


@mcp.tool()
def validate_tables_batch(
    offset: int = 0,
    limit: int = 20,
    prefer_local: bool = False,
    include_success_results: bool = True,
) -> dict[str, Any]:
    """347개 표를 offset/limit 단위로 나눠 smoke test한다. Inspector timeout 방지를 위해 이 도구 사용을 권장한다."""
    return get_service().validate_tables_batch(
        offset=offset,
        limit=limit,
        prefer_local=prefer_local,
        include_success_results=include_success_results,
    )


@mcp.tool()
def validate_all_tables(
    limit: int = 0,
    prefer_local: bool = False,
) -> dict[str, Any]:
    """호환용 전체 검증 도구. Inspector에서는 긴 호출로 timeout이 날 수 있으므로 batch 도구를 권장한다."""
    return get_service().validate_all_tables(
        limit=limit,
        prefer_local=prefer_local,
    )


if __name__ == "__main__":
    mcp.run()
