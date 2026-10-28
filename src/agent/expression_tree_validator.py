"""Server-owned source, structure, calendar and dimensional checks."""
from statbridge_mcp.observability import observed
import re
from datetime import date
from expression_tree_schema import ExpressionPlan, MAX_SOURCES, MAX_OUTPUTS

# D=366 bounds the history fetch; daily yoy uses the same calendar date.
YEAR_LAGS = {'D': 366, 'M': 12, 'Q': 4, 'S': 2, 'Y': 1}
MAX_POINTS = 20000
MAX_CELLS = 500000
MAX_WORK = 2000000


def canonical_bound(value, frequency):
    # KOSIS observation codes include YYYYQn and YYYYHn as well as
    # YYYY01..04 / YYYY01..02. Only normalize a matching frequency.
    pattern = {'Q': r'(\d{4})Q([1-4])', 'S': r'(\d{4})H([1-2])'}.get(frequency)
    match = re.fullmatch(pattern, value) if pattern and isinstance(value, str) else None
    if match:
        return f'{match[1]}0{match[2]}'
    if not value or '-' not in value:
        return value
    parsed = date.fromisoformat(value)
    if frequency == 'D':
        return parsed.strftime('%Y%m%d')
    part = {'M': parsed.month, 'Q': (parsed.month - 1) // 3 + 1,
            'S': (parsed.month - 1) // 6 + 1, 'Y': 1}[frequency]
    return f'{parsed.year:04d}' + (f'{part:02d}' if frequency != 'Y' else '')


class DerivedValidationError(ValueError):
    def __init__(self, message, *, failure_kind='validation_error'):
        super().__init__(message)
        self.failure_kind = failure_kind


def period_index(period, frequency):
    if frequency == 'D':
        if not re.fullmatch(r'\d{8}', str(period)):
            raise DerivedValidationError(f'잘못된 D 기간: {period}')
        try:
            parsed = date(int(period[:4]), int(period[4:6]), int(period[6:]))
            if not 1900 <= parsed.year <= 2200: raise ValueError('year')
        except ValueError as exc:
            raise DerivedValidationError(f'유효하지 않은 일별 날짜: {period}') from exc
        return parsed.toordinal()
    count = YEAR_LAGS[frequency]
    if not re.fullmatch(r'\d{4}' if frequency == 'Y' else r'\d{6}', period):
        raise DerivedValidationError(f'잘못된 {frequency} 기간: {period}')
    year = int(period[:4])
    part = 1 if frequency == 'Y' else int(period[4:])
    if not 1900 <= year <= 2200 or not 1 <= part <= count:
        raise DerivedValidationError(f'기간 범위 오류: {period}')
    return year * count + part - 1


def shift_period(period, frequency, offset):
    index = period_index(period, frequency) + offset
    if frequency == 'D':
        try: result = date.fromordinal(index).strftime('%Y%m%d')
        except ValueError as exc: raise DerivedValidationError('일별 날짜 이동 범위를 초과했습니다.') from exc
        period_index(result, frequency)
        return result
    count = YEAR_LAGS[frequency]
    result = f'{index // count:04d}' + (f'{index % count + 1:02d}' if count > 1 else '')
    period_index(result, frequency)
    return result


def prior_year_period(period, frequency):
    if frequency != 'D':
        return shift_period(period, frequency, -YEAR_LAGS[frequency])
    period_index(period, frequency)
    current = date(int(period[:4]), int(period[4:6]), int(period[6:]))
    try: return current.replace(year=current.year - 1).strftime('%Y%m%d')
    except ValueError: return None  # Feb 29 has no same-date prior-year observation.


@observed('dag.validate', 'span')
def validate_plan(plan: ExpressionPlan, catalog):
    if not 1 <= len(plan.sources) <= MAX_SOURCES or not 1 <= len(plan.outputs) <= MAX_OUTPUTS:
        raise DerivedValidationError(f'입력 및 출력 시계열은 각각 최대 {MAX_SOURCES}개입니다.')
    ids, depth, history = set(), {}, {}
    frequencies = {s.frequency for s in plan.sources}
    if len(frequencies) != 1:
        raise DerivedValidationError('주기가 다른 원자료는 결합할 수 없습니다. 명시적인 주기 변환이 필요합니다.')
    frequency = next(iter(frequencies))
    first, last = period_index(plan.start_period, frequency), period_index(plan.end_period, frequency)
    if first > last or last - first + 1 > MAX_POINTS:
        raise DerivedValidationError('조회기간 순서 또는 계산량 한도를 확인해 주세요.')
    identities = set()
    for source in plan.sources:
        catalog.validate_source_selection(source)
        identity = (source.table_id, source.item_id, tuple(sorted(source.classifications.items())), source.frequency)
        if source.alias in ids or identity in identities:
            raise DerivedValidationError('중복된 입력 ID 또는 시계열입니다.')
        identities.add(identity)
        ids.add(source.alias)
        depth[source.alias], history[source.alias] = 0, 0
    work = 0
    for node in plan.nodes:
        if node.id in ids or any(ref not in ids for ref in node.inputs):
            raise DerivedValidationError('중복 ID, 순환 또는 정의되지 않은 계산 입력입니다.')
        depth[node.id] = 1 + max(depth[ref] for ref in node.inputs)
        if depth[node.id] > 12:
            raise DerivedValidationError('계산식 중첩 깊이가 12를 초과했습니다.')
        extra = YEAR_LAGS[frequency] if node.operator == 'yoy_pct' else (node.lag or 0)
        if node.window:
            extra = node.window - 1
        history[node.id] = max(history[ref] for ref in node.inputs) + extra
        if node.base_period:
            base = period_index(node.base_period, frequency)
            if base > last:
                raise DerivedValidationError('기준기간이 표시 종료기간보다 뒤에 있습니다.')
        if node.operator == 'period_change':
            if frequency != 'D':
                raise DerivedValidationError('기간 비교에는 일별 원자료를 연결해 주세요.')
            ends=set();labels=set()
            for p in node.periods:
                if not first <= period_index(p.start,'D') < period_index(p.end,'D') <= last:
                    raise DerivedValidationError('비교기간은 조회 범위 내에서 시작일보다 종료일이 뒤여야 합니다.')
                if p.end in ends or p.label in labels:
                    raise DerivedValidationError('중복 비교기간입니다.')
                ends.add(p.end);labels.add(p.label)
        work += (last - first + 1 + history[node.id]) * (node.window or 1)
        ids.add(node.id)
    if any(out.node not in ids for out in plan.outputs):
        raise DerivedValidationError('출력은 검증된 원자료 또는 계산 노드를 참조해야 합니다.')
    if len({out.label for out in plan.outputs}) != len(plan.outputs):
        raise DerivedValidationError('출력 계열 이름은 서로 달라야 합니다.')
    if not any(out.display != 'table' for out in plan.outputs):
        raise DerivedValidationError('그래프에 표시할 출력 계열이 필요합니다.')
    if plan.category_comparison:
        outputs=[o.node for o in plan.outputs]
        nodes={n.id:n for n in plan.nodes}
        if any(o not in nodes for o in outputs):
            raise DerivedValidationError('국가별 비교 출력은 기간 비교 계산 노드를 참조해야 합니다.')
        selected=[nodes[o] for o in outputs]
        if (len(set(outputs))!=len(outputs) or any(n.operator!='period_change' for n in selected)
                or len({str(n.periods) for n in selected})!=1
                or not set(plan.category_comparison.average_nodes)<=set(outputs)
                or len(set(plan.category_comparison.average_nodes))!=len(plan.category_comparison.average_nodes)):
            raise DerivedValidationError('국가별 비교 출력과 평균 대상·기간이 일치하지 않습니다.')
    if work > MAX_WORK:
        raise DerivedValidationError('계산 작업량 한도를 초과했습니다.')
    lookback = max(history.values())
    bases = [shift_period(n.base_period, frequency, -max(history[ref] for ref in n.inputs))
             for n in plan.nodes if n.base_period]
    fetch_start = min([shift_period(plan.start_period, frequency, -lookback), *bases])
    points = last - period_index(fetch_start, frequency) + 1
    if points > MAX_POINTS or points * (len(plan.sources) + len(plan.nodes)) > MAX_CELLS:
        raise DerivedValidationError('원자료 및 중간 계산 메모리 한도를 초과했습니다.')
    return {'frequency': frequency, 'fetch_start': fetch_start, 'fetch_end': plan.end_period,
            'lookback': lookback, 'estimated_cells': points * (len(plan.sources) + len(plan.nodes)), 'work': work}


def result_unit(operator, units, exponent=None):
    from report_summary import difference_unit
    def index_unit(unit):
        # KOSIS also publishes indices with a reference month, e.g. 2025.12=100.
        return unit in {'지수', '지수(기준=100)'} or bool(re.fullmatch(
            r'\(?\d{4}(?:\.(?:0?[1-9]|1[0-2]))?\s*[=＝]\s*100\)?', unit))
    def difference(unit):
        return '포인트' if index_unit(unit) else difference_unit(unit)
    if operator in {'add', 'subtract'}:
        if len(set(units)) != 1 or not units[0]:
            raise DerivedValidationError('덧셈·뺄셈에는 동일하고 확인된 단위가 필요합니다.')
        return difference(units[0]) if operator == 'subtract' else units[0]
    if operator in {'pct_change', 'yoy_pct', 'period_change', 'base_change'}:
        if difference_unit(units[0]) == '%p':
            raise DerivedValidationError('비율의 증가율은 의미가 모호합니다. 차이(%p)인지 확인해 주세요.')
        return '%'
    if operator == 'rebase_100':
        return '지수(기준=100)'
    if operator == 'divide':
        return '배' if units[0] and units[0] == units[1] else f'({units[0]})/({units[1]})'
    if operator == 'percent_ratio':
        if len(set(units)) != 1 or not units[0]:
            raise DerivedValidationError('백분율 비율에는 동일하고 확인된 단위가 필요합니다.')
        return '%'
    if operator == 'multiply':
        return f'({units[0]})·({units[1]})'
    if operator == 'inverse':
        return f'1/({units[0]})'
    if operator == 'power':
        return f'({units[0]})^{exponent}'
    if operator == 'log':
        if units[0] not in {'배', '1', '무차원'} and not index_unit(units[0]):
            raise DerivedValidationError('로그에는 기준이 확인된 무차원 값 또는 지수가 필요합니다.')
        return '로그'
    return difference(units[0]) if operator == 'difference' else units[0]
