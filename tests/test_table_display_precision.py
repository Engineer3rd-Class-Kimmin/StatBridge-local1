"""ai-assisted precision harness using synthetic observations only."""
import copy
import csv
import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from output_agent import OutputAgent
from output_schema import ChartSpec
from report_table import TableOptions, build_table, render_table_file, text_value


def observations(unit='지수'):
    return [{'label': '합성 지표', 'unit': unit, 'frequency': 'M',
             'points': [{'date': '202401', 'value': 1.0049},
                        {'date': '202402', 'value': 1.0051}]}]


@pytest.mark.parametrize('unit', ['지수', '%', '2020=100', '백만원'])
@pytest.mark.parametrize('orientation', ['indicators', 'periods'])
@pytest.mark.parametrize('layout', [False, True])
def test_default_two_decimals_preserves_raw_values(unit, orientation, layout):
    source = observations(unit)
    before = copy.deepcopy(source)
    options = TableOptions(orientation=orientation, layout={'enabled': layout})
    doc = build_table(source, options)
    assert doc['options']['decimals'] == 2
    assert options.decimals is None
    assert source == before
    numeric = [v for row in doc['sections'][0]['rows'] for v in row if isinstance(v, (int, float))]
    assert numeric == [1.0049, 1.0051]
    rows = list(csv.reader(io.StringIO(render_table_file(doc, 'csv').decode('utf-8-sig'))))
    displayed = [cell for row in rows for cell in row]
    assert '1.00' in displayed and '1.01' in displayed
    assert '1.0049' not in displayed and '1.0051' not in displayed
    if layout:
        texts = [c['text'] for s in doc['layoutSections'] for r in s['sheetRows'] for c in r['cells']]
        assert '1.00' in texts and '1.01' in texts


@pytest.mark.parametrize('value,expected', [
    (2.675, '2.68'), (-2.675, '-2.68'), (9.999, '10.00'),
    (0, '0.00'), (0.000123456, '0.00'), (12345.6789, '12,345.68'),
    (None, '—'),
])
def test_round_half_up_display(value, expected):
    assert text_value(value, 2) == expected


@pytest.mark.parametrize('comparison', ['period_change', 'period_growth'])
def test_comparison_uses_unrounded_inputs(comparison):
    source = observations()
    before = copy.deepcopy(source)
    doc = build_table(source, TableOptions(comparison=comparison))
    a, b = Decimal('1.0049'), Decimal('1.0051')
    expected = float(b - a if comparison == 'period_change' else (b / a - 1) * 100)
    section = doc['sections'][0]
    assert section['computedCells'][0]['value'] == expected
    assert section['rows'][0][-1] == expected
    assert section['computedCells'][0]['observations'] == source[0]['points']
    assert source == before
    # Rounding the operands first would produce 0.01 or 1%, respectively.
    assert text_value(expected, 2) == ('0.00' if comparison == 'period_change' else '0.02')


@pytest.mark.parametrize('layout', [False, True])
def test_xlsx_preserves_numeric_values_and_sets_display_format(layout):
    doc = build_table(observations(), TableOptions(layout={'enabled': layout}))
    wb = load_workbook(io.BytesIO(render_table_file(doc, 'xlsx')))
    cells = [c for row in wb.worksheets[0] for c in row if c.data_type == 'n']
    for raw in [1.0049, 1.0051]:
        cell = next(c for c in cells if c.value == raw)
        assert cell.number_format == '#,##0.00'


def test_output_and_followup_calculation_keep_original_precision():
    from test_advanced_reports import fixture
    result = fixture(1)
    values = [1.0049 + i * .0001 for i in range(len(result['execution']['rows']))]
    for row, value in zip(result['execution']['rows'], values):
        row['DT'] = str(value)
    before = copy.deepcopy(result)
    agent = OutputAgent()
    output = agent._build(result, ChartSpec(), generate_explanation=False)
    assert output['tableDocument']['options']['decimals'] == 2
    assert [r['value'] for r in output['table']['rows']] == values
    assert [p['value'] for p in output['visualization']['series'][0]['points']] == values
    followup = agent._build(result, ChartSpec(transform='average'), generate_explanation=False)
    assert followup['visualization']['series'][0]['points'][0]['value'] == sum(values) / len(values)
    assert result == before


def test_explicit_precision_still_overrides_default():
    doc = build_table(observations(), TableOptions(decimals=4))
    assert doc['options']['decimals'] == 4
    assert '1.0049' in render_table_file(doc, 'csv').decode('utf-8-sig')


def test_report_conversation_table_rounds_display_only():
    from report_conversation import ReportPlan, execute
    from test_statistical_question import item
    source = [item('합성 금리', ('1.0049', '1.0051'))]
    before = copy.deepcopy(source)
    answer, _ = execute('표로 정리해줘', ReportPlan(style='table'), source, {})
    assert answer['status'] == 'answered'
    assert answer['table']['rows'][0][3] == '0.00'
    assert Decimal(answer['facts'][0]['result']) == Decimal('0.0002')
    assert source == before


def test_derived_difference_retains_precision_after_table_rendering():
    from dynamic_expression_executor import DynamicExpressionExecutor, Series
    from test_derived_output_views import plan
    source = {'x': Series({'202001': 1.0049, '202002': 1.0051, '202003': 1.0053}, '지수', 'M', ['x'])}
    computed = DynamicExpressionExecutor().execute(plan(), source)
    before = copy.deepcopy(computed)
    doc = build_table(OutputAgent()._series(computed['rows']))
    assert doc['options']['decimals'] == 2
    assert list(computed['series']['delta'].values.values())[-2:] == [1.0051 - 1.0049, 1.0053 - 1.0051]
    assert '0.00' in render_table_file(doc, 'csv').decode('utf-8-sig')
    assert computed == before
