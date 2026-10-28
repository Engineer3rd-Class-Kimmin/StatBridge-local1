"""Ground measured concepts in catalog titles and retain each requested dimension.

No benchmark queries, IDs, answers or response fixtures are loaded here.
"""
from itertools import product
import re
from request_match_guard import structure_request, unsupported_quantity_terms, missing_requested_scopes
from canonical_request_planner import selections as canonical_selections

def compact(text):
    return re.sub('[^a-z0-9가-힣]', '', str(text).lower())

def normalize(query):
    q = compact(query)
    q = q.replace('수출수입물가지수', '수출물가지수수입물가지수')
    if '서비스수지' in q:
        q = re.sub(r'(?<!서비스)수입', '서비스수입', q)
        q = re.sub(r'(?<!서비스)지급', '서비스지급', q)
    q = q.replace('계절조정된', '계절조정').replace('계절요인을제거한','계절조정')
    return q

def unavailable(query, tables):
    q = compact(query)
    names = ' '.join((compact(t['table_name']) for t in tables))
    measured=' '.join(compact(value) for t in tables for value in [t['table_name'],*t.get('item_names',[]),*[v['value_name'] for d in t.get('dimensions',[]) for v in d.get('values',[])]])
    # A borrower/population qualifier cannot be substituted by a nearby loan or survey series.
    for subject in ('취약차주','다주택자','자영업자','소상공인','예금자보호','예금보호'):
        if subject in q:
            scoped=measured
            if '대출' in q:
                scoped=' '.join(compact(value) for t in tables if any(w in compact(t['table_name']) for w in ['대출','가계신용']) for value in [t['table_name'],*[v['value_name'] for d in t.get('dimensions',[]) for v in d.get('values',[])]])
            if any(w in q for w in ['심리','체감경기']):
                scoped=' '.join(compact(value) for t in tables if any(w in compact(t['table_name']) for w in ['심리','경기','소비자동향']) for value in [t['table_name'],*[v['value_name'] for d in t.get('dimensions',[]) for v in d.get('values',[])]])
            if subject not in scoped:return subject
    if '가계부채' in q and 'gdp' in q and any(w in q for w in ['비율','대비']) and not any('가계부채' in compact(t['table_name']) and 'gdp' in compact(t['table_name']) for t in tables):return 'GDP 대비 가계부채 비율'
    if '주택시장' in q and '심리' in q and not any('주택시장' in compact(t['table_name']) and '심리' in compact(t['table_name']) for t in tables):return '주택시장 심리 지표'
    for term in ('연체율','고정이하여신비율','전망치','총부채원리금상환비율'):
        if term in q and term not in names:
            return term
    if any(word in q for word in ['미국','미시간','유로지역','글로벌']) and any(word in q for word in ['경제심리','소비심리','기대인플레이션']):
        matched=[t for t in tables if any('sentiment' in d for d in t.get('domains',[])) and any(word in compact(t['table_name']) for word in ['국가별','지역별']) and any(word in ' '.join(compact(v['value_name']) for dim in t['dimensions'] for v in dim['values']) for word in ['미국','유로지역'])]
        if not matched:return '요청하신 국가·조사의 심리 지표'
    if '금리' in q and any((t in q for t in ('국고채', '회사채'))) and (not any((t in names for t in ('국고채금리', '회사채금리')))):
        return '채권 금리'
    return None

def domains(query):
    q = normalize(query)
    wanted = []
    for word in ('생산자물가', '수출물가', '수입물가', '수출물량', '수입물량','수출금액','수입금액'):
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
        return ([('GDP지출','국내총생산에대한지출')] if '규모' in q else [])+[('성장기여도', '지출항목별성장기여도')]
    if '계절조정' in q and '실질' in q and '재화' in q and '서비스' in q and ('수출' in q or '수입' in q):return [('GDP지출','국내총생산에대한지출')]
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
    from semantic_catalog_planner import matches
    return matches(query,dimension)


def legacy_selections(query, tables):
    # Derive shared-name variants from every canonical catalog title.
    families = {}
    for table in tables:
        match = re.fullmatch(r'(.+?)\s*\(([^()]+)\)', table['table_name'])
        if match:
            base, suffix = match.groups()
            families.setdefault(compact(base), {}).setdefault(re.sub(r'\s+', '', suffix).lower(), []).append(table)
    expanded = query
    changed = False
    for base, variants in sorted(families.items(), key=lambda pair: -len(pair[0])):
        if len(variants) < 2:
            continue
        base_pattern = r'[\s·/,.~]*'.join(map(re.escape, base))
        suffix_pattern = '|'.join(r'\s*'.join(map(re.escape,v)) for v in sorted(variants, key=len, reverse=True))
        pattern = rf'{base_pattern}\s*((?:{suffix_pattern})(?:\s*(?:,|/|·|및|와|과|그리고)\s*(?:{suffix_pattern}))+)(?![가-힣])'
        for match in reversed(list(re.finditer(pattern, expanded, re.I))):
            suffixes = [re.sub(r'\s+', '', v).lower() for v in re.findall(suffix_pattern, match.group(1), re.I)]
            if any(len(variants[v]) != 1 for v in suffixes):
                return []
            names = ['「' + variants[v][0]['table_name'] + '」' for v in dict.fromkeys(suffixes)]
            expanded = expanded[:match.start()] + ' 그리고 '.join(names) + expanded[match.end():]
            changed = True
    if changed:
        from canonical_request_planner import instruction_text
        residual = instruction_text(expanded, tables)
        if not structure_request(residual)['metrics'] and not unsupported_quantity_terms(residual, tables):
            return canonical_selections(expanded, tables, allow_combined_values=True)
    explicit = canonical_selections(query, tables)
    if explicit:
        return explicit
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
    domain_metrics = {'가계대출': {'대출'}, '가계신용': {'대출'}, '산업대출': {'대출'}, '성장기여도': {'GDP', '수출'}, 'GDP지출': {'GDP','수출','수입'}, '국제수지': {'수출', '수입'}, '경상수지': set(), '지급수단': {'신용카드'}, '결제시스템': set(), '금융망': set(), '외환결제': set()}
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
            if domain == '산업대출' and '예금취급기관' in q and not any(w in q for w in ('비은행','예금은행')) and '산업별대출금예금취급기관' not in title:
                continue
            from semantic_catalog_planner import dimension_matches
            groups = [dimension_matches(query,table,d) for d in table.get('dimensions', [])]
            coverage = sum((len(v) for v in groups))
            if not coverage:
                continue
            score = coverage * 100
            if any(w in q for w in ['제외','뺀','이외']) and any(any(w in v['value_name'] for w in ['이외','제외']) for vs in groups for v in vs):score+=500
            if domain=='금융망' and '처리' in q and title=='한은금융망':score+=100
            if domain=='금융망' and '처리' not in q and '결제통계표' in title:score+=100
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


def selections(query,tables):
    from query_conditions import active_conditions
    query=active_conditions(query)
    # Bare rates coordinated with another measure need independent clarification.
    if re.search(r'(?:^|까지(?:의)?\s+|(?:최근|현재)\s+)금리(?:와|과)\s',query):return []
    explicit_ids=set(structure_request(query)['explicit_ids'])
    if explicit_ids:
        # IDs constrain tables, not the number of requested classifications.
        # The scalar dictionary scorer otherwise drops coordinated values.
        scoped=[t for t in tables if t['table_id'] in explicit_ids]
        if {t['table_id'] for t in scoped}!=explicit_ids:return []
        semantic_query=re.sub(r'\[?\bDT_[0-9A-Za-z]+\b\]?', ' ', query, flags=re.I)
        chosen=selections(semantic_query,scoped)
        return chosen if {t['table_id'] for t in chosen}==explicit_ids else []
    foreign=any(word in query for word in ['미국','미시간','유로지역','글로벌','해외'])
    if foreign and any(word in query for word in ['심리','체감경기','기대인플레이션']):
        eligible=[t for t in tables if not any('sentiment' in domain for domain in t.get('domains',[])) or any(word in str(t.get('table_name')) for word in ['지역별','국가별'])]
        tables=eligible
    explicit=canonical_selections(query,tables)
    if explicit and not missing_requested_scopes(query,explicit,tables):return explicit
    if structure_request(query)['explicit_ids']:return []
    if unavailable(query,tables) or unsupported_quantity_terms(query,tables):return []
    from semantic_catalog_planner import selections as semantic_selections
    legacy=legacy_selections(query,tables)
    if legacy and not missing_requested_scopes(query,legacy,tables) and all('explicit_catalog_scope' in item.get('reasons',[]) for item in legacy):return legacy
    # Prefer metadata-grounded measured concepts before broad legacy aliases;
    # e.g. an unadjusted account must not acquire a seasonal table as an extra.
    selected=semantic_selections(query,tables)
    if selected and not missing_requested_scopes(query,selected,tables):return selected
    if legacy and not missing_requested_scopes(query,legacy,tables):return legacy
    return scoped_selections(query, tables)


def scoped_selections(query, tables):
    """Keep the basis/region of each independently named comparison clause."""
    sentences = re.split(r'(?<!\d)[.!?\n]', query)
    dated = [s for s in sentences if re.search(r'(?:19|20)\d{2}년', s)]
    shared = ' '.join(re.findall(r'(?:19|20)\d{2}년|월별|분기별|연간|연도별|일별', query))
    for sentence in dated or sentences:
        parts = re.split(r'(?:과|와)\s+(?!선그래프|그래프|표\b)', sentence)
        if not 2 <= len(parts) <= 5:
            continue
        selected = []
        for part in parts:
            # Recursion terminates: each part has no comparison separator.
            matches = selections(part + ' ' + shared, tables)
            if not matches or any(m.get('_missing_dimensions') for m in matches):
                break
            selected.extend(matches)
        else:
            if len({m['table_id'] for m in selected}) > 1 and not missing_requested_scopes(query, selected, tables):
                return selected
    return []
