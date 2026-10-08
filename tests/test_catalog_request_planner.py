"""Catalog-grounded paraphrases and fail-closed multi-series regressions."""
import copy
from types import SimpleNamespace

import pytest

from agent_runtime import StatBridgeAgent
from catalog_request_planner import selections


@pytest.fixture(scope='module')
def agent():
    return StatBridgeAgent(object(), ncp_client=SimpleNamespace(configured=False))


def categories(agent, result):
    return [[h['value_name'] for h in item['dimension_hits']] for item in result]


@pytest.mark.parametrize('query,expected', [
    ('자동차와 반도체 수출물량을 월마다 비교할 자료', [['자동차'], ['반도체']]),
    ('가계신용에서 판매신용과 가계대출을 분기마다 비교', [['판매신용'], ['가계대출']]),
    ('민간 최종소비지출과 설비투자의 실질 계절조정 성장기여도를 분기별로 비교', [['민간'], ['설비투자']]),
    ('계좌이체의 금액과 건수를 매월 비교', [['계좌이체', '금액'], ['계좌이체', '건수']]),
])
def test_retains_each_requested_category_in_request_order(agent, query, expected):
    chosen = selections(query, agent.resolver.tables)
    assert categories(agent, chosen) == expected
    assert agent.resolve(query)['status'] == 'resolved'


def test_context_total_and_child_are_not_both_selected(agent):
    chosen = selections('서비스수지에서 지급과 수입을 월별로 보여줘', agent.resolver.tables)
    assert categories(agent, chosen) == [['서비스지급'], ['서비스수입']]


def test_contract_currency_does_not_request_a_money_supply_series(agent):
    query = '계약통화 수입물가지수 총지수와 수출물가지수 총지수를 매월 비교'
    chosen = selections(query, agent.resolver.tables)
    assert len(chosen) == 2
    assert all('물가' in item['table_name'] for item in chosen)
    assert agent.resolve(query)['status'] == 'resolved'


def test_period_reentry_retains_each_grounded_series_without_model(agent):
    query = '계좌이체 건수와 금액을 월별로 비교'
    first = agent.resolve(query)
    again = agent.resolve(query, state=first['state'])
    assert first['status'] == again['status'] == 'resolved'
    assert len(again['api_plans']) == 2
    assert first['api_plans'] == again['api_plans']


@pytest.mark.parametrize('query', [
    '신용카드와 체크카드 금액 그리고 한국은행 기준금리',
    '신용카드 금액과 소비자물가지수',
    '반도체 수출물량 DT_132Y001',
    '명목 GDP 성장기여도 내수와 순수출',
    '지역별 체크카드와 신용카드 금액',
    '반도체와 자동차 수출물량을 일별로',
])
def test_incompatible_or_incomplete_requests_do_not_take_grounded_path(agent, query):
    assert selections(query, agent.resolver.tables) == []


@pytest.mark.parametrize('query', [
    '기업대출 연체율을 보여줘', '실질 GDP 전망치와 실적을 비교',
    '회사채 금리와 국고채 금리 비교',
])
def test_unsupported_measure_is_not_replaced_by_a_nearby_table(agent, query):
    assert agent.resolve(query)['status'] == 'no_match'


@pytest.mark.parametrize('mutation', ['omit', 'extra', 'title', 'code', 'confirmed'])
def test_revalidation_blocks_incomplete_or_tampered_grounded_plans(agent, mutation):
    query = '반도체와 자동차 수출물량을 월별 비교'
    result = copy.deepcopy(agent.resolve(query))
    assert result['status'] == 'resolved'
    if mutation == 'omit':
        result['api_plans'].pop()
    elif mutation == 'extra':
        result['api_plans'].append(copy.deepcopy(result['api_plans'][0]))
    elif mutation == 'title':
        result['api_plans'][0]['table_name'] = '다른 표'
    elif mutation == 'code':
        result['api_plans'][0]['exact_params']['objL1'] = 'invented'
    else:
        table = result['api_plans'][0]['table_name'].split('(')[0]
        result['state']['confirmed'] = {'canonical_table:' + table: 'DT_132Y001'}
    checked = agent.validate_resolution(query, result)
    assert checked['status'] == 'no_match'
    assert checked['api_plans'] == []
    assert checked['request_validation']['valid'] is False
