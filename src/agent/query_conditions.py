"""Clarify unresolved data semantics before selecting a catalog series.

Rules describe statistical concepts, never evaluation cases or stored answers.
"""
import re
from query_semantics import active_request


def clarification_question(query, tables):
    query=active_request(query)
    if (re.search(r'수입물가|수출물가',query) and re.search(r'백분율|상승률|오르내렸',query)
            and not re.search(r'전월|전년|작년.*같은\s*달|지난.*같은\s*달|직전\s*달|바로\s*앞달',query)):
        return '변화율은 전월 대비와 전년 동월 대비 중 어느 비교 기준으로 계산할까요?'
    # Calculation planning owns its source/definition questions and recovery.
    from derived_indicator_planner import is_derived_request
    if is_derived_request(query):return None
    if re.search(r'(?<![0-9A-Za-z_])DT_[0-9A-Za-z]+(?![0-9A-Za-z_])',query):
        return None
    q = re.sub(r'\s+', '', query).lower()
    titles = ' '.join(t['table_name'] for t in tables)
    if '가계대출' in q and '금리' in q and not any(w in q for w in ('신규취급액','잔액')):
        return '가계대출 금리는 신규취급액과 잔액 중 어느 기준으로 조회할까요?'
    if 'm2' in q and 'M2' in titles:
        if not any(w in q for w in ('평잔', '말잔')):
            return 'M2 총액은 평잔과 말잔 중 어느 기준으로 조회할까요?'
        if not any(w in q for w in ('원계열', '계절조정')):
            return 'M2는 원계열과 계절조정계열 중 어느 쪽으로 조회할까요?'
    if '경제심리지수' in q and '경제심리지수' in titles and not any(w in q for w in ('원계열', '순환변동치')):
        return '경제심리지수 원계열과 순환변동치 중 어느 계열을 조회할까요?'
    if any(w in q for w in ('수입물가', '수출물가')) and not any(w in q for w in ('원화', '달러', '계약통화')):
        return '물가지수는 원화·달러·계약통화 중 어느 통화 기준으로 조회할까요?'
    if re.search(r'대표(?:값|숫자|하는값)',q) and not re.search(r'평균|연말|12월값|합계|합산|중앙값|최댓값|최솟값|1/\d+', q):
        return '연간 대표값은 월별 값의 단순평균과 연말 값 중 어떤 집계 기준을 원하시나요? 다른 기준도 지정할 수 있습니다.'
    return None


def active_conditions(query):
    """An explicit change replaces a previously described currency basis."""
    query=active_request(query)
    change=re.search(r'(?:이번에는|이번엔|이제는)\s*(원화|달러|계약통화)\s*기준.*?(?:바꿔|변경)',query)
    if not change:return query
    prefix=query[:change.start()]
    for currency in ('원화','달러','계약통화'):
        prefix=prefix.replace(currency+' 기준','이전 기준')
    return prefix+query[change.start():]
