"""Scope and arithmetic contracts with independent subjects/periods."""
import pytest
from query_semantics import active_request
from query_conditions import clarification_question
from derived_indicator_planner import is_derived_request,is_availability_check
from request_period import explicit_period


@pytest.mark.parametrize('query',[
    '보고서의 ‘비중과 증감률’ 절에 넣을 기초자료야. 2022년 월별 수출액을 그대로 보여줘.',
    '금리를 보여줘. 전월 대비 변화는 다음 작업에서 다룰 테니 잔액 기준 공표값만 가져와.',
    '수입물가지수 값을 보여줘. 계산하지 말고 이미 발표된 증가율을 가져와줘.',
    '매출액증가율 공표값을 조회해줘.',
])
def test_context_and_published_measure_are_not_new_arithmetic(query):
    assert not is_derived_request(query)


@pytest.mark.parametrize('query',[
    '다섯 달 각각의 값을 1/5씩 반영한 한 숫자를 만들어줘.',
    '지난달 금리를 1로 놓았을 때 이번달 금리가 얼마인지 보여줘.',
    '매출액의 가장 높은 점에서 가장 낮은 점까지 얼마나 떨어져 있는지 보여줘.',
    '첫 해를 100으로 놓고 새 눈금으로 표시해줘.',
])
def test_implicit_operations_are_calculation_requests(query):
    assert is_derived_request(query)


def test_rejected_operation_order_does_not_pause_positive_calculation():
    assert not is_availability_check('비율의 전월 변화율을 구한 뒤 이동평균을 계산해줘. 반대로 평균부터 계산하지 말아줘.')
    assert is_availability_check('비율과 이동평균을 계산할 자료를 확인해줘. 아직 계산하지 말아줘.')
    assert is_availability_check('원자료를 먼저 확인하고 아직 계산하지 말아줘.')
    assert is_availability_check('먼저 계산하지 말고 원자료를 확인해줘.')


def test_future_purpose_does_not_negate_a_current_calculation():
    assert is_derived_request('다음 작업을 위해 두 금리의 전월 변화율을 계산해줘.')


def test_balance_synonym_never_changes_the_requested_borrower():
    text=active_request('기업대출 금리를 각 달에 남아 있는 대출 잔액 전체 기준으로 보여줘.')
    assert '기업대출' in text and '가계대출' not in text


def test_missing_basis_ignores_candidate_list():
    question=clarification_question('예금은행 가계대출 금리를 조회해줘. 메모의 ‘신규취급액 / 잔액’은 선택지 목록이야.',[])
    assert '신규취급액' in question and '잔액' in question


def test_confirmed_balance_basis_is_not_asked_again():
    t=[{'table_name':'M2'}]
    question=clarification_question('M2 총액을 한 달 평균잔액 기준으로 조회해줘. 원계열·계절조정은 양식의 후보 목록이야.',t)
    assert '원계열' in question and '계절조정' in question
    assert '평잔과 말잔' not in question


def test_override_drops_only_the_old_currency():
    text=active_request('원화 기준이 기본값이야. 이번 표는 예외로 달러 기준으로 변경할게.')
    assert '원화 기준' not in text and '달러 기준' in text


@pytest.mark.parametrize('words',['첫 석 달','첫 세 달','첫 분기'])
def test_first_quarter_wording_is_not_a_full_year(words):
    p=explicit_period('2022년 '+words+'의 월별 자료를 보여줘')
    assert (p['start'],p['end'])==('2022-01-01','2022-03-31')


def test_table_vintage_does_not_widen_observation_period():
    p=explicit_period('2017년 이후 수록 자료에서 2021년과 2022년 연간 값을 조회해줘')
    assert (p['start'],p['end'])==('2021-01-01','2022-12-31')


def test_active_quoted_agreement_survives_a_later_rejected_proposal():
    text=active_request('분석 메모는 ‘M2 총액, 평잔, 원계열’이야. 검토하다 버린 말잔·계절조정안은 쓰지 말고 메모대로 진행해줘.')
    assert '평잔' in text and '원계열' in text and '말잔' not in text
    assert active_request(text)==text


def test_scope_normalization_is_idempotent_for_gdp_and_quarter_ranges():
    text=active_request('시장가격 GDP의 2022년 1~4분기를 보여줘.')
    assert active_request(text)==text


def test_source_search_matches_words_across_metadata_fields():
    from derived_availability import _search
    meta={'table_id':'DT_TEST','table_name':'예금은행 대출금리(신규취급액 기준)',
          'items':[],'dimensions':[{'values':[{'class_name':'가계대출'}]}]}
    assert _search({'tables':[meta]},['예금은행 가계대출 금리'])[0][0]['table_id']=='DT_TEST'
    assert not _search({'tables':[meta]},['주택 가격 지수'])
