"""Bounded calendar-keyed arithmetic; no eval/exec and no network access."""
from statbridge_mcp.observability import observed, span, annotate
import math
from decimal import Decimal, localcontext
from dataclasses import dataclass
from expression_tree_validator import (DerivedValidationError, MAX_POINTS, MAX_CELLS, MAX_WORK,
                                       YEAR_LAGS, period_index, shift_period, prior_year_period, result_unit, canonical_bound)


@dataclass
class Series:
    values: dict[str, float | None]
    unit: str
    frequency: str
    source_ids: list[str]


def finite(value):
    if isinstance(value, bool):
        raise DerivedValidationError('관측값에 논리값을 사용할 수 없습니다.')
    if value is None or str(value).strip() in {'', '-', '..', '...', 'NA', 'N/A'}:
        return None
    try:
        number = float(str(value).replace(',', ''))
    except (ValueError, TypeError) as exc:
        raise DerivedValidationError('숫자가 아닌 원자료가 있습니다.') from exc
    if not math.isfinite(number):
        raise DerivedValidationError('NaN 또는 무한대 원자료는 사용할 수 없습니다.')
    return number


def source_series(source, response):
    if response.get('status') != 'success' or response.get('source') != 'kosis_api' or response.get('table_id') != source.table_id:
        raise DerivedValidationError(f'{source.required_data}: KOSIS API 원자료 조회에 실패했습니다. 자료 부재로 단정할 수 없습니다.', failure_kind='source_api_error')
    rows = response.get('rows') or []
    if len(rows) > MAX_POINTS:
        raise DerivedValidationError('입력 관측 수 한도를 초과했습니다.')
    values, units = {}, set()
    for row in rows:
        if str(row.get('TBL_ID')) != source.table_id or str(row.get('ITM_ID')) != source.item_id or str(row.get('ORG_ID')) != '301':
            raise DerivedValidationError('조회 응답의 기관·통계표·항목이 요청과 다릅니다.')
        for key, selected in source.classifications.items():
            index = key.replace('objL', '')
            if str(row.get(f'C{index}')) != selected:
                raise DerivedValidationError('조회 응답의 분류가 요청과 다릅니다.')
        # KOSIS uses A for annual and H for half-year in some responses.
        actual_frequency = {'A': 'Y', 'H': 'S'}.get(row.get('PRD_SE'), row.get('PRD_SE'))
        if actual_frequency != source.frequency:
            raise DerivedValidationError('조회 응답의 주기가 요청과 다릅니다.')
        period = canonical_bound(str(row.get('PRD_DE') or ''), source.frequency)
        period_index(period, source.frequency)
        if period in values:
            raise DerivedValidationError(f'{source.alias}: 중복 관측 시점 {period}')
        values[period] = finite(row.get('DT'))
        units.add(str(row.get('UNIT_NM') or '').strip())
    if not values or not any(v is not None for v in values.values()):
        raise DerivedValidationError(f'{source.required_data}: 요청 기간의 실제 관측값이 없습니다.', failure_kind='source_data_absent')
    if units == {''}:
        from verified_source_units import unit_resolution
        resolution=unit_resolution(source,response)
        if resolution:units={resolution['unit']}
    if len(units) != 1 or not next(iter(units)):
        raise DerivedValidationError(f'{source.required_data}: 원자료 단위를 확정할 수 없습니다. 자료 제공기관의 단위 메타데이터를 확인한 뒤 같은 요청을 다시 시도해 주세요.',
                                     failure_kind='source_metadata_incomplete')
    return Series(values, next(iter(units)), source.frequency, [source.alias])


def period_change(item, node):
    """Bounded period reductions; never interpolate, backfill or invent prices."""
    values={};details=[]
    with localcontext() as ctx:
        ctx.prec=36
        for period in node.periods:
            groups=[]
            for bound in (period.start,period.end):
                rows=[(p,v) for p,v in sorted(item.values.items())
                      if (p==bound if period.basis=='exact' else p[:6]==bound[:6]) and v is not None]
                if not rows:
                    raise DerivedValidationError(f'{period.label}: {bound}의 실제 관측값이 없습니다. 날짜 또는 원자료를 확인해 주세요.')
                if any(v<=0 for _,v in rows):
                    raise DerivedValidationError('기간 변화율의 입력은 양수여야 합니다.')
                mean=sum((Decimal(str(v)) for _,v in rows),Decimal(0))/len(rows)
                groups.append((mean,rows))
            start,end=groups[0][0],groups[1][0]
            value=((end/start if node.direction=='direct' else start/end)-1)*100
            number=float(value)
            if not math.isfinite(number):raise DerivedValidationError('기간 변화율이 유효한 수치 범위를 초과했습니다.')
            values[period.end]=number
            details.append({'label':period.label,'basis':period.basis,'direction':node.direction,
                'start':period.start,'end':period.end,'start_value':str(start),'end_value':str(end),
                'start_observations':[p for p,_ in groups[0][1]],'end_observations':[p for p,_ in groups[1][1]],'result':str(value)})
    return values,details


class DynamicExpressionExecutor:
    @observed('dag.execute', 'span')
    def execute(self, plan, inputs, *, cached=None, changed=None):
        series = dict(inputs)
        issues, calculations = [], {}
        dirty = set(changed or [])
        cells = sum(len(s.values) for s in series.values())
        work = 0
        cached_count = 0
        for node in plan.nodes:
            with span('dag.node'):
                annotate(operation=node.operator)
                if any(ref in dirty for ref in node.inputs):
                    dirty.add(node.id)
                if cached and node.id not in dirty and node.id in cached:
                    cached_count += 1
                    annotate(cache_hit=True,unit=cached[node.id].unit,frequency=cached[node.id].frequency)
                    series[node.id] = cached[node.id]
                    calculations[node.id] = {'label': node.id, 'operator': node.operator, 'inputs': node.inputs,
                        'parameters': node.model_dump(exclude={'id', 'operator', 'inputs'}, exclude_none=True)}
                    continue
                args = [series[ref] for ref in node.inputs]
                if len({a.frequency for a in args}) != 1:
                    raise DerivedValidationError('주기가 다른 계열의 결합은 차단됩니다.')
                freq = args[0].frequency
                unit = result_unit(node.operator, [a.unit for a in args], node.exponent)
                annotate(unit=unit,frequency=freq)
                if node.operator=='period_change':
                    values,details=period_change(args[0],node)
                    series[node.id]=Series(values,unit,freq,args[0].source_ids)
                    calculations[node.id]={'label':node.id,'operator':node.operator,'inputs':node.inputs,
                        'parameters':node.model_dump(exclude={'id','operator','inputs'},exclude_none=True),'period_details':details}
                    cells+=len(values);work+=len(args[0].values)*len(node.periods)
                    if cells>MAX_CELLS or work>MAX_WORK:raise DerivedValidationError('기간 비교 계산량 한도를 초과했습니다.')
                    continue
                # Include all calendar positions so lag/rolling never bridge gaps.
                starts = [min(a.values) for a in args]
                ends = [max(a.values) for a in args]
                start, end = min(*starts, plan.start_period), max(*ends, plan.end_period)
                count = period_index(end, freq) - period_index(start, freq) + 1
                if count > MAX_POINTS or cells + count > MAX_CELLS:
                    raise DerivedValidationError('실제 계산 메모리 한도를 초과했습니다.')
                cells += count
                work += count * (node.window or 1)
                if work > MAX_WORK:
                    raise DerivedValidationError('실제 계산 작업량 한도를 초과했습니다.')
                values = {}
                accumulator, cumulative_valid = 0.0, True
                for offset in range(count):
                    period = shift_period(start, freq, offset)
                    x = args[0].values.get(period)
                    y = args[1].values.get(period) if len(args) == 2 else None
                    value, reason = None, None
                    missing_periods = []
                    op = node.operator
                    try:
                        if op == 'lag':
                            value = args[0].values.get(shift_period(period, freq, -node.lag))
                        elif op == 'cumulative_sum':
                            # The cumulative domain is explicitly the displayed interval.
                            if period < plan.start_period:
                                value = None
                            else:
                                if x is None:
                                    cumulative_valid = False
                                if cumulative_valid:
                                    accumulator += x
                                    value = accumulator
                        elif op == 'rolling_mean':
                            window_periods = [shift_period(period, freq, -k) for k in range(node.window)]
                            window = [args[0].values.get(p) for p in window_periods]
                            missing_periods = [p for p,v in zip(window_periods,window) if v is None]
                            if all(v is not None for v in window):
                                value = sum(window) / node.window
                        elif x is not None:
                            if op in {'add', 'subtract', 'multiply', 'divide', 'percent_ratio'} and y is not None:
                                if op == 'add': value = x + y
                                elif op == 'subtract': value = x - y
                                elif op == 'multiply': value = x * y
                                else: value = x / y * (100 if op == 'percent_ratio' else 1)
                            elif op in {'pct_change', 'yoy_pct', 'difference'}:
                                prior_period = prior_year_period(period, freq) if op == 'yoy_pct' else shift_period(period, freq, -node.lag)
                                prior = args[0].values.get(prior_period)
                                if prior is not None:
                                    value = x - prior if op == 'difference' else (x / prior - 1) * 100
                            elif op in {'rebase_100', 'base_change'}:
                                base = args[0].values.get(node.base_period)
                                if base is not None:
                                    value = (x / base - (1 if op == 'base_change' else 0)) * 100
                            elif op == 'inverse': value = 1 / x
                            elif op == 'log': value = math.log(x)
                            elif op == 'power':
                                if x < 0 and float(node.exponent) % 1:
                                    raise ValueError('실수 범위에서 정의되지 않은 거듭제곱입니다.')
                                value = x ** node.exponent
                    except ZeroDivisionError:
                        reason = 'zero_division'
                    except (ValueError, OverflowError):
                        reason = 'domain_or_overflow'
                    if value is not None and not math.isfinite(value):
                        value, reason = None, 'non_finite'
                    values[period] = value
                    if value is None and plan.start_period <= period <= plan.end_period:
                        issue={'node': node.id, 'period': period, 'reason': reason or 'missing_input_or_history'}
                        if missing_periods:issue['missing_periods']=sorted(missing_periods)
                        issues.append(issue)
                series[node.id] = Series(values, unit, freq, sorted({s for a in args for s in a.source_ids}))
                calculations[node.id] = {'label': node.id, 'operator': node.operator, 'inputs': node.inputs,
                                         'parameters': node.model_dump(exclude={'id', 'operator', 'inputs'}, exclude_none=True)}
        rows = []
        for output in plan.outputs:
            item = series[output.node]
            selected = [(p, v) for p, v in sorted(item.values.items()) if plan.start_period <= p <= plan.end_period and v is not None]
            if not selected:
                detail = ', '.join(f"{i['period']}({i['reason']}"+
                    (f"; 부족 시점: {', '.join(i['missing_periods'])}" if i.get('missing_periods') else '')+
                    ')' for i in issues if i['node'] == output.node)[:500]
                raise DerivedValidationError(f'{output.label}: 계산 가능한 관측값이 없습니다. 원자료·비교기간·분모를 확인해 주세요. {detail}')
            for period, value in selected:
                rows.append({'PRD_DE': period, 'DT': value, 'ITM_NM': output.label, 'UNIT_NM': item.unit,
                             '_SERIES_LABEL': output.label, '_SOURCE_SERIES_ID': output.node,
                             '_FREQUENCY': item.frequency, '_DERIVED_CALCULATION': output.node})
        annotate(row_count=len(rows),issue_count=len(issues),cached_nodes=cached_count,work_units=work,cells=cells)
        return {'rows': rows, 'issues': issues, 'series': series, 'calculations': calculations}
