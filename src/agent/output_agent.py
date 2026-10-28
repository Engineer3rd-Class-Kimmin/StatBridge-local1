from __future__ import annotations
from statbridge_mcp.observability import observed

from dataclasses import dataclass
from typing import Any

import json
import math

from output_schema import ChartEditCommand, ChartSpec, Highlight, Annotation
from plotly_renderer import render_plotly
from chart_availability import inspect_charts, combined_layout_reason, comparable_axis_units
from report_table import build_table


SUPPORTED_CHART_TYPES = {"auto", "line", "bar", "stacked_bar", "area", "scatter", "bubble", "pie", "donut", "histogram", "box", "heatmap", "treemap", "waterfall"}
SUPPORTED_LAYOUTS = {"combined", "separate", "horizontal", "grid"}


def edit_change_description(command: ChartEditCommand) -> str:
    from edit_description import describe_edit
    return describe_edit(command)


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

    palette = ["#4568ff", "#ff805e", "#24a47c", "#9a62df", "#e3a52b", "#2aa7c9",
               "#8c564b", "#d857a8", "#607d8b", "#a3a527", "#5b8c5a", "#2d4472"]

    def __init__(self, ncp_client: Any | None = None) -> None:
        self.ncp_client = ncp_client

    @staticmethod
    def _series_key(row: dict[str, Any]) -> tuple[str, str, str]:
        source_id = str(row.get("_SOURCE_SERIES_ID") or "")
        explicit = str(row.get("_SERIES_LABEL") or "").strip()
        dimensions = [str(row.get(f"C{index}_NM") or "").strip() for index in range(1, 9)]
        dimensions = [name for name in dimensions if name and name not in {"전체", "계", "합계"}]
        if explicit:
            label = " · ".join([explicit, *(name for name in dimensions if name not in explicit)])
            return source_id, label, str(row.get("UNIT_NM") or "")
        labels = [str(row.get("ITM_NM") or "").strip()]
        labels.extend(dimensions)
        return source_id, " · ".join(x for x in labels if x) or "통계값", str(row.get("UNIT_NM") or "")

    def _series(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Display names are not identities: different classification codes may
        # legitimately have the same label (or arrive without dimension names).
        identities = {}
        row_identities = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = (str(row.get('TBL_ID') or row.get('_SOURCE_SERIES_ID') or ''),
                        str(row.get('ITM_ID') or ''),
                        tuple((f'C{i}', str(row.get(f'C{i}') or '')) for i in range(1, 9)))
            row_identities[id(row)] = identity
            identities.setdefault(self._series_key(row), set()).add(identity)
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        frequencies: dict[tuple[str, str, str], str] = {}
        calculations: dict[tuple[str,str,str],str] = {}
        source_identities = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                value = float(str(row.get("DT", "")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            period = str(row.get("PRD_DE") or "").strip()
            if period:
                key = self._series_key(row)
                if len(identities[key]) > 1:
                    identity = row_identities[id(row)]
                    suffix = ' · '.join([identity[1], *(v for _, v in identity[2] if v)])
                    key = (key[0], key[1] + ' [' + suffix + ']', key[2])
                grouped.setdefault(key, []).append({"date": period, "value": value})
                if (row.get('TBL_ID') and row.get('ITM_ID')
                        and not row.get('_DERIVED_CALCULATION')
                        and str(row.get('_SOURCE_SERIES_ID') or row['TBL_ID']) == str(row['TBL_ID'])):
                    source_identities[key] = {
                        'provider': 'kosis', 'table_id': str(row['TBL_ID']),
                        'item_id': str(row['ITM_ID']),
                        'classifications': {f'objL{i}': str(row[f'C{i}']) for i in range(1, 9) if row.get(f'C{i}')},
                        'frequency': str(row.get('_FREQUENCY') or row.get('PRD_SE') or ''),
                    }
                frequencies[key] = str(row.get("_FREQUENCY") or "")
                if row.get("_DERIVED_CALCULATION"):calculations[key]=row["_DERIVED_CALCULATION"]
        result = []
        for index, ((source_id, label, unit), points) in enumerate(grouped.items()):
            unique: dict[str, dict[str, Any]] = {}
            for point in points:
                previous = unique.get(point["date"])
                if previous is not None and previous["value"] != point["value"]:
                    raise ValueError(f"{label}의 {point['date']} 값이 여러 개라 계열을 안전하게 합칠 수 없습니다.")
                unique[point["date"]] = point
            result.append({
                "id": f"series-{index + 1}",
                **({"calculation":calculations[(source_id,label,unit)]} if (source_id,label,unit) in calculations else {}),
                "label": label,
                "unit": unit,
                "sourceId": source_id,
                **({'sourceIdentity': source_identities[(source_id,label,unit)]} if (source_id,label,unit) in source_identities else {}),
                "frequency": frequencies[(source_id, label, unit)],
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
        from report_summary import explain
        return explain(series)["text"]

    @staticmethod
    def _table(series: list[dict[str, Any]]) -> dict[str, Any]:
        """Expose the plotted values as a table without changing their meaning."""
        return {
            "columns": ["series", "period", "value", "unit"],
            "rows": [
                {
                    "series": item["label"],
                    "period": point["date"],
                    "value": point["value"],
                    "unit": item["unit"],
                }
                for item in series
                for point in item["points"]
            ],
        }

    @staticmethod
    def _evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
        """Report only source identifiers and query bounds supplied by execution."""
        execution = result.get("execution") or {}
        plans = result.get("api_plans") or [result.get("api_plan")]
        sources = execution.get("sources") or []
        evidence = []
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            table_id = str(plan.get("table_id") or "")
            source = next((item for item in sources if isinstance(item, dict) and str(item.get("table_id") or "") == table_id), {})
            evidence.append({
                "tableId": table_id,
                "tableName": str(plan.get("table_name") or ""),
                "organizationId": str(plan.get("org_id") or ""),
                "itemId": str(plan.get("item_id") or ""),
                "frequency": str(plan.get("frequency") or ""),
                "requestedPeriod": {
                    "start": str(plan.get("start_period") or ""),
                    "end": str(plan.get("end_period") or ""),
                },
                "rowCount": source.get("row_count"),
                "source": str(source.get("source") or ""),
            })
        return evidence

    @staticmethod
    def _model_data(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "label": s["label"], "unit": s["unit"], "frequency": s.get("frequency"), "pointCount": len(s["points"]),
            "first": s["points"][0] if s["points"] else None,
            "last": s["points"][-1] if s["points"] else None,
            "minimum": min(s["points"], key=lambda p: p["value"]) if s["points"] else None,
            "maximum": max(s["points"], key=lambda p: p["value"]) if s["points"] else None,
        } for s in series]

    def _explain(self, series: list[dict[str, Any]], spec: ChartSpec, fallback: str, query: str = "") -> dict[str, Any]:
        from report_summary import explain
        return explain(series, self.ncp_client, query=query)

    def _propose_spec(self, series: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
        if not self.ncp_client or not self.ncp_client.configured:
            return {}
        system = (
            "너는 통계 그래프 명세 생성기다. 사용자 요청과 실제 데이터 구조만 사용한다. "
            "허용된 유형은 line, bar, stacked_bar, area, scatter, bubble, pie, donut, "
            "histogram, box, heatmap, treemap, waterfall이다. JSON 객체 하나만 반환하라. "
            "필드는 chart_type, title, subtitle, x_axis_label, y_axis_label, legend_position, "
            "highlights([{start,end,label}]), series_chart_types(계열 이름별 line/bar/area), secondary_axis_series만 허용한다. "
            "막대와 선을 함께 요청하면 각 계열의 유형을 series_chart_types에 넣고, 단위가 다른 보조 계열을 secondary_axis_series에 넣어라. "
            "데이터에 필요한 차원이 없으면 line 또는 bar를 선택하라. "
            "frequency=Q의 YYYY01~YYYY04는 1~4분기이며 월이 아니다. 실제 주기를 제목과 부제에 반영한다. "
            "통계 수치나 데이터 계열을 새로 만들지 마라."
            "series_chart_types의 키와 secondary_axis_series 값은 제공된 series.label을 띄어쓰기와 구분 기호까지 그대로 복사하라. "
            "주택담보대출처럼 줄인 지표명으로 바꾸지 마라. 원·도넛 등에는 계열별 선/막대 유형이나 보조축을 넣지 마라."
        )
        user = json.dumps({"request": request.get("natural_language") or "", "series": self._model_data(series)}, ensure_ascii=False)
        try:
            text, _ = self.ncp_client.chat_main(system, user, max_tokens=380, thinking_effort="none")
            proposed = self.ncp_client._json_object(text)
            allowed = {"chart_type", "title", "subtitle", "x_axis_label", "y_axis_label", "legend_position", "highlights", "series_chart_types", "secondary_axis_series"}
            return {key: value for key, value in proposed.items() if key in allowed}
        except Exception:
            return {}

    @staticmethod
    def _apply_data_edits(series: list[dict[str, Any]], spec: ChartSpec) -> list[dict[str, Any]]:
        shaped = []
        for item in series:
            if item["label"] in spec.hidden_series:
                continue
            points = [p for p in item["points"] if
                      (not spec.period_start or p["date"] >= spec.period_start) and
                      (not spec.period_end or p["date"] <= spec.period_end)]
            if spec.transform == "cumulative":
                total = 0.0
                points = [{"date": p["date"], "value": (total := total + p["value"])} for p in points]
            elif spec.transform == "average" and points:
                points = [{"date": points[-1]["date"], "value": sum(x["value"] for x in points) / len(points)}]
            elif spec.transform in {"growth_rate", "year_over_year"}:
                if spec.transform == "year_over_year":
                    from chart_data_transforms import year_over_year
                    if item.get('calculation') or '%' in item['unit'] or '％' in item['unit']:
                        raise ValueError('이미 비율·증감률인 자료입니다. 원지수·금액 등 수준 자료에서 전년동기 대비를 선택해 주세요.')
                    transformed=year_over_year(item['points'],item.get('frequency'))
                    visible={p['date'] for p in points}
                    points=[p for p in transformed if p['date'] in visible]
                    if not points:
                        raise ValueError('전년 같은 시점의 값이 없거나 0이어서 증가율을 계산할 수 없습니다.')
                else:
                    lag = 1
                    points = [{"date": points[i]["date"], "value": (points[i]["value"] / points[i-lag]["value"] - 1) * 100}
                              for i in range(lag, len(points)) if points[i-lag]["value"] != 0]
            unit = "%" if spec.transform in {"growth_rate", "year_over_year"} else item["unit"]
            shaped.append({**item, "points": points, "unit": unit})
        if spec.top_n:
            shaped = sorted(shaped, key=lambda s: abs(s["points"][-1]["value"]) if s["points"] else -1, reverse=True)[:spec.top_n]
        if spec.series_order:
            shaped.sort(key=lambda s: spec.series_order.index(s["label"]))
        return shaped

    @staticmethod
    def _output_series(result, series, target='chart'):
        """Use the verified execution plan, never labels inferred by styling."""
        outputs = (((result.get('_derived') or {}).get('record') or {}).get('plan') or {}).get('outputs', [])
        if not outputs or target == 'all' or all(out.get('display', 'both') == 'both' for out in outputs):
            return series
        allowed = {(out['node'], out['label']) for out in outputs
                   if out.get('display', 'both') in {'both', target}}
        return [s for s in series if (s['sourceId'], s['label']) in allowed]

    @staticmethod
    def _layout_reason(result, series):
        reason = combined_layout_reason(series)
        names = [p.get('table_name', '') for p in result.get('api_plans', [])]
        if len(series) > 1 and any('잔액표' in n for n in names) and any('거래표' in n for n in names):
            return reason or '잔액과 기간 중 거래는 의미가 다르므로 계열별 그래프로 표시합니다.'
        return reason

    def _display_series(self, result, spec, source_series=None, *, target='chart'):
        if source_series is None:
            source_series = self._series((result.get('execution') or {}).get('rows') or [])
            plans = result.get('api_plans') or [result.get('api_plan')]
            for item in source_series:
                matching = [p for p in plans if p and (str(p.get('table_id')) == item.get('sourceId') or len(plans) == 1)]
                if spec.transform == 'year_over_year' and not item.get('frequency') and matching:
                    item['frequency'] = matching[0].get('frequency', '')
        transform_series = source_series
        if spec.transform == 'year_over_year':
            history = self._series((result.get('execution') or {}).get('chartComparisonHistory') or [])
            by_key = {(s['sourceId'], s['label'], s['unit']): s for s in history}
            annual = self._series((result.get('execution') or {}).get('chartAnnualIndices') or [])
            annual_by_key = {(s['sourceId'], s['label'], s['unit']): s for s in annual}
            transform_series = []
            for item in source_series:
                prior = by_key.get((item['sourceId'], item['label'], item['unit']), {})
                lookup = {p['date']: p for p in prior.get('points', [])}
                lookup.update({p['date']: p for p in item['points']})
                # Display bounds belong to the original observations, not the warmup fetch.
                first, last = item['points'][0]['date'], item['points'][-1]['date']
                bounded = spec.model_copy(update={'period_start': max(first, spec.period_start or first),
                                                  'period_end': min(last, spec.period_end or last)})
                combined = {**item, 'points': [lookup[d] for d in sorted(lookup)]}
                shaped = self._apply_data_edits([combined], bounded.model_copy(update={'series_order': [], 'top_n': None}))
                if spec.presentation.publication_yoy and item.get('frequency') == 'M' and '=100' in item['unit'].replace(' ', '').replace('＝', '='):
                    from chart_data_transforms import annual_index_growth
                    for s in shaped:
                        annual_points = annual_by_key.get((item['sourceId'], item['label'], item['unit']), {}).get('points', [])
                        s['annualGrowth'] = annual_index_growth(annual_points, bounded.period_start, bounded.period_end)
                transform_series.extend(shaped)
            series = self._apply_data_edits(transform_series, spec.model_copy(update={'transform': 'raw'}))
        else:
            series = self._apply_data_edits(source_series, spec)
        return self._output_series(result, series, target)

    @observed('output.build', 'span')
    def _build(self, result: dict[str, Any], spec: ChartSpec, generate_explanation: bool = True) -> dict[str, Any]:
        if result.get('derivedEvidence') and spec.transform != 'raw':
            raise ValueError('파생지표의 계산 조건은 자연어 요청 또는 계산 트리에서 수정해 주세요.')
        validation = result.get("request_validation")
        if validation is not None and validation.get("valid") is not True:
            raise ValueError("요청과 통계표가 일치하지 않아 그래프 출력을 중단했습니다.")
        plans = result.get("api_plans") or ([result["api_plan"]] if result.get("api_plan") else [])
        planned_ids = {str(p["table_id"]) for p in plans}
        if validation and set(str(t) for t in validation.get("table_ids", [])) != planned_ids:
            raise ValueError("검증 이후 출력 통계표가 변경되었습니다.")
        for row in (result.get("execution") or {}).get("rows") or []:
            source = row.get("_SOURCE_SERIES_ID") or row.get("TBL_ID")
            if result.get('derivedEvidence'):
                from derived_indicator_runtime import validate_derived_output_row
                validate_derived_output_row(result, row, planned_ids)
                continue
            if planned_ids and source and str(source) not in planned_ids:
                raise ValueError("출력 자료의 출처가 요청한 통계표와 다릅니다.")
        source_series = self._series((result.get("execution") or {}).get("rows") or [])
        from derived_period_comparison import output_series, comparison_plan, decorate_figure
        source_series = output_series(result,source_series)
        comparison = comparison_plan(result)
        for item in source_series:
            matching = [p for p in plans if str(p.get('table_id')) == item.get('sourceId')]
            if not matching and len(plans) == 1:
                matching = plans
            if not item.get('frequency') and matching:
                item['frequency'] = str(matching[0].get('frequency') or '')
        # Summaries read original observations in the displayed period, never
        # cumulative/average/growth-rate values generated for chart display.
        summary_series = [{**item, 'points': [p for p in item['points']
            if (not spec.period_start or p['date'] >= spec.period_start)
            and (not spec.period_end or p['date'] <= spec.period_end)]} for item in source_series]
        displayed = self._display_series(result, spec, source_series, target='all')
        table_series = self._output_series(result, displayed, 'table')
        series = self._output_series(result, displayed, 'chart')
        visible_labels = {s['label'] for s in series}
        summary_series = [s for s in summary_series if s['label'] in visible_labels]
        series = [{**s, "color": spec.series_colors.get(s["label"], s["color"])} for s in series]
        from chart_designs import palette, materialize
        design_colors=palette(spec)
        if design_colors:series=[{**s,'color':spec.series_colors.get(s['label'],design_colors[i%len(design_colors)])} for i,s in enumerate(series)]
        if spec.report:
            from advanced_chart_spec import PALETTE
            colors=design_colors or PALETTE
            series=[{**s,'color':spec.series_colors.get(s['label'],colors[i%len(colors)])} for i,s in enumerate(series)]
        series=[{**s,'color':(spec.series_styles.get(s['label']).color if spec.series_styles.get(s['label']) and spec.series_styles[s['label']].color else s['color'])} for s in series]
        labels = {s["label"] for s in source_series}
        if any(label not in labels for label in [*spec.series_chart_types, *spec.secondary_axis_series, *spec.series_colors, *spec.series_dashes, *spec.series_styles, *spec.display_names, *(r.label for r in spec.range_styles)]):
            raise ValueError("그래프 명세에 조회되지 않은 계열이 포함되어 있습니다.")
        from research_chart_editing import validate_research_spec
        validate_research_spec(series, spec)
        if spec.secondary_axis_series and spec.chart_type not in {"line", "bar", "stacked_bar", "area"}:
            raise ValueError("보조축은 선·막대·영역 그래프에서만 사용할 수 있습니다.")
        mixed_units = len(comparable_axis_units(series)) > 1
        mixed_frequency = len({s["frequency"] for s in series if s["frequency"]}) > 1
        if not spec.report and (mixed_frequency or (mixed_units and not spec.secondary_axis_series)) and spec.layout == "combined" and spec.chart_type not in {"scatter", "bubble"}:
            raise ValueError(combined_layout_reason(series))
        summary = '업로드한 원자료와 확인한 기간·방향·평균 대상으로 변화율을 계산했습니다.' if comparison else self._summary(summary_series)
        renderer_info={}
        if spec.report:
            from advanced_chart_render import plotly_report, echarts_report, report_warnings
            from report_export import document
            figure=plotly_report(series,materialize(spec,[s['label'] for s in series]),result)
            try:
                renderer_info['echartsOption']=echarts_report(series,materialize(spec,[s['label'] for s in series]),result)
            except ValueError as error:
                renderer_info['echartsUnavailable']=str(error)
            renderer_info['reportDocument']=document(result,spec)
            renderer_info['warnings']=report_warnings(series,spec)
            if spec.report.kind=='regression':
                from advanced_chart_render import regression
                renderer_info['regression']=regression(series)
        else:
            figure = render_plotly(series, materialize(spec,[s['label'] for s in series]))
            from advanced_chart_render import report_warnings
            renderer_info['warnings']=report_warnings(series,spec)
            if spec.presentation.design_template=='snapshot':
                import plotly.graph_objects as go
                from plotly_renderer import arrange_bottom_captions
                from advanced_chart_render import source_caption
                from types import SimpleNamespace
                native=go.Figure(figure)
                native.add_annotation(name='statbridge-footer:source',x=0,y=-.1,xref='paper',yref='paper',text=source_caption(result,SimpleNamespace(source_note='')),showarrow=False,font=dict(size=11,color='#657586'))
                if spec.presentation.publication_yoy or spec.presentation.publication_axes:
                    native.layout.annotations[-1].update(y=0, yshift=-45, yanchor='top', xanchor='left', font=dict(size=13, color='#111111'))
                    if plans and all(str(p.get('org_id')) == '301' for p in plans):
                        native.layout.annotations[-1].text = native.layout.annotations[-1].text.replace('자료: ', '자료: 한국은행 · ', 1)
                    if any(s.get('annualGrowth') for s in series):
                        native.add_annotation(name='statbridge-footer:annual-method', x=0, y=0, xref='paper', yref='paper',
                            yshift=-65, yanchor='top', xanchor='left', showarrow=False,
                            text='주: 점선은 공표 연간지수의 전년 대비 증가율', font=dict(size=11, color='#666666'))
                        native.update_layout(margin=dict(b=max(native.layout.margin.b or 0,95)))
                else:
                    arrange_bottom_captions(native,series,spec)
                figure=json.loads(native.to_json())
        figure=decorate_figure(result,figure,series)
        from level_yoy_comparison import CALCULATION_NOTE
        calculations=(result.get('execution') or {}).get('calculations') or []
        if calculations:
            note = ('파생지표는 KOSIS 원자료와 확인된 계산식으로 산출했습니다. 원자료·산식·결측 처리는 계산 근거에서 확인할 수 있습니다.'
                    if result.get('derivedEvidence') else CALCULATION_NOTE)
            renderer_info.setdefault('warnings',[]).append(note)
            if any(s.get('source')=='user_file' for s in (result.get('execution') or {}).get('sources',[])):
                renderer_info['warnings'][-1]='파생지표에 사용자 제공 파일이 포함되어 있습니다. API 자료와 파일의 단위·출처·해시는 계산 근거에서 확인해 주세요.'
        if spec.transform == 'year_over_year':
            renderer_info.setdefault('warnings', []).append(CALCULATION_NOTE)
            raw_count=sum(len(s['points']) for s in summary_series)
            shown_count=sum(len(s['points']) for s in series)
            if shown_count < raw_count:
                renderer_info['warnings'].append(f'전년도 비교값이 없거나 0인 {raw_count-shown_count}개 관측값은 표시하지 않았습니다.')
        from report_summary import explain
        from report_export import export_formats
        explanation = {'text':summary,'facts':[]} if comparison else (self._explain(summary_series, spec, summary, query=str(result.get("query") or "")) if generate_explanation else explain(summary_series, query=str(result.get('query') or '')))
        explanation['scope'] = {'basis': 'original_observations', 'transform': spec.transform, 'labels': [s['label'] for s in summary_series], 'periodStart': spec.period_start, 'periodEnd': spec.period_end}
        table_document=build_table(table_series, spec.report_table, title=spec.title, sources=self._evidence(result), display_names=spec.display_names)
        if comparison:
            table_document['notes']=['사용자 파일의 실제 관측값으로 계산한 기간별 변화율(%)입니다. 월평균은 선택한 시작 월·종료 월의 관측값 평균 후 계산했습니다. 휴일·결측값은 보간하지 않았습니다.',
                '계산 기간·원자료 표시 방향·평균 구성·출처·파일 해시는 계산 근거에서 확인해 주세요.']
        explanation['calculations']=calculations
        if spec.transform == 'year_over_year':
            explanation['comparisonSources'] = (result.get('execution') or {}).get('chartComparisonSources', [])
        if calculations:explanation['scope']['basis']='source_observations_and_server_calculations'
        return {
            "status": "ready" if any(s["points"] for s in series) else "empty",
            "agent": "output-agent-chartspec-v2",
            "summary": explanation["text"],
            "table": self._table(table_series),
            "tableSeries": table_series,
            "exportFormats": export_formats(series,spec,result),
            "tableDocument": table_document,
            "explanation": explanation,
            "evidence": self._evidence(result),
            "chartState": spec.model_dump(),
            "plotlyFigure": figure,
            **renderer_info,
            "visualization": {
                "chartType": spec.chart_type, "layout": spec.layout, "series": series, "editable": True,
                "editOptions": {
                    "title": spec.title, "showLegend": spec.show_legend,
                    "xAxisLabel": spec.x_axis_label, "yAxisLabel": spec.y_axis_label,
                    "supportedChartTypes": inspect_charts(series)[0],
                    "supportedLayouts": sorted(SUPPORTED_LAYOUTS),
                },
            },
        }

    @observed('output.prepare', 'span')
    def prepare(self, result: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = dict(request or {})
        series = self._series((result.get("execution") or {}).get("rows") or [])
        from derived_period_comparison import output_series,comparison_plan
        series=output_series(result,series)
        series=self._output_series(result,series)
        from quick_output import dual_axis_requested,dual_axis_defaults
        dual_intent=dual_axis_requested(raw.get('natural_language') or '')
        quick = bool(raw.get('quick_output'))
        if quick:
            from quick_output import requested_chart
            explicit = ('line' if dual_intent else requested_chart(raw.get('natural_language') or '')) if raw.get('chart_type') in {None, 'auto'} else None
            if explicit:
                raw['chart_type'] = explicit
            if self._layout_reason(result, series):
                raw['layout'] = 'separate'
            labels = list(dict.fromkeys(item['label'] for item in series))
            title = ' · '.join(labels[:2])
            parts=[label.split(' · ',1) for label in labels]
            shared=len(labels)>1 and all(len(part)==2 and part[0]==parts[0][0] for part in parts)
            if shared:title=parts[0][0]
            elif len(labels) > 2:
                title += f' 외 {len(labels)-2}개 지표'
            proposed = {'title': title + (' 비교' if len(labels) > 1 else ' 추이') if title else '통계 그래프'}
        else:
            proposed = self._propose_spec(series, raw)
        # Initial output is neutral. Background highlights are user edits,
        # not decorations that the presentation model may invent.
        proposed.pop("highlights", None)
        labels={s['label'] for s in series}
        references=[]
        mapping=proposed.get('series_chart_types')
        if isinstance(mapping,dict):references.extend(mapping)
        axes=proposed.get('secondary_axis_series')
        if isinstance(axes,list):references.extend(axes)
        unknown=[label for label in references if not isinstance(label,str) or label not in labels]
        rejected_proposal=bool(unknown)
        if rejected_proposal:
            # Discard untrusted styling, never remap a guessed name to another series.
            # User-selected type/layout and original observations remain authoritative.
            proposed={}
        requested_type = str(raw.get("chart_type") or "auto")
        if requested_type not in SUPPORTED_CHART_TYPES:
            raise ValueError("지원하지 않는 그래프 종류입니다.")
        if requested_type != "auto":
            proposed["chart_type"] = requested_type
            # A model's per-series suggestion must not override the explicit
            # chart type selected by the user. Mixed types remain available via
            # explicit follow-up edits and automatic output planning.
            proposed.pop("series_chart_types", None)
        elif proposed.get("chart_type") not in SUPPORTED_CHART_TYPES - {"auto"}:
            proposed["chart_type"] = self._select_chart_type("auto", series)
        if proposed.get('chart_type') not in {'line','bar','stacked_bar','area'}:
            proposed.pop('series_chart_types',None)
            proposed.pop('secondary_axis_series',None)
        elif raw.get('layout') and raw['layout']!='combined':
            proposed.pop('secondary_axis_series',None)
        calculations=(result.get('execution') or {}).get('calculations') or []
        if calculations and requested_type in {'auto','line','bar'}:
            derived={c['label'] for c in calculations}
            right=[item['label'] for item in series if item['label'] in derived]
            left=[item for item in series if item['label'] not in derived]
            compatible=(len({item.get('frequency') for item in series})==1 and len(comparable_axis_units(left))==1)
            if right and len(right)<len(series) and compatible:
                raw['layout']='combined'
                proposed.update(chart_type='line',series_chart_types={label:'bar' for label in right},secondary_axis_series=right,
                    axes={'y':{'title_orientation':'horizontal','title_placement':'top'},'y2':{'title_orientation':'horizontal','title_placement':'top'}})
            elif right and len(right)<len(series):
                raw['layout']='separate'
                proposed.pop('secondary_axis_series',None)
                proposed.pop('series_chart_types',None)
        if dual_intent:
            if requested_type not in {'auto','line','bar','stacked_bar','area'}:
                raise ValueError('이중축 혼합차트는 선·막대·영역 계열로 구성해 주세요.')
            proposed.update(dual_axis_defaults(series))
            raw['layout']='combined'
        proposed.update(layout=raw.get("layout") or "combined", show_legend=raw.get("show_legend", True))
        for field in ("title", "subtitle", "x_axis_label", "y_axis_label"):
            if raw.get(field): proposed[field] = raw[field]
        try:
            spec = ChartSpec.model_validate(proposed)
        except ValueError:
            spec = ChartSpec(chart_type=self._select_chart_type(requested_type, series), layout=raw.get("layout") or "combined",
                             title=raw.get("title") or "", x_axis_label=raw.get("x_axis_label") or "",
                             y_axis_label=raw.get("y_axis_label") or "", show_legend=raw.get("show_legend", True))
        if spec.presentation.design_template is None:
            from chart_designs import apply_design
            spec=apply_design(spec,'snapshot')
        if comparison_plan(result):
            spec.chart_type='bar' if requested_type=='auto' else spec.chart_type
            spec.y_axis_label='변화율 (%)'
            spec.series_colors={**{s['label']:s['color'] for s in series},**spec.series_colors}
        try:
            output=self._build(result, spec, generate_explanation=not quick)
        except ValueError:
            if requested_type != "auto" or spec.chart_type in {"line", "bar"}:
                raise
            safe = ChartSpec.model_validate({**spec.model_dump(), "chart_type": self._select_chart_type("auto", series)})
            output=self._build(result, safe, generate_explanation=not quick)
        if rejected_proposal:
            output['warnings']=['자동 스타일 제안의 항목명이 조회 결과와 달라 적용하지 않았습니다. 선택한 그래프 종류로 실제 조회된 계열을 표시했습니다.']
        return output

    def edit(self, result: dict[str, Any], current_output: dict[str, Any], instruction: str) -> dict[str, Any]:
        """Compatibility entry point; interpretation lives exclusively in ChartEditAgent."""
        from chart_edit_agent import ChartEditAgent, Hcx007EditModel
        from ncp_clova_client import NcpClovaClient
        if isinstance(self.ncp_client, NcpClovaClient):
            model = Hcx007EditModel(self.ncp_client)
        else:
            # Explicit injected clients are used by existing offline regression tests.
            client = self.ncp_client
            class InjectedModel:
                model_name = "injected-test-model"
                supports_images = False
                requires_legacy_explanation = True
                def interpret(self, system: str, context: dict[str, Any]) -> dict[str, Any]:
                    if not client or not client.configured:
                        raise ValueError("자연어 그래프 수정에는 NCP_CLOVA_API_KEY가 필요합니다.")
                    text, _ = client.chat_main(system, json.dumps(context, ensure_ascii=False), max_tokens=1400, thinking_effort="none")
                    return client._json_object(text)
            model = InjectedModel()
        return ChartEditAgent(model, self).edit(result, current_output, instruction)

    @observed('edit.apply', 'span')
    def apply_edits(self, result: dict[str, Any], current_output: dict[str, Any], commands: list[ChartEditCommand], *, generate_explanation: bool = True) -> dict[str, Any]:
        """Apply a validated batch atomically, then render exactly once."""
        labels = [s["label"] for s in self._series((result.get("execution") or {}).get("rows") or [])]
        spec = ChartSpec.model_validate(current_output["chartState"])
        before = spec.model_dump()
        effective = []
        for command in commands:
            if command.operation=='filter_period' and 'years' in command.params:
                years=command.params['years']
                if years is not None and (type(years) is not int or years not in {1,5,10}):
                    raise ValueError('빠른 기간은 1년·5년·10년·전체 중에서 선택해 주세요.')
                dates=sorted({p['date'] for s in self._series((result.get('execution') or {}).get('rows') or []) for p in s['points']})
                if not dates:raise ValueError('기간을 선택할 관측값이 없습니다.')
                start=None;end=None
                if years:
                    end=dates[-1];cutoff=str(int(end[:4])-years).zfill(4)+end[4:]
                    start=next(d for d in dates if d>cutoff)
                command=command.model_copy(update={'start':start,'end':end})
            changed = self._apply_edit_spec(spec, command, labels)
            if changed.model_dump() != spec.model_dump():
                effective.append(command)
            spec = changed
        if spec.model_dump() == before:
            raise ValueError("요청한 설정이 이미 적용되어 있어 변경된 내용이 없습니다. 다른 수정 내용을 입력해 주세요.")
        edited = self._build(result, spec, generate_explanation=generate_explanation)
        if (edited.get('explanation', {}).get('facts') == current_output.get('explanation', {}).get('facts')
                and current_output.get('explanation', {}).get('facts')):
            # Reuse wording, but retain the current display/transform scope.
            edited['explanation'] = {**current_output['explanation'], 'scope': edited['explanation'].get('scope', {})}
            edited['summary'] = current_output['summary']
        state_only=(edited.get('plotlyFigure')==current_output.get('plotlyFigure')
                    and edited.get('tableDocument')==current_output.get('tableDocument'))
        edited["editChanges"] = [edit_change_description(c) for c in effective]
        if state_only:
            edited['stateOnlyChange']=True
            edited['editChanges'].append('설정을 저장했습니다. 현재 모양은 같으며 이후 디자인 변경이나 파일 출력에 반영됩니다.')
        if any(c.operation == 'set_chart_type' for c in effective) and (
                before['range_styles'] or before['series_styles'] or before['series_chart_types'] or before['guides']):
            edited['editChanges'].append('그래프 종류를 변경해 이전 그래프 전용 부분 스타일·혼합 종류를 정리했습니다. 원자료와 표시 기간은 유지됩니다.')
        edited["lastEdit"] = effective[-1].model_dump()
        edited["editHistory"] = [*(current_output.get("editHistory") or []), *(c.model_dump() for c in effective)][-120:]
        return edited

    @staticmethod
    def _apply_edit_spec(current: ChartSpec, command: ChartEditCommand, labels: list[str]) -> ChartSpec:
        from research_chart_editing import apply_research_edit
        extended = apply_research_edit(current, command, labels)
        if extended is not None:
            return extended
        updates: dict[str, Any] = {}
        if command.params and not (command.operation=="filter_period" and set(command.params)<={"years"}) and (command.operation != "highlight_period" or set(command.params) - {"color", "opacity"}):
            raise ValueError(f"{command.operation} 명령에 적용할 수 없는 설정이 있습니다: {', '.join(command.params)}. 설정을 확인해 주세요.")
        value = command.value
        if command.operation == "remove_annotation":
            text=str(value or "")
            if not text: raise ValueError("삭제할 주석 텍스트를 지정해 주세요.")
            updates.update(annotations=[a for a in current.annotations if a.text != text],
                notes=[n for n in current.notes if n.text != text],
                highlights=[h.model_copy(update={"label":""}) if h.label == text else h for h in current.highlights])
        elif command.operation == "set_title": updates["title"] = str(value or "")
        elif command.operation == "set_subtitle": updates["subtitle"] = str(value or "")
        elif command.operation == "set_axis_labels":
            if command.x_axis_label is None and command.y_axis_label is None:
                raise ValueError("축 이름 수정 명령에 새 축 이름이 없습니다. 눈금 수정은 축 표시 명령으로 요청해 주세요.")
            updates.update(x_axis_label=current.x_axis_label if command.x_axis_label is None else command.x_axis_label,
                           y_axis_label=current.y_axis_label if command.y_axis_label is None else command.y_axis_label)
        elif command.operation == "set_chart_type":
            if value not in {'line','bar','stacked_bar','area'}:
                updates['presentation']=current.presentation.model_copy(update={'publication_yoy':False,'publication_axes':False})
            if value != 'bar':
                updates['presentation'] = current.presentation.model_copy(update={'publication_yoy': False})
            # An explicit legacy type selection takes precedence over a report
            # renderer's kind; do not leave a hidden report kind drawing lines.
            updates['report']=None
            if current.report and current.report.kind == 'dual_axis' and value in {'line','bar','stacked_bar','area'}:
                updates['secondary_axis_series'] = list(current.report.right_axis)
            # A global kind change must not retain per-series overrides from
            # the previous mixed chart, otherwise "bar" can still draw lines.
            updates.update(chart_type=value, series_chart_types={})
            if value != current.chart_type:
                updates.update(range_styles=[], series_styles={
                    label: style.model_copy(update={key:None for key in style.model_dump(exclude_none=True) if key not in {'color','opacity'}})
                    for label,style in current.series_styles.items()
                    if value != 'heatmap' and (value not in {'scatter','bubble'} or label == labels[0])})
                if value not in {'bar','stacked_bar'}:
                    updates['presentation'] = current.presentation.model_copy(update={'bar_orientation':'vertical', 'publication_yoy':False})
                if value not in {'line','bar','stacked_bar','area'}:
                    updates.update(guides=[], notes=[note for note in current.notes if not note.label])
                    updates['presentation']=updates.get('presentation',current.presentation).model_copy(update={'publication_axes':False})
            if value not in {"line", "bar", "stacked_bar", "area"}:
                updates["secondary_axis_series"] = []
            if (value=='stacked_bar' and (value!=current.chart_type or current.series_chart_types)) or (current.series_chart_types and current.presentation.bar_mode in {'stack','relative'}):
                presentation=updates.get('presentation',current.presentation)
                updates['presentation']=presentation.model_copy(update={'bar_mode':'relative' if value=='stacked_bar' else 'group'})
        elif command.operation == "set_layout":
            updates['layout']=value
            if value!='combined':
                updates['presentation']=current.presentation.model_copy(update={'publication_yoy':False,'publication_axes':False})
            if current.report and value!='combined':
                updates['report']=current.report.model_copy(update={'kind':'panels','columns':len(labels) if value=='horizontal' else 2 if value=='grid' else 1})
            elif current.report and current.report.kind=='panels':
                updates['report']=current.report.model_copy(update={'kind':'forecast'})
        elif command.operation == "set_legend":
            if isinstance(value, bool): updates["show_legend"] = value
            else: updates["legend_position"] = value
        elif command.operation == "set_line_width": updates["line_width"] = value
        elif command.operation == "highlight_period": updates["highlights"] = [*current.highlights, Highlight(start=command.start or "", end=command.end or "", label=command.label or "", **{k:v for k,v in command.params.items() if k in {"color","opacity"}})]
        elif command.operation == "highlight_series":
            if value not in labels: raise ValueError("선택한 계열이 조회 결과에 없습니다.")
            updates["highlighted_series"] = str(value)
        elif command.operation == "add_annotation": updates["annotations"] = [*current.annotations, Annotation(period=command.start or "", text=str(value or ""))]
        elif command.operation == "add_reference_line": updates["reference_lines"] = [*current.reference_lines, float(value)]
        elif command.operation == "hide_series":
            if value not in labels: raise ValueError("제외할 계열이 조회 결과에 없습니다.")
            updates["hidden_series"] = [*current.hidden_series, str(value)]
        elif command.operation == "filter_period": updates.update(period_start=command.start, period_end=command.end,period_window_years=command.params.get("years"))
        elif command.operation == "set_top_n": updates["top_n"] = value
        elif command.operation == "set_transform":
            updates['transform'] = value
            if value != current.transform:
                updates['y_axis_label'] = '전년동기 대비 (%)' if value == 'year_over_year' else ''
                updates['axes'] = {k: v for k, v in current.axes.items() if k == 'x'}
                if value != 'year_over_year':
                    updates['presentation'] = current.presentation.model_copy(update={'publication_yoy': False})
        elif command.operation == "set_series_chart_type":
            if command.label not in labels: raise ValueError("선택한 계열이 조회 결과에 없습니다.")
            updates["series_chart_types"] = {**current.series_chart_types, command.label: value}
        elif command.operation == "set_secondary_axis":
            if value not in labels: raise ValueError("보조축에 표시할 계열이 조회 결과에 없습니다.")
            updates["secondary_axis_series"] = list(dict.fromkeys([*current.secondary_axis_series, str(value)]))
            updates["layout"] = "combined"
            updates['presentation']=current.presentation.model_copy(update={'publication_yoy':False,'publication_axes':False})
            if current.report:
                updates["report"]=current.report.model_copy(update={"kind":"dual_axis","right_axis":updates["secondary_axis_series"]})
        elif command.operation in {"set_series_color", "set_series_dash"}:
            if command.label not in labels:
                raise ValueError("수정할 계열이 조회 결과에 없습니다.")
            field = "series_colors" if command.operation == "set_series_color" else "series_dashes"
            updates[field] = {**getattr(current, field), command.label: value}
            if command.label in current.series_styles:
                key='color' if command.operation=='set_series_color' else 'dash'
                updates['series_styles']={**current.series_styles,command.label:current.series_styles[command.label].model_copy(update={key:None})}
        edited_spec = current.model_copy(update=updates)
        return ChartSpec.model_validate(edited_spec.model_dump())

    def inspect(self, result: dict[str, Any]) -> dict[str, Any]:
        """Describe available output choices without producing a render spec."""
        series = self._series((result.get("execution") or {}).get("rows") or [])
        from derived_period_comparison import output_series,comparison_plan
        series=output_series(result,series)
        series=self._output_series(result,series)
        allowed, unavailable = inspect_charts(series)
        return {
            "seriesCount": len(series),
            "pointCount": sum(len(item.get("points") or []) for item in series),
            "recommendedChartType": 'bar' if comparison_plan(result) else self._select_chart_type("auto", series),
            "supportedChartTypes": allowed,
            "unavailableChartTypes": unavailable,
            "combinedLayoutReason": self._layout_reason(result, series),
            "supportedLayouts": sorted(SUPPORTED_LAYOUTS),
            "editableFields": ["title", "show_legend", "x_axis_label", "y_axis_label"],
        }
