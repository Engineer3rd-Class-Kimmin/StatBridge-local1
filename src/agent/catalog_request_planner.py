"""Ground measured concepts in catalog titles and retain each requested dimension.

No benchmark queries, IDs, answers or response fixtures are loaded here.
"""
from itertools import product
import re
from request_match_guard import structure_request, unsupported_quantity_terms

def compact(text):
    return re.sub('[^a-z0-9가-힣]', '', str(text).lower())

def normalize(query):
    q = compact(query)
    q = q.replace('수출수입물가지수', '수출물가지수수입물가지수')
    if '서비스수지' in q:
        q = re.sub(r'(?<!서비스)수입', '서비스수입', q)
        q = re.sub(r'(?<!서비스)지급', '서비스지급', q)
    q = q.replace('계절조정된', '계절조정')
    return q

def unavailable(query, tables):
    q = compact(query)
    names = ' '.join((compact(t['table_name']) for t in tables))
    for term in ('연체율', '전망치'):
        if term in q and term not in names:
            return term
    if '금리' in q and any((t in q for t in ('국고채', '회사채'))) and (not any((t in names for t in ('국고채금리', '회사채금리')))):
        return '채권 금리'
    return None

def domains(query):
    q = normalize(query)
    wanted = []
    for word in ('생산자물가', '수출물가', '수입물가', '수출물량', '수입물량'):
        if word in q:
            wanted.append((word, word))
    if wanted:
        return wanted
    if '가계대출' in q and '판매신용' not in q and ('신규' not in q) and ('금리' not in q) and any((w in q for w in ('예금은행', '비은행예금취급기관'))):
        return [('가계대출', '예금취급기관가계대출')]
    if '판매신용' in q and '가계대출' in q:
        return [('가계신용', '가계신용')]
    if '대출' in q and any((w in q for w in ('제조업', '부동산업', '산업별', '업종별'))):
        return [('산업대출', '산업별대출금')]
    if '기여' in q and any((w in q for w in ('gdp', '성장', '내수', '순수출'))):
        return [('성장기여도', '지출항목별성장기여도')]
    if '계절조정' in q and '실질' in q and any((w in q for w in ('최종소비지출', '민간소비', '정부소비', '건설투자', '설비투자'))):
        return [('GDP지출', '국내총생산에대한지출')]
    if any((w in q for w in ('상품수지', '서비스수지', '본원소득수지', '여행수지', '운송수지'))):
        return [('국제수지', '국제수지')]
    if '계절조정' in q and '경상수지' in q:
        return [('경상수지', '경상수지')]
    if any((w in q for w in ('총액결제시스템', '소액결제시스템'))):
        return [('결제시스템', '결제시스템별')]
    if '외환동시결제' in q:
        return [('외환결제', '한은금융망결제통계')]
    if '한은금융망' in q:
        return [('금융망', '한은금융망')]
    if any((w in q for w in ('계좌이체', '신용카드', '체크카드'))) and any((w in q for w in ('건수', '금액', '이용'))):
        return [('지급수단', '지급수단별')]
    return []

def dimension_values(query, dimension):
    q = normalize(query)
    matches = []
    for value in dimension.get('values', []):
        name = compact(value['value_name'])
        terms = {name}
        if name.startswith('처리'):
            terms.add(name[2:])
        if name in {'민간', '정부'}:
            terms.update({name + '소비', name + '최종소비지출'})
        for term in terms:
            if len(term) < 2:
                continue
            for match in re.finditer(re.escape(term), q):
                if q[match.end():].startswith('에서'):
                    continue
                matches.append((match.start(), match.end(), value, term))
    kept = [m for m in matches if not any((n[0] <= m[0] and n[1] >= m[1] and (n[1] - n[0] > m[1] - m[0]) for n in matches))]
    bycode = {m[2]['value_id']: m[2] for m in sorted(kept, key=lambda m: m[0])}
    return list(bycode.values())

def selections(query, tables):
    q = normalize(query)
    result = []
    # Explicit full table titles own their qualifiers in the canonical resolver.
    # Bare subcategory words (e.g. 신용카드) alone do not establish that boundary.
    named = {t['table_name'] for t in tables if compact(t['table_name']) in q}
    if len(named) > 1 or any('(' in name for name in named):
        return []
    # Contract currency is a price-index valuation basis, not money supply.
    requested = structure_request(q.replace('계약통화', '계약기준'))
    requested_domains = domains(query)
    if structure_request(query)['explicit_ids'] or unavailable(query, tables) or unsupported_quantity_terms(query, tables):
        return []
    covered = set()
    domain_metrics = {'가계대출': {'대출'}, '가계신용': {'대출'}, '산업대출': {'대출'}, '성장기여도': {'GDP', '수출'}, 'GDP지출': {'GDP'}, '국제수지': {'수출', '수입'}, '경상수지': set(), '지급수단': {'신용카드'}, '결제시스템': set(), '금융망': set(), '외환결제': set()}
    for domain, _ in requested_domains:
        covered.update(domain_metrics.get(domain, {domain}))
    if set(requested['metrics']) - covered:
        return []
    for domain, anchor in requested_domains:
        ranked = []
        for table in tables:
            title = compact(table['table_name'])
            if anchor not in title:
                continue
            frequency = table.get('prd_se')
            if '일별' in q and frequency != 'D':
                continue
            if '시간별' in q and frequency != 'H':
                continue
            if any((w in q for w in ('월별', '매월', '월마다'))) and frequency != 'M':
                continue
            if any((w in q for w in ('분기별', '분기마다'))) and frequency != 'Q':
                continue
            if any((w in q for w in ('연간', '연도별', '매년'))) and frequency not in {'A', 'Y'}:
                continue
            if '지역별' in q and '지역' not in title:
                continue
            if any((w in q for w in ('국가별', '나라별'))) and '국가' not in title:
                continue
            if '명목' in q and '명목' not in title:
                continue
            if '실질' in q and '실질' not in title:
                continue
            if '원계열' in q and '계절조정' in title:
                continue
            if '계절조정' in q and '계절조정' not in title:
                continue
            if domain in {'GDP지출', '성장기여도'} and (not ('계절조정' in title and '실질' in title)):
                continue
            if domain == '산업대출' and '예금은행' in q and ('비은행' in title or '예금은행' not in title):
                continue
            groups = [dimension_values(query, d) for d in table.get('dimensions', [])]
            coverage = sum((len(v) for v in groups))
            if not coverage:
                continue
            score = coverage * 100
            if '지역' in title and '지역' not in q:
                score -= 200
            if '업권별' in title and domain in {'가계대출', '가계신용'}:
                score += 50
            if '전산업' in title and domain == '산업대출':
                score += 50
            if '총지수' in q and '기본분류' in title:
                score += 50
            if domain == '금융망' and title == '한은금융망':
                score += 50
            ranked.append((score, table, groups))
        ranked.sort(key=lambda x: -x[0])
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return []
        _, table, groups = ranked[0]
        choices = [values or [None] for values in groups]
        for combo in product(*choices):
            hits = [{'api_param': d['api_param'], 'value_id': v['value_id'], 'value_name': v['value_name']} for d, v in zip(table['dimensions'], combo) if v]
            result.append({'table_id': table['table_id'], 'table_name': table['table_name'], 'dimension_hits': hits, 'score': 300, 'reasons': ['catalog_measured_concept:' + domain]})
    return result
