"""Synthetic independent subjects, calculation names and display intervals."""
import copy
import pytest

from report_summary import explain, facts_for, display_number
from request_period import explicit_period
from derived_request_period import requested_display_period
from query_conditions import clarification_question
from derived_indicator_planner import needs_source_preflight


def observed(label, values=(1.56, 1.63, 1.64), **extra):
    return dict(label=label, unit='%p', frequency='M', sourceId='synthetic',
                points=[dict(date=d, value=v) for d, v in zip(['202702', '202703', '202704'], values)], **extra)


def test_component_values_keep_the_component_not_only_the_parent():
    data = [observed('전체대출 · 주택대출'), observed('전체대출 · 기타대출')]
    result = explain(data)['text']
    assert '전체대출 · 주택대출의' in result
    assert '전체대출 · 기타대출의' in result


def test_two_regions_and_a_calculation_window_survive_prose_formatting():
    label = '동부·서부 대출 차이의 당월·직전월 평균'
    data = [observed(label, (1.5599999999999998, 1.63, 1.64), calculation={'operator': 'rolling_mean'})]
    original = copy.deepcopy(data)
    result = explain(data, query='기간 추이')
    assert label + '의 2027년 4월 관측값은 1.64 %p입니다.' in result['text']
    assert '1.56 %p' in result['text'] and '0.08 %p' in result['text']
    assert '9999999' not in result['text']
    assert '표시 시작 시점' in result['text'] and '조회 시작 시점' not in result['text']
    assert data == original


def test_short_name_collision_preserves_both_adjustment_bases():
    data = [observed('경기지수(원계열)'), observed('경기지수(계절조정)')]
    result = explain(data)['text']
    assert '경기지수(원계열)의' in result and '경기지수(계절조정)의' in result


def test_rate_pace_explicitly_compares_adjacent_observations():
    data = [observed('고용의 전년동월 대비 변화율', (-2.0, -1.8, -1.7))]
    pace = next(f for f in facts_for(data) if f['kind'] == 'pace')
    assert '전월 대비 상승폭' in pace['text']


@pytest.mark.parametrize('query,start,end', [
    ('2027년 2월 수준에 비해 3월이 몇 퍼센트 달라졌는지 한 숫자로 보여줘.', '2027-03-01', '2027-03-31'),
    ('2027년 2월에서 3월로 이동한 폭을 %p 간격 하나로 보여줘.', '2027-03-01', '2027-03-31'),
    ('2027년 2월·3월·4월 금액을 더한 금액 하나를 보여줘.', '2027-04-01', '2027-04-30'),
    ('2027년 2~5월의 각 값을 1/4씩 반영한 한 숫자가 필요해.', '2027-05-01', '2027-05-31'),
    ('2027년 실질 GDP의 2분기를 기준으로 3분기에 몇 퍼센트 변했는지 숫자 하나로 보여줘.', '2027-07-01', '2027-09-30'),
    ('2027년 2월에 비해 3월이 몇 퍼센트 달라졌는지 한 숫자로 보여줘. 분모는 2월 수준이야.', '2027-03-01', '2027-03-31'),
    ('2027년 2분기 기준 3분기 변화율을 한 숫자로 보여줘. 기준은 2분기야.', '2027-07-01', '2027-09-30'),
])
def test_scalar_output_is_distinct_from_input_period(query, start, end):
    result = requested_display_period(query)
    assert (result['start'], result['end']) == (start, end)


def test_scalar_output_does_not_override_an_explicit_display_range():
    result = requested_display_period('표시 기간은 2027년 2월부터 2027년 5월까지. 각 달의 변화율을 숫자 하나씩 보여줘.')
    assert (result['start'], result['end']) == ('2027-02-01', '2027-05-31')


def test_undefined_aggregate_is_asked_but_rejected_aggregate_is_not():
    assert clarification_question('월별 값을 묶은 대표값 대신 각 월의 원계열을 유지해줘.', []) is None
    assert clarification_question('연간 대표값을 보여줘.', []) is not None


def test_ratio_wording_requires_real_observation_unit_preflight():
    assert needs_source_preflight('수입액을 1로 놓았을 때 수출액이 얼마에 해당하는지 계산해줘.')


def test_inherited_quarters_define_source_range_without_month_codes():
    result = explicit_period('2027년 실질 GDP의 2분기를 기준으로 3분기에 몇 퍼센트 변했는지 숫자 하나로 보여줘.')
    assert (result['start'], result['end']) == ('2027-04-01', '2027-09-30')


@pytest.mark.parametrize('value,expected', [
    (0.00999999999999978, '0.01'), (0.001, '0.001'),
    (0.0000000001234, '0.0000000001234'), (-0.00999999999999978, '-0.01'),
    ('1234567890123.45', '1,234,567,890,123.45'),
])
def test_prose_suppresses_roundoff_without_erasing_small_observations(value, expected):
    assert display_number(value) == expected
