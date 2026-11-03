"""Synthetic observation fixtures, never provider output or evaluation answers."""
import copy
import io
import zipfile
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from report_table import TableOptions, build_table, render_table_file
from output_schema import ChartEditCommand, ChartSpec
from output_agent import OutputAgent
from report_export import document, restore_document


def series():
    return [{'label': '한국', 'unit': '%', 'frequency': 'M', 'points': [{'date': '202401', 'value': 1.234567}, {'date': '202403', 'value': -2}]},
            {'label': '미국', 'unit': '백만원', 'frequency': 'M', 'points': [{'date': '202402', 'value': 0}, {'date': '202403', 'value': 8}]}]


def test_table_preserves_observations_missing_zero_precision_and_transpose():
    original = series(); before = copy.deepcopy(original)
    a = build_table(original)
    assert a['sections'][0]['rows'][0][1:] == [1.234567, None, -2]
    assert a['sections'][0]['rows'][1][1:] == [None, 0, 8]
    b = build_table(original, TableOptions(orientation='periods', labels=['미국', '한국'], recent_periods=2))
    assert b['sections'][0]['rows'] == [['2024-02', 0, None], ['2024-03', 8, -2]]
    assert original == before


def test_mixed_frequency_is_separate_and_quarter_not_january():
    rows = series(); rows[1]['frequency'] = 'Q'; rows[1]['points'][0]['date'] = '202401'
    doc = build_table(rows)
    assert len(doc['sections']) == 2
    assert doc['sections'][1]['headers'][1] == '2024년 1분기'


def test_hiding_or_replacing_a_selected_series_does_not_block_graph_edits():
    doc = build_table(series()[1:], TableOptions(labels=['한국', '미국']))
    assert doc['options']['labels'] == ['미국']
    assert len(doc['sections'][0]['rows']) == 1
    assert any('제외' in n for n in doc['notes'])


@pytest.mark.parametrize('patch', [{'decimals': 7}, {'recent_periods': 0}, {'value': 100}, {'orientation': 'sum'}])
def test_options_cannot_change_observations_or_request_invalid_settings(patch):
    with pytest.raises(ValueError): TableOptions.model_validate(patch)


def test_xlsx_is_numeric_and_no_formula_for_untrusted_text():
    rows = series(); rows[0]['label'] = '=HYPERLINK("bad")'
    doc = build_table(rows, TableOptions(decimals=3))
    out = render_table_file(doc, 'xlsx')
    with zipfile.ZipFile(io.BytesIO(out)) as archive:
        for name in archive.namelist(): ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        assert root.findall('.//s:f', ns) == []
        assert '1.234567' in [v.text for v in root.findall('.//s:v', ns)]
    csv = render_table_file(doc, 'csv').decode('utf-8-sig')
    assert "'=HYPERLINK" in csv
    assert '1.235' in csv
    assert '1.234567' not in csv


def test_csv_cells_match_display_rounding_grouping_period_order_and_missing():
    import csv
    from report_table import text_value
    items = series(); items[0]['points'][0]['value'] = 12345.6789
    doc = build_table(items, TableOptions(orientation='periods', decimals=2, labels=['미국', '한국']))
    rows = list(csv.reader(io.StringIO(render_table_file(doc, 'csv').decode('utf-8-sig'))))
    section = doc['sections'][0]
    assert rows[0] == ['표 1. ' + doc['title']]
    assert rows[2] == section['headers']
    assert rows[3:6] == [[text_value(cell, 2) for cell in row] for row in section['rows']]
    assert rows[3] == ['2024-01', '—', '12,345.68']
    assert rows[4][1] == '0.00'


@pytest.mark.parametrize('value,decimals,expected', [(1.25, 1, '1.3'), (-1.25, 1, '-1.3'), (2.675, 2, '2.68'), (0, 2, '0.00')])
def test_csv_and_print_rounding_agree_with_browser_halfway_values(value, decimals, expected):
    from report_table import text_value
    assert text_value(value, decimals) == expected


def test_twelve_month_table_fits_one_page_without_fixed_five_column_split():
    rows = series(); rows[0]['points'] = [{'date': f'2024{i:02}', 'value': i} for i in range(1, 13)]
    doc = build_table(rows)
    out = render_table_file(doc, 'png')
    assert out.startswith(b'\x89PNG')
    assert render_table_file(doc, 'pdf').startswith(b'%PDF')


def test_ten_long_indicators_print_without_cell_text_overflow(monkeypatch):
    from matplotlib.figure import Figure
    original = Figure.savefig
    checked = []
    def inspect(fig, *args, **kwargs):
        fig.canvas.draw(); renderer = fig.canvas.get_renderer()
        for ax in fig.axes:
            for table in ax.tables:
                for cell in table.get_celld().values():
                    box = cell.get_window_extent(renderer)
                    text = cell.get_text().get_window_extent(renderer)
                    assert text.x0 >= box.x0 - 2 and text.x1 <= box.x1 + 2
                    assert text.y0 >= box.y0 - 2 and text.y1 <= box.y1 + 2
                checked.append(True)
        return original(fig, *args, **kwargs)
    monkeypatch.setattr(Figure, 'savefig', inspect)
    rows = [{'label': f'{i}번째 국가별 분류 지표 ' + '긴 설명과 항목 이름 ' * 4, 'unit': '%', 'frequency': 'M',
             'points': [{'date': f'2024{m:02}', 'value': i + m/10} for m in range(1, 9)]} for i in range(10)]
    doc = build_table(rows, TableOptions(title='보고서 비교표', note='검증 주석 ' * 60))
    assert render_table_file(doc, 'png').startswith(b'PK')
    assert len(checked) >= 2


def test_table_settings_roundtrip_and_legacy_document_remain_observation_bound():
    from test_advanced_reports import fixture
    result = fixture(1); agent = OutputAgent()
    spec = ChartSpec(report_table=TableOptions(decimals=3, orientation='periods'))
    saved = document(result, spec)
    assert restore_document(result, saved).report_table == spec.report_table
    old = document(result, ChartSpec()); old['state'].pop('report_table')
    assert restore_document(result, old).report_table is None
    output = agent._build(result, spec, generate_explanation=False)
    assert output['tableDocument']['options']['decimals'] == 3
    assert output['table']['rows'][0]['value'] == output['tableDocument']['sections'][0]['rows'][0][1]


def test_export_endpoint_checks_login_ownership_revision_and_saved_settings(monkeypatch, tmp_path):
    import account_auth as auth
    import bridge_api as api
    from test_account_auth import login, fixture_graph
    store = auth.AccountStore(tmp_path/'accounts.sqlite3'); monkeypatch.setattr(auth, 'STORE', store)
    anonymous = TestClient(api.app)
    assert anonymous.post('/api/table/export', json={'edit_session_id': 'table', 'revision': 0, 'format': 'xlsx'}).status_code == 401
    client, user = login('test1'); session = fixture_graph(monkeypatch, store, user, 'table')
    body = {'edit_session_id': 'table', 'revision': session['output'].get('editVersion', 0), 'format': 'xlsx'}
    assert client.post('/api/table/export', json={**body, 'revision': body['revision'] + 1}).status_code == 409
    assert client.post('/api/table/export', json={**body, 'edit_session_id': 'another'}).status_code == 404
    response = client.post('/api/table/export', json=body)
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'PK')
    edit = client.post('/api/output/edit', json={'edit_session_id': 'table', 'revision': body['revision'], 'commands': [{'operation': 'set_table', 'kind': 'STYLE_EDIT', 'params': {'patch': {'decimals': 3}}}]})
    assert edit.status_code == 200, edit.text
    assert edit.json()['outputSpec']['tableDocument']['options']['decimals'] == 3
    assert not edit.json()['outputSpec'].get('stateOnlyChange')
    undo = client.post('/api/output/edit', json={'edit_session_id': 'table', 'revision': edit.json()['outputSpec']['editVersion'], 'action': 'undo'})
    assert undo.status_code == 200
    assert undo.json()['outputSpec']['tableDocument']['options']['decimals'] == 2
