"""Metadata-owned selection of named measures and classification values.

Uses only current catalog titles/values; never imports evaluation inputs or plans.
"""
import re
import csv
from pathlib import Path
from itertools import product
from functools import lru_cache
from collections import Counter


def norm(s):
    s=re.sub(r'\s*(?:주)?\d+\)\s*$', '', str(s))
    s=re.sub(r'^\d+[.\)]\s*','',s)
    s=re.sub(r'^[A-K][0-9-]*\s+', '', s)
    return re.sub(r'[^a-z0-9가-힣]','',s.lower()).replace('및','').replace('으로인한','').replace('대비','대').replace('재화와서비스의','재화서비스')


@lru_cache(maxsize=1)
def catalog_paths():
    source=Path(__file__).resolve().parents[2]/'data/processed/bok_table_master.csv'
    if not source.is_file():return {}
    with source.open(encoding='utf-8-sig',newline='') as stream:
        return {row['TBL_ID']:row.get('PATH','') for row in csv.DictReader(stream)}


@lru_cache(maxsize=32768)
def value_terms(name):
    text=re.sub(r'\s*(?:주)?\d+\)\s*$', '', str(name))
    terms={norm(text)}
    optional=re.fullmatch(r'(.+)\(([^)]+)\)',text)
    if optional and len(norm(optional.group(1)))<=3 and re.fullmatch(r'[a-zA-Z0-9]+',optional.group(1).strip()):terms.add(norm(optional.group(2)))
    if optional and not any(ch.isdigit() for ch in optional.group(2)) and len(norm(optional.group(1)))>=3:terms.add(norm(optional.group(1)))
    # Survey prefixes and footnotes are metadata context, not required wording.
    for prefix in ['국내은행-', '국내은행', '기업-', '가계-', 'A.','Ⅰ.','1.']:
        if text.startswith(prefix):terms.add(norm(text[len(prefix):]))
    for suffix in ['CSI','BSI']:
        if suffix in text:terms.add(norm(text.replace(suffix,'')))
    # Catalog compound industry names support conventional shortened wording.
    if text.endswith('서비스업') and '·' in text:
        stem=text[:-4].strip();parts=stem.split('·')
        if len(parts)>=3:terms.add(norm(''.join(parts[:2])+'서비스업'))
    if ' 및 ' in text and '개발' in text and text.endswith('업'):
        subject=text.split(' 및 ')[0];terms.add(norm(subject+'개발업'))
    if text.endswith('서비스 공급업'):terms.add(norm(text.replace(' 공급','')))
    if norm(text).endswith('합계'):terms.add('총'+norm(text)[:-2])
    if norm(text).startswith('처리'):terms.add(norm(text)[2:])
    if norm(text).endswith('대출') and len(norm(text))>4:terms.add(norm(text)[:-2])
    if norm(text).startswith('중앙은행대'):terms.add(norm(text)[5:])
    if norm(text) in {'민간','정부'}:terms|={norm(text)+'소비',norm(text)+'최종소비지출'}
    return {s for s in terms if len(s)>=1}


def dimension_query(query,table):
    base=table['table_name'].split('(')[0].strip()
    if len(norm(base))>=4:
        pattern=r'(?<![가-힣a-zA-Z0-9])'+r'\s*'.join(re.escape(c) for c in base if not c.isspace())+r'(?![가-힣a-zA-Z0-9])'
        return re.sub(pattern,' ',query,count=1,flags=re.I)
    return query


def dimension_matches(query,table,dimension):
    # A title may also be the aggregate classification. Suppress title-owned
    # values only when the user named a more specific value in this dimension.
    return matches(dimension_query(query,table),dimension) or matches(query,dimension)


def matches(query,dimension):
    query=re.sub(r'([가-힣]+)\([A-Za-z0-9]+\)',r'\1',query)
    query=query.replace('어음부도율','부도율')
    # Calendar bounds are not numeric classification names (12월 != 1~2%).
    # Keep named forecast horizons such as 향후1년 intact.
    query=re.sub(r'(?:19|20)\d{2}\s*년(?!\s*기준)(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?|\s*[1-4]\s*분기)?', ' ', query)
    q=norm(query);hits=[];exclusions={}
    # Shared modifiers in coordinated phrases own every listed member.
    query=re.sub(r'([가-힣A-Za-z]+)[·,]([가-힣A-Za-z]+)\s*(이용금액|이용건수|현금흐름)',r'\1\3 · \2\3',query)
    q=norm(query)
    for v in dimension.get('values',[]):
        if str(v['value_name']).startswith('[참고]') and '참고' not in q and '구m2' not in q:continue
        terms=set(value_terms(v['value_name']))
        if any(w in q for w in ['순환변동치','원계열']) and any(w in v['value_name'] for w in ['순환변동치','원계열']):terms={t for t in terms if any(w in t for w in ['순환변동치','원계열'])}
        if '장기' in q and '만기' in q:
            lower=re.match(r'(\d+)년이상',v['value_name'].replace(' ',''))
            if lower and int(lower.group(1))>=1:terms.add('장기')
        if '현금흐름' in q and norm(v['value_name']).endswith('현금흐름'):terms.add(norm(v['value_name'])[:-4])
        if '서비스수지' in q and norm(v['value_name']) in {'서비스수입','서비스지급'}:terms.add(norm(v['value_name'])[3:])
        for term in terms:
            if len(term)==1 and not re.search(r'(?<![가-힣])'+re.escape(term)+r'(?=[·,\s]|과|와|의|을|를|$)',query):continue
            if term not in q:continue
            if term=='장기' and '만기' in q:hits.append((0,0,v,term));continue
            for m in re.finditer(re.escape(term),q):
                if any(q[m.end():].startswith(w) for w in ['에서','가운데','중에서']) and not term.endswith('기준'):continue
                if term in {'기타','전체','합계'} and not re.search(r'(?<![가-힣])'+re.escape(term)+r'(?=\s|[·,]|과|와|로|를|을|$)',query):continue
                hits.append((m.start(),m.end(),v,term))
        value=norm(v['value_name']).replace('공제','')
        for ending in ['수출','수입']:
            if value.endswith(ending) and value[:-len(ending)] in q and ending in q and '재화서비스' in value:hits.append((-1,-1,v,value))
        if '부도율' in q and '부도율' in value and '전자결제분포함' in value and '전자결제분' in q and '포함' in q:hits.append((-1,-1,v,value))
        # Shared numerator in coordinated ratios: X 대비 A·B means X/A and X/B.
        ratio=re.fullmatch(r'(.+)대(.+)',norm(v['value_name']))
        if ratio and '현금흐름' in v['value_name'] and '대비' in query and ratio.group(1) in q and ratio.group(2) in q:
            hits.append((-1,-1,v,norm(v['value_name'])))
        # Qualifiers may be separated by Korean particles: currency (country),
        # bank (borrower) and rate-range names remain one catalog value.
        parts=[norm(x) for x in re.split(r'[()\-]',re.sub(r'(?:\s*\d+\))+\s*$','',str(v['value_name']))) if len(norm(x))>=2]
        parts=[part.replace('환매조건부채권매도','환매조건부채권') for part in parts]
        parts=[part[:-2] if part.endswith('대출') and len(part)>4 else part for part in parts]
        excluded=re.search(r'(?:이외|제외)',v['value_name'])
        if excluded and any(w in q for w in ['뺀','제외']) and norm(v['value_name']) not in q:
            pieces=[norm(x) for x in re.split(r'및|과|와|제외|이외',v['value_name']) if len(norm(x))>=2]
            prefix=norm(re.split(r'제외|이외',v['value_name'])[0])
            exclude_q=norm(re.split(r'제외|뺀|이외',query)[0])
            components=sorted({norm(w['value_name']) for w in dimension['values'] if norm(w['value_name']) in prefix and norm(w['value_name']) in exclude_q and '이외' not in w['value_name'] and len(norm(w['value_name']))>=2},key=len,reverse=True)
            remainder=prefix
            for part in components:remainder=remainder.replace(part,'')
            if components and not remainder:
                exclusions[v['value_id']]=len(components);hits.append((-1,-1,v,norm(v['value_name'])))
        rate_range=re.search(r'(\d+(?:\.\d+)?)%?\s*[~∼]\s*(\d+(?:\.\d+)?)%.*미만',v['value_name'])
        prefix=norm(v['value_name'].split('-')[0])
        if rate_range and prefix not in q:continue
        if rate_range and float(rate_range.group(2))==float(rate_range.group(1))+1 and re.search(re.escape(rate_range.group(1))+r'%\s*대',query):hits.append((-1,-1,v,norm(v['value_name'])))
        if len(parts)>1 and all(part in q for part in parts) :
            hits.append((0,0,v,norm(v['value_name'])))
    kept=[h for h in hits if not any(h[0]>=0 and n[0]>=0 and n[0]<=h[0] and n[1]>=h[1] and n[1]-n[0]>h[1]-h[0] for n in hits)]
    found=list({h[2]['value_id']:h[2] for h in kept}.values())
    preferred={v['value_name'] for v in found if v.get('_language_index_preferred')}
    found=[v for v in found if v['value_name'] not in preferred or v.get('_language_index_preferred')]
    if exclusions:
        best=max(exclusions.values());found=[v for v in found if exclusions.get(v['value_id'])==best]
    if '원계열' in q or '순환변동치' in q:found=[v for v in found if not any(b in v['value_name'] for b in ['원계열','순환변동치']) or any(b in q and b in v['value_name'] for b in ['원계열','순환변동치'])]
    if '포함' in q and '제외' not in q:found=[v for v in found if '제외' not in v['value_name']]
    if '제외' in q and '포함' not in q and not exclusions:found=[v for v in found if '포함' not in v['value_name']]
    # Different names can share a punctuation-free spelling; preserve composite wording.
    composites=[v for v in found if '및' in v['value_name'] and any(norm(v['value_name'])==norm(w['value_name']) and v!=w for w in found)]
    if composites and any(mark in query for mark in ['·','및']):found=[v for v in found if not any(norm(v['value_name'])==norm(w['value_name']) and v!=w for w in composites)]
    exact=[v for v in found if norm(v['value_name']) in q]
    found=[v for v in found if v in exact or not any(norm(w['value_name']) in value_terms(v['value_name']) for w in exact)]
    if len(found)>1:
        if any(norm(v['value_name'])=='ict산업' for v in found):found=[v for v in found if norm(v['value_name'])!='ict산업']
        if any(w in q for w in ['단기','장기']):found=[v for v in found if norm(v['value_name']) not in {'대외채무','순대외채권'}]
        found=[v for v in found if not (any(x in norm(v['value_name']) for x in ['m1','m2','본원통화','lf']) and any(b in norm(v['value_name']) for b in ['말잔','평잔','계열']) or norm(v['value_name']).startswith('l광의유동성')) or (any(w in q for w in ['전체','총계','합계']) and not any(w in q for w in ['구성','계열에서']))]
        if any(w in q for w in ['나눠','구성','가운데']):found=[v for v in found if '가계최종소비지출' not in norm(v['value_name'])]
        found=[v for v in found if norm(v['value_name'])!='가계신용' or not any(norm(w['value_name']) in {'예금은행','상호저축은행','가계대출','판매신용'} for w in found)]
    positions={v['value_id']:min((h[0] for h in kept if h[2]['value_id']==v['value_id'] and h[0]>=0),default=len(q)) for v in found}
    found.sort(key=lambda v:positions[v['value_id']])
    return found


def family(q,title,table):
    """A measured family is stronger evidence than generic dimension words."""
    if ('gdp' in q or '국내총생산' in q) and '지출' not in q:
        return 'GDP' if '경제활동별gdp' in title and 'gni' in title else None
    if '경상수지' in q and '국제수지' not in q and '원계열' not in q and not any(w in q for w in ['상품수지','서비스수지','본원소득수지','자본수지','금융계정']):
        return '경상수지' if '경상수지' in title else None
    if any(x in q for x in ['상품수지','서비스수지','상품수출','상품수입','경상수지','본원소득수지','자본수지','금융계정']):
        if '지역' in title:return '금융계정' if '금융계정' in title and '금융계정' in q else '국제수지' if '경상수지' in title and '경상수지' in q else None
        if '계절조정' in q and '원계열' not in q and not any(w in q for w in ['계절조정하지않','계절조정하지않은']):return '국제수지' if '경상수지' in title and '계절조정' in title else None
        return '국제수지' if title=='국제수지' else None
    if any(x in q for x in ['여행수지','운송수지']):return '서비스수지' if '서비스무역세분류' in title else None
    if '기업' in q and (any(w in q for w in ['연간','연도별','최근몇년']) or ('기업경영분석' in q and not any(w in q for w in ['월별','분기별']))) and '금리' not in q:
        return 'corporate_measure' if 'corporate_finance' in table.get('domains',[]) else None
    if '광의유동성' in q:return '광의유동성' if '광의유동성' in title else None
    if '개관표' in q:return '개관표' if '개관표' in title else None
    if '순대외채권' in q:return '순대외채권' if '순대외채권' in title else None
    for loan in ['대출태도','신용위험','대출수요']:
        if loan in title:return loan if loan in q or (loan=='대출태도' and '대출에대한태도' in q) else None
    if 'gni' in q or '국민총소득' in q:return '국민총소득' if '국내총생산과지출' in title else None
    if '가계' in q and any(w in q for w in ['식료품','주류','담배','내구재','최종소비지출']):
        return '가계소비' if '가계' in title and '최종소비지출' in title else None
    if '어음부도율' in q:return '어음부도율' if '어음교환' in title and '부도' in title else None
    if 'ict산업' in q:return 'ict산업' if 'ict산업특수분류' in title else None
    if '금리' not in q and '예금은행' in q and '대출금' in q and '금리' not in title and '예금은행대출금' in title:return '은행대출금'
    if '여신' in q:return '여신' if '비은행금융기관여신' in title else None
    if '수신' in q and any(w in q for w in ['상호저축은행','신용협동조합','상호금융']):return '수신' if '비은행금융기관수신' in title else None
    if '예금은행' in q and '총수신' in q:return '총수신' if '예금은행총수신' in title else None
    if '예금은행' in q and '원화예금' in q:return '원화예금' if '예금은행원화예금' in title else None
    if '예금은행' in q and '총수신' in q:return '총수신' if '예금은행총수신' in title else None
    if '금리' not in q and '가계대출' in q and '예금은행' in q and '예금은행지역별가계대출' in title:
        return '가계대출잔액'
    if '금리' not in q and '비중' not in q and '대출' in q:
        if '기업대출' in q and '예금은행' in q and '예금은행대출금' in title:return '기업대출잔액'
        if '가계대출' in q and ('예금취급기관' in q or '예금은행' in q) and '예금취급기관가계대출' in title:return '가계대출잔액'
    if '가계신용' in q:return '가계신용' if '가계신용' in title else None
    if '금리' in q and not any(x in q for x in ['대출태도','신용위험','대출수요']):
        if '비중' in q:
            return '금리비중' if '금리' in title and '비중' in title else None
        if '금리' not in title or '비중' in title:return None
        if '대출' in title:return '대출금리' if '대출' in q else None
        if '수신' in title:return '수신금리' if any(x in q.replace('예금은행','') for x in ['예금','적금','수신','저축','cd','rp']) else None
        return None
    for term in ['대출태도','신용위험','대출수요']:
        if term in title:return term if term in q else None
    for term in ['수출물가','수입물가','수출물량','수입물량','수출금액','수입금액','생산자물가']:
        if term in title:return term if term in q else None
    if 'bsi' in q or '업황' in q:
        return 'BSI' if any(x in title for x in ['기업경기','기업경기실사']) else None
    if '경영애로' in q:return '경영애로' if '경영애로' in title else None
    if any(x in q for x in ['소비자심리','생활형편','소비자들의']):
        return '소비자동향' if '소비자동향' in title else None
    if '신용카드' in q and '결제금액' in q and '이용금액' not in q:return '지급수단' if '지급수단별' in title else None
    if '지로' in q and any(w in q for w in ['cd공동망','타행환공동망']) and '지급결제동향' in title:return '지급결제동향'
    if '체크카드' in q and '신용카드' in q:return '지급수단' if '지급수단별' in title else None
    if any(x in title for x in ['지로','금융공동망','한은금융망','신용카드','결제시스템별','지급결제동향','전자지급결제대행']):
        if '시스템' in q and '결제' in q and any(x in title for x in ['결제시스템별','지로','금융공동망']):return '결제시스템'
        for term in ['지급결제동향','전자지급결제대행','신용카드','한은금융망','지로','금융공동망']:
            if term in title and term in q:return term
    for term in ['본원통화','m1','m2','lf','광의유동성']:
        if term in title:
            aliases={'lf':['lf','금융기관유동성'],'광의유동성':['광의유동성']}.get(term,[term])
            return term if any(x in q for x in aliases) else None
    # Exact title subjects retain table boundaries even if their qualifiers
    # are elsewhere in the sentence.
    base=re.sub(r'\([^)]*\)','',table['table_name']);base=norm(base)
    if len(base)>=3 and base in q:return base
    for term in ['개관표','국제투자대조표','대외채무','순대외채권','총저축','최종소비지출','경상수지','금융계정','대출금','원화예금','총수신','화폐발행','국내공급물가','어음부도','신용카드','지로','금융공동망','한은금융망','전자지급결제대행','ict산업']:
        if term in q and term in title:return term
    return None


def selections(query,tables,debug=None):
    from query_semantics import active_request
    query=active_request(query)
    query=re.sub(r'계절\s*조정하지\s*않(?:은|고)?', '원계열',query)
    query=query.replace('계절 요인을 제거한','계절조정').replace('계절 요인 제거','계절조정')
    q=norm(query).replace('협의통화','m1').replace('광의통화','m2').replace('대중국','중국')
    buckets={}
    paths=catalog_paths()
    counts=Counter(t['table_name'] for t in tables)
    duplicate_titles={name for name,count in counts.items() if count>1}
    for table in tables:
        title=norm(table['table_name']);f=family(q,title,table)
        if '금리' in title and '금리' not in q:continue
        if '지역별' in q and '지역' not in title:continue
        if '국가별' in q and '국가' not in title:continue
        if not f and any(x in q for x in ['기업','가계소비','여신','총수신','원화예금','경영애로','신용카드','한은금융망','결제시스템','지로','금융공동망','경상수지','상품수지','서비스수지','금융계정','ict산업']):continue
        if not f and any(x in q for x in ['금리','업황','bsi','소비자물가','기준금리','개관표','가계신용','순대외채권','m1','m2','본원통화','lf','광의유동성','대출태도','대출수요','신용위험']):continue
        from request_match_guard import measured_intent_matches
        years=[int(x) for x in re.findall(r'(?:19|20)\d{2}(?=년)',query)]
        end=str(table.get('period_end_observed') or '')[:4]
        start=str(table.get('period_start_observed') or '')[:4]
        if years and end.isdigit() and int(end)<min(years):continue
        if years and start.isdigit() and int(start)>max(years):continue
        if table['table_name'] in duplicate_titles:
            path=norm(paths.get(table['table_id'],''))
            if '잔액' in q and '신규취급액' not in q and '신규취급액' in path:continue
            if '신규취급액' in q and '잔액' not in q and '잔액' in path:continue
        groups=[dimension_matches(query,table,d) for d in table.get('dimensions',[])]
        if f in {'대출금리','수신금리'}:
            # Basis aliases describe the table, not an extra account series.
            groups=[[v for v in vs if norm(v['value_name']) not in {'신규취급액기준','잔액기준'}] or vs for vs in groups]
            if all(not any(norm(v['value_name']) not in {'신규취급액기준','잔액기준'} for v in vs) for vs in groups):continue
        scoped_table={**table,'item_names':table.get('item_names',[])+[v['value_name'] for vs in groups for v in vs]}
        if f=='경영애로' and '%' in table.get('units',[]):scoped_table['item_names']+=['응답 비중']
        measure_query=' '.join(v['value_name'] for v in (groups[-1] if groups else [])) if f=='corporate_measure' else query
        if f in {'대출금리','수신금리'} and '신규취급액' in q and '잔액' in q:
            measure_query=measure_query.replace('신규취급액','') if '신규취급액' not in title else measure_query.replace('잔액','')
        if '수출금액' in title:measure_query=measure_query.replace('수출물량','')
        if '수출물량' in title:measure_query=measure_query.replace('수출금액','')
        if not measured_intent_matches(measure_query,scoped_table):continue
        # A specific measured dimension (financial ratio, purpose account) can
        # identify its table where the user does not know the formal title.
        longest=max((len(term) for vs in groups for v in vs for term in value_terms(v['value_name']) if term in q and not re.search(r'(?:은행|기업|기관|조합|제조업|서비스업)$',term)),default=0)
        if not f:
            if longest<6:continue
            if any(x in q for x in ['금리','업황','bsi','소비자물가','기준금리']):continue
            f='dimension_measure'
        frequency=table.get('prd_se')
        if any(x in q for x in ['연간','연도별','매년']) and ('연간' in title or set(table.get('available_frequencies',[])) & {'년','Y','A'}):
            frequency='Y'
        if '일별' in q and frequency!='D':continue
        if '시간별' in q and frequency!='H':continue
        if any(x in q for x in ['월별','월마다','매월']) and frequency!='M':continue
        if any(x in q for x in ['분기별','분기통계','분기마다']) and frequency!='Q':continue
        if any(x in q for x in ['연간','연도별','매년']) and frequency not in {'A','Y'}:continue
        if f in {'m1','m2','본원통화','lf','광의유동성'}:
            if '계절조정' in q and '계절조정' not in title:continue
            if '원계열' in q and '원계열' not in title:continue
            if '광의유동성' in q and f!='광의유동성':continue
            if 'lf' in q and f!='lf':continue
        rejected=False
        if '잔액' in q and '평잔' not in q and '평잔' in title:continue
        for wanted,opposite in [('말잔','평잔'),('평잔','말잔'),('원계열','계절조정'),('계절조정','원계열'),('실질','명목'),('명목','실질'),('실적','전망'),('전망','실적'),('신규취급액','잔액'),('잔액','신규취급액')]:
            if wanted in q and opposite not in q and opposite in title and wanted not in title:rejected=True
        if rejected:continue
        if '분위수' in title and '분위수' not in q:continue
        if '차주당' in title and '차주당' not in q:continue
        if '예금주' not in q and f=='원화예금' and '예금주' in title:continue
        if '가계신용' not in q and '대출행태' in title and not any(w in q for w in ['대출태도','대출수요','신용위험','대출에대한태도']):continue
        if '예금은행' in q and '상호저축은행' not in q and '비은행' not in q and '비은행' in title:continue
        if '한국은행' in q and '원화대출금' in q and '한국은행원화대출금' not in title:continue
        if '지역' in title and not any(x in q for x in ['지역','중국','미국','일본']) and not any(norm(v['value_name']) in q for d in table.get('dimensions',[]) for v in d['values'] if len(norm(v['value_name']))>=2 and (re.search(r'(?:RG|AREA|REGION|NATION|COUNTRY)_',v['value_id'],re.I) or '지역' in d.get('object_name',''))):continue
        if '국가' in title and '국가' not in q:continue
        if f=='dimension_measure' and '기준' in ''.join(v['value_name'] for d in table['dimensions'] for v in d['values']) and any(w in q for w in ['수출금액','수출물량']):continue
        if '업종별기업경기' in title and '서비스업' not in q and not ('제조업' in q and '비제조업' in q):continue
        if '기업경기조사' in title and '서비스업' in q:continue
        score=sum(max((len(t) for t in value_terms(v['value_name']) if t in q),default=0) for vs in groups for v in vs)*20
        score-=sum(10 for vs in groups for v in vs if norm(v['value_name']) not in q)
        score+=sum(len(norm(word))*3 for word in re.findall(r'[a-zA-Z0-9가-힣]+',table['table_name']) if len(norm(word))>=3 and norm(word) in q)
        # Prefer modern named surveys and specific borrower/account contexts.
        if '기업경기조사' in title:score+=20
        if '업종별기업경기' in title and '제조업' in q and '비제조업' in q and '전망' in q:score+=40
        if '말잔' in title and not any(w in q for w in ['말잔','평잔']):score+=10
        if '기본분류' in title and not any(w in q for w in ['특수','품목별','용도별']):score+=10
        if '매출액가중' in title and '매출액가중' not in q:score-=200
        if '예금은행' in q and not any(w in q for w in ['비은행','상호저축은행','상호금융','신용협동조합']) and '비은행' in title:continue
        if '비은행' in q and '비은행' not in title and ('수신' in q or '여신' in q):continue
        if f in {'m1','m2'} and not any(w in q for w in ['경제주체','보유','비영리','비금융기업']) and '경제주체별' in title:score-=60
        if f in {'m1','m2'} and not any(w in q for w in ['예금은행','신탁','서민금융','기관별']) and '기관별' in title:score-=60
        if f in {'대출금리','수신금리'} and '신규취급액' not in q and '잔액' not in q and '신규취급액' in title:score+=10
        if '명목' not in q and '실질' not in q and f in {'가계소비','국민총소득'} and '명목' in title:score+=10
        if f=='가계소비' and '계절조정' in title:score+=10
        if end.isdigit():score+=int(end)-2000
        standard=re.search(r'제(\d+)차',table['table_name'])
        if standard:score+=int(standard.group(1))
        if '차주당' in title and '차주당' not in q:score-=200
        if f in {'대출금리','수신금리'} and '신규취급액' in q and '잔액' in q:
            f+=':'+('신규취급액' if '신규취급액' in title else '잔액')
        if '분기' in q and frequency=='Q':score+=15
        if f=='dimension_measure' and not groups:continue
        if f in {'m1','m2','본원통화','lf','광의유동성'} and not any(groups):
            for idx,d in enumerate(table.get('dimensions',[])):
                groups[idx]=[v for v in d['values'] if norm(v['value_name']).startswith(f) and not v['value_name'].startswith('[참고]')][:1]
        if f=='총수신' and not any(groups):
            groups=[[v for v in d['values'] if norm(v['value_name']) in {'총수신','수신합계'}] for d in table['dimensions']]
        if not any(groups) and not norm(re.sub(r'\([^)]*\)','',table['table_name'])) in q:continue
        buckets.setdefault(f,[]).append((score,table,groups))
    if debug is not None:debug.update({k:[(score,t['table_name'],t['table_id']) for score,t,_ in sorted(v,key=lambda x:-x[0])[:5]] for k,v in buckets.items()})
    if '지급결제동향' in buckets and '지로' in q and any(w in q for w in ['cd공동망','타행환공동망']):
        for alternative in ['지로','금융공동망']:buckets.pop(alternative,None)
    if len(buckets)>1 and 'dimension_measure' in buckets:del buckets['dimension_measure']
    result=[]
    chosen_rows=[]
    for f,ranked in buckets.items():
        ranked.sort(key=lambda x:-x[0])
        if f=='corporate_measure':
            if '현금흐름' in q and '비율' in q:ranked=[row for row in ranked if '현금흐름비율' in norm(row[1]['table_name'])] or ranked
            spans=[];terms_by_table={}
            for score,t,gs in ranked:
                terms={term for v in (gs[-1] if gs else []) for term in value_terms(v['value_name']) if len(term)>=3 and term in q}
                terms_by_table[t['table_id']]=terms
                for term in terms:
                    spans.extend((m.start(),m.end(),term) for m in re.finditer(re.escape(term),q))
            wanted={h[2] for h in spans if not any(h[0]>=0 and n[0]>=0 and n[0]<=h[0] and n[1]>=h[1] and n[1]-n[0]>h[1]-h[0] for n in spans)}
            remaining=set(wanted)
            while remaining:
                best=max(ranked,key=lambda row:(len(terms_by_table[row[1]['table_id']]&remaining),row[0]))
                covered=terms_by_table[best[1]['table_id']]&remaining
                if not covered:break
                chosen_rows.append(best);remaining-=covered
            continue
        if len(ranked)>1 and ranked[0][0]==ranked[1][0]:continue
        chosen_rows.append(ranked[0])
    for _,table,groups in chosen_rows:
        choices=[];missing=[]

        for d,vs in zip(table.get('dimensions',[]),groups):
            if not vs:
                values=d.get('values',[]);default=d.get('representative_value_id')
                vs=[v for v in values if v['value_id']==default]
                if not vs:vs=[v for v in values if norm(v['value_name']) in {'합계','전체','전국','종합','총지수','총예금','전산업','계','총수신','총대출금','원화대출금계','수신합계'}]
                if not vs and all(re.fullmatch(r'\d{4}년기준',v['value_name']) for v in values) and values:vs=values[:1]
                if not vs and len(values)==1:vs=values
                if not vs and values:missing.append(d.get('object_name') or d['api_param']);vs=values[:1]
                vs=vs[:1]
            choices.append(vs or [None])
        for combo in product(*choices):
            result.append({'table_id':table['table_id'],'table_name':table['table_name'],'score':400,'reasons':['metadata_named_measure'],'_frequency':('Y' if any(x in q for x in ['연간','연도별','매년']) and ('연간' in norm(table['table_name']) or set(table.get('available_frequencies',[])) & {'년','Y','A'}) else table.get('prd_se')),'dimension_hits':[{'api_param':d['api_param'],'value_id':v['value_id'],'value_name':v['value_name']} for d,v in zip(table.get('dimensions',[]),combo) if v],'_missing_dimensions':missing})
    return result
