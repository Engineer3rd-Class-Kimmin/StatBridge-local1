from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_CHART_TYPES = {"auto", "line", "bar", "area", "scatter"}
SUPPORTED_LAYOUTS = {"combined", "separate"}


@dataclass(slots=True)
class OutputRequest:
    """User-controlled presentation request consumed only by OutputAgent."""

    chart_type: str = "auto"
    layout: str = "combined"
    title: str | None = None
    show_legend: bool = True
    x_axis_label: str | None = None
    y_axis_label: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "OutputRequest":
        raw = value or {}
        chart_type = str(raw.get("chart_type") or "auto").lower()
        layout = str(raw.get("layout") or "combined").lower()
        return cls(
            chart_type=chart_type if chart_type in SUPPORTED_CHART_TYPES else "auto",
            layout=layout if layout in SUPPORTED_LAYOUTS else "combined",
            title=str(raw.get("title") or "").strip() or None,
            show_legend=bool(raw.get("show_legend", True)),
            x_axis_label=str(raw.get("x_axis_label") or "").strip() or None,
            y_axis_label=str(raw.get("y_axis_label") or "").strip() or None,
        )


class OutputAgent:
    """Turn MCP rows into an editable, renderer-independent output specification.

    This skeleton deliberately does not draw pixels. It owns chart selection,
    data shaping and edit options; the frontend only renders the returned spec.
    """

    palette = ["#4568ff", "#ff805e", "#24a47c", "#9a62df", "#e3a52b", "#2aa7c9"]

    @staticmethod
    def _series_key(row: dict[str, Any]) -> tuple[str, str]:
        explicit = str(row.get("_SERIES_LABEL") or "").strip()
        if explicit:
            return explicit, str(row.get("UNIT_NM") or "")
        labels = [str(row.get("ITM_NM") or "").strip()]
        for index in range(1, 9):
            label = str(row.get(f"C{index}_NM") or "").strip()
            if label and label not in {"전체", "계", "합계"}:
                labels.append(label)
        return " · ".join(x for x in labels if x) or "통계값", str(row.get("UNIT_NM") or "")

    def _series(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                value = float(str(row.get("DT", "")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            period = str(row.get("PRD_DE") or "").strip()
            if period:
                grouped.setdefault(self._series_key(row), []).append({"date": period, "value": value})
        result = []
        for index, ((label, unit), points) in enumerate(grouped.items()):
            unique = {point["date"]: point for point in points}
            result.append({
                "id": f"series-{index + 1}",
                "label": label,
                "unit": unit,
                "color": self.palette[index % len(self.palette)],
                "points": [unique[key] for key in sorted(unique)],
            })
        return result

    @staticmethod
    def _select_chart_type(requested: str, series: list[dict[str, Any]]) -> str:
        if requested != "auto":
            return requested
        point_count = max((len(item.get("points") or []) for item in series), default=0)
        return "bar" if point_count <= 6 else "line"

    @staticmethod
    def _summary(series: list[dict[str, Any]]) -> str:
        if not series:
            return "선택한 기간에 그래프로 표시할 수치 데이터가 없습니다."
        item = series[0]
        points = item.get("points") or []
        if not points:
            return "그래프를 생성했습니다."
        first, last = points[0], points[-1]
        delta = last["value"] - first["value"]
        direction = "증가" if delta > 0 else "감소" if delta < 0 else "같은 수준을 유지"
        extra = f" 함께 표시된 계열은 총 {len(series)}개입니다." if len(series) > 1 else ""
        label = str(item.get("label") or "통계값")
        last_char = label[-1]
        has_batchim = "가" <= last_char <= "힣" and (ord(last_char) - ord("가")) % 28 != 0
        subject = label + ("은" if has_batchim else "는")
        return (
            f"{subject} {first['date']} {first['value']:,.1f}에서 "
            f"{last['date']} {last['value']:,.1f}{item.get('unit') or ''}로 {direction}했습니다.{extra}"
        )

    def prepare(self, result: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
        output_request = OutputRequest.from_dict(request)
        execution = result.get("execution") or {}
        series = self._series(execution.get("rows") or [])
        chart_type = self._select_chart_type(output_request.chart_type, series)
        return {
            "status": "ready" if series else "empty",
            "agent": "output-agent-skeleton-v1",
            "summary": self._summary(series),
            "visualization": {
                "chartType": chart_type,
                "layout": output_request.layout,
                "series": series,
                "editable": True,
                "editOptions": {
                    "title": output_request.title,
                    "showLegend": output_request.show_legend,
                    "xAxisLabel": output_request.x_axis_label,
                    "yAxisLabel": output_request.y_axis_label,
                    "supportedChartTypes": sorted(SUPPORTED_CHART_TYPES - {"auto"}),
                    "supportedLayouts": sorted(SUPPORTED_LAYOUTS),
                },
            },
        }

    def inspect(self, result: dict[str, Any]) -> dict[str, Any]:
        """Describe available output choices without producing a render spec."""
        series = self._series((result.get("execution") or {}).get("rows") or [])
        return {
            "seriesCount": len(series),
            "pointCount": sum(len(item.get("points") or []) for item in series),
            "recommendedChartType": self._select_chart_type("auto", series),
            "supportedChartTypes": sorted(SUPPORTED_CHART_TYPES - {"auto"}),
            "supportedLayouts": sorted(SUPPORTED_LAYOUTS),
            "editableFields": ["title", "show_legend", "x_axis_label", "y_axis_label"],
        }
