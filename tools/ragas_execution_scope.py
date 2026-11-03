"""AI-assisted: verify structured display scope without trusting narrative claims.

This evaluator utility never reads a question, oracle, summary or judge verdict.
It describes the executed chart's dates only after binding every displayed value
to the same-run official observations or an executed calculation receipt.
"""
from datetime import date
import math
import re


class ScopeEvidenceError(ValueError):
    pass


def frequency(value):
    return 'Y' if value == 'A' else value


def period_bounds(value, freq):
    value, freq = str(value), frequency(freq)
    if freq in {'Q', 'S'}:
        value = re.sub(r'^(\d{4})[QH]([1-4])$', r'\g<1>0\2', value)
    try:
        if freq == 'D' and re.fullmatch(r'\d{8}', value):
            start = date(int(value[:4]), int(value[4:6]), int(value[6:]))
            return start, start
        if freq == 'Y' and re.fullmatch(r'\d{4}', value):
            return date(int(value), 1, 1), date(int(value), 12, 31)
        if freq in {'M', 'Q', 'S'} and re.fullmatch(r'\d{6}', value):
            size = {'M': 1, 'Q': 3, 'S': 6}[freq]
            part, year = int(value[4:]), int(value[:4])
            if not 1 <= part <= 12 // size:
                raise ValueError('period ordinal')
            month = (part - 1) * size + 1
            start = date(year, month, 1)
            end = date(year + 1, 1, 1) if month + size == 13 else date(year, month + size, 1)
            from datetime import timedelta
            return start, end - timedelta(days=1)
    except ValueError as exc:
        raise ScopeEvidenceError('Invalid observation period') from exc
    raise ScopeEvidenceError('Unsupported observation period or frequency')


def same_number(actual, expected):
    try:
        # The product serializes observations as floats. Require that exact
        # conversion, rather than accepting an arbitrary numeric tolerance.
        a, b = float(actual), float(expected)
        return not isinstance(actual, bool) and math.isfinite(a) and math.isfinite(b) and a == b
    except (TypeError, ValueError, OverflowError):
        return False


def _raw_binding(series, point, evidence):
    identity = series.get('sourceIdentity') or {}
    if not identity or identity.get('provider') != 'kosis':
        return None
    freq = frequency(series.get('frequency'))
    if frequency(identity.get('frequency')) != freq:
        raise ScopeEvidenceError('Display and source frequencies differ')
    for index, doc in enumerate(evidence):
        if doc['origin'] != 'official_api_response':
            continue
        payload = doc['payload']
        req = payload.get('request', {})
        if (req.get('tblId') != identity.get('table_id') or req.get('itmId') != identity.get('item_id')
                or frequency(req.get('prdSe')) != freq
                or any(req.get(k) != v for k, v in identity.get('classifications', {}).items())):
            continue
        rows = payload.get('response')
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if (row.get('TBL_ID') == identity['table_id'] and row.get('ITM_ID') == identity['item_id']
                    and frequency(row.get('PRD_SE')) == freq
                    and all(row.get(k.replace('objL', 'C')) == v for k, v in identity.get('classifications', {}).items())
                    and period_bounds(row.get('PRD_DE'), freq) == period_bounds(point['date'], freq)
                    and same_number(point['value'], row.get('DT'))):
                return {'evidence_index': index, 'row_index': row_index, 'origin': doc['origin']}
    return None


def _calculation_binding(series, point, evidence):
    node = series.get('sourceId')
    for index, doc in enumerate(evidence):
        if doc['origin'] != 'executed_calculation_evidence':
            continue
        receipt = doc['payload']
        plan = receipt.get('plan') or {}
        if node not in {o['node'] for o in plan.get('outputs', []) if o.get('display', 'both') != 'table'}:
            continue
        result = receipt.get('node_results', {}).get(node, {})
        if frequency(result.get('frequency')) != frequency(series.get('frequency')):
            continue
        if same_number(point['value'], result.get('values', {}).get(point['date'])):
            return {'evidence_index': index, 'node': node, 'period': point['date'], 'origin': doc['origin']}
    return None


def build_execution_scope(final, evidence, metadata):
    """Return typed period evidence, or None for an output without a chart.

`evidence` is an ordered list of same-case/run capture documents. The caller
must authenticate their identities and hashes; returned indices bind to that
exact list. Every displayed point must have a source binding, otherwise fail.
"""
    chart = final.get('chart') or []
    if not chart:
        return None
    configured = final.get('period') or {}
    try:
        start = date.fromisoformat(configured['start']) if configured.get('start') else None
        end = date.fromisoformat(configured['end']) if configured.get('end') else None
    except (TypeError, ValueError) as exc:
        raise ScopeEvidenceError('Invalid configured display period') from exc
    if start and end and start > end:
        raise ScopeEvidenceError('Reversed display period')
    tables = {m['table_id']: m for m in (metadata or {}).get('tables', [])}
    series_scopes = []
    for series in chart:
        points = series.get('points') or []
        if not points:
            continue
        freq = frequency(series.get('frequency'))
        bounds = [period_bounds(p['date'], freq) for p in points]
        if any(a[0] >= b[0] for a, b in zip(bounds, bounds[1:])):
            raise ScopeEvidenceError('Displayed periods must be unique and increasing')
        if any((start and b < start) or (end and a > end) for a, b in bounds):
            raise ScopeEvidenceError('Displayed observation lies outside configured display period')
        bindings = []
        for point in points:
            binding = _raw_binding(series, point, evidence) or _calculation_binding(series, point, evidence)
            if binding is None:
                raise ScopeEvidenceError('Displayed observation has no matching same-run raw or executed evidence')
            bindings.append(binding)
        used = sorted({b['evidence_index'] for b in bindings})
        table_ids, fetch_ranges = set(), []
        for index in used:
            doc = evidence[index]
            if doc['origin'] == 'official_api_response':
                req = doc['payload']['request']
                table_ids.add(req['tblId'])
                fetch_ranges.append({'table_id':req['tblId'], 'frequency':frequency(req['prdSe']),
                                     'fetch_start':req.get('startPrdDe'), 'fetch_end':req.get('endPrdDe')})
            else:
                receipt = doc['payload']
                table_ids.update(s['table_id'] for s in receipt['plan']['sources'])
                fetch_ranges.append({'calculation_node':series.get('sourceId'), **receipt['bounds']})
        series_scopes.append({
            'source_id':series.get('sourceId'), 'frequency':freq,
            'configured_display_period':{k:configured[k] for k in ('start','end') if k in configured},
            'displayed_observation_periods':[p['date'] for p in points],
            'first_displayed_observation_period':points[0]['date'],
            'last_displayed_observation_period':points[-1]['date'],
            'point_source_bindings':bindings,
            'fetch_periods':fetch_ranges,
            'catalog_available_periods':[{'table_id':tid, 'periods':tables[tid]['periods']}
                                         for tid in sorted(table_ids) if tid in tables],
        })
    if not series_scopes:
        return None
    return {'evidence_type':'verified_execution_period_scope',
        'period_roles':{
            'catalog_available_periods':'통계표 전체 수록기간. 이번 화면의 표시 시작 시점과 구분한다.',
            'fetch_periods':'실제 원자료 또는 계산의 조회기간. 선행 계산 이력을 포함할 수 있다.',
            'configured_display_period':'이번 실행에서 선택된 화면 표시 범위. 통계표 전체 수록기간이 아니다.',
            'displayed_observation_periods':'이번 구조화된 출력에 실제 포함된 관측 시점. 각 값과 시점은 원자료 또는 실행된 계산과 대조했다.',
            'first_displayed_observation_period':'이번 출력에 포함된 첫 관측 시점이며 자료 전체의 최초 공표 시점이 아니다.',
        }, 'series':series_scopes}
