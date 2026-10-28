"""Independent account/basis/region scopes, grounded in public metadata."""
import pytest
from catalog_classifications import refresh_classifications
from catalog_request_planner import selections
from derived_indicator_planner import is_derived_request
from request_match_guard import unsupported_quantity_terms
from stat_dictionary.stat_language_resolver import StatLanguageResolver
from statbridge_mcp.metadata_store import MetadataStore


@pytest.fixture(scope='module')
def tables():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    resolver = StatLanguageResolver(root/'src/agent/stat_dictionary/stat_language_dictionary.json')
    refresh_classifications(resolver.tables, MetadataStore())
    return resolver.tables


def test_two_money_bases_remain_independent(tables):
    query = ('2022년부터 2023년 M2 상품별 구성내역의 M2 총액(말잔 원계열)과 '
             'M2 상품별 구성내역의 M2 총액(평잔 계절조정계열)을 월별 표와 선그래프로 보여줘.')
    chosen = selections(query, tables)
    assert {t['table_name'] for t in chosen} == {
        'M2 상품별 구성내역(말잔 원계열)', 'M2 상품별 구성내역(평잔 계절조정계열)'}
    assert all(not t['_missing_dimensions'] for t in chosen)


def test_comparison_does_not_discard_an_unknown_measure(tables):
    query = ('2022년 M2 상품별 구성내역의 M2 총액(평잔 원계열)과 '
             '존재하지않는산업지수를 월별로 보여줘.')
    assert unsupported_quantity_terms(query, tables) == ['존재하지않는산업지수']
    assert not selections(query, tables)


def test_named_rate_accounts_are_not_unknown(tables):
    assert not unsupported_quantity_terms('차입금리와 저축성수신금리를 보여줘', tables)
    assert unsupported_quantity_terms('외계행성금리를 보여줘', tables) == ['외계행성금리']


def test_total_and_regional_accounts_use_metadata_supported_annual_frequency(tables):
    chosen = selections('2020년부터 2023년 국제수지 전체 경상수지와 지역별 일본 경상수지를 연간 표로 보여줘.', tables)
    assert {t['table_name'] for t in chosen} == {'국제수지', '지역별 경상수지'}
    assert all(t['_frequency'] in {'Y', 'A'} for t in chosen)


def test_published_aggregate_is_not_new_arithmetic():
    assert not is_derived_request('현금 발행 및 예수금 합계·제도부문 합계(거래표)를 연간 조회해줘.')
    assert is_derived_request('현금과 예수금을 합산해서 합계를 계산해줘.')


def test_sna_version_is_not_an_observation_year(tables):
    from request_period import explicit_period
    from canonical_request_planner import instruction_text
    query='2020년부터 2023년 상세자금순환표(2008 SNA)의 잔액표를 연간 조회해줘.'
    assert explicit_period(query)['start']=='2020-01-01'
    assert '2008' not in instruction_text(query,tables)
