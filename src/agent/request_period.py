"""Parse explicit user date ranges independently of model-generated periods."""
import calendar
from datetime import date
import re

TOKEN=re.compile(r"(?<![\da-z_])(?P<year>(?:19|20)\d{2})(?:-(?P<im>\d{1,2})(?!\d)(?:-(?P<iday>\d{1,2})(?!\d))?|\s*년(?:\s*(?P<quarter>[1-4])\s*분기|\s*(?P<month>\d{1,2})\s*월(?:\s*(?P<day>\d{1,2})\s*일)?)?)?",re.I)


def _boundary(match,end=False):
    year=int(match['year']); month=int(match['im'] or match['month'] or (12 if end else 1))
    if match['quarter']: month=(int(match['quarter'])-1)*3+(3 if end else 1)
    day=int(match['iday'] or match['day'] or (calendar.monthrange(year,month)[1] if end else 1))
    return date(year,month,day).isoformat()


def explicit_period(query, latest=None):
    original=str(query or '')
    original=re.sub(r'((?:19|20)\d{2})\s*년\s*(?:첫\s*(?:석|세|3)\s*달|첫\s*분기)',r'\1년 1분기',original)
    original=re.sub(r'((?:19|20)\d{2})\s*년\s*(?:첫\s*(?:두|2)\s*달)',r'\1년 1~2월',original)
    original=re.sub(r'(?:19|20)\d{2}\s*년\s*이후\s*수록(?:된|\s*자료)?', ' ',original)
    original=re.sub(r'(?<!\d)(?:19|20)\d{2}\s*SNA(?:\s*기준)?|\((?:19|20)\d{2}\s*[~∼]\s*\)', ' ',original,flags=re.I)
    # Expand closed shorthand before TOKEN can mistake its year for a full
    # January-to-December request ("2025년 1~6월"). No endpoints are guessed
    # across distinct years, and open bounds keep their existing behavior.
    month_range=r'(\d{1,2})\s*(?:월\s*)?(?:~|～|–|—)\s*(\d{1,2})\s*월(?:\s*까지)?'
    expanded=re.sub(r'(?<!\d)((?:19|20)\d{2})\s*년\s*'+month_range,
                    lambda m:f'{m[1]}년 {m[2]}월부터 {m[1]}년 {m[3]}월까지',original)
    years=set(re.findall(r'(?<![\da-z_])(?:19|20)\d{2}(?!\d)',original,re.I))
    if len(years)==1:
        year=next(iter(years))
        expanded=re.sub(r'(?<![\d년])'+month_range,
                        lambda m:f'{year}년 {m[1]}월부터 {year}년 {m[2]}월까지',expanded)
    if expanded!=original:
        result=explicit_period(expanded,latest)
        if result:result['expression']=original
        return result
    # Index base years are metadata, not a second observation endpoint.
    text=re.sub(r'(?<!\d)(?:19|20)\d{2}\s*(?:년\s*(?:\d{1,2}\s*월\s*)?)?[=＝]\s*100(?!\d)',lambda m:' '*len(m[0]),original)
    matches=list(TOKEN.finditer(text))
    relative = re.search(r'최근\s*(\d{1,2})\s*년', text)
    if relative and not matches and latest:
        end = date.fromisoformat(latest)
        count = int(relative[1])
        if count < 1: return None
        month_index = end.year * 12 + end.month - count * 12
        year, month = divmod(month_index, 12)
        return {'start': date(year, month+1, 1).isoformat(), 'end': end.isoformat(),
                'source': 'latest_observation', 'expression': relative[0],
                'basis': '최신 제공 관측 시점 기준'}
    inherited=[]
    for match in matches:
        tail=text[match.end():]
        connector=r'\s*(?:부터|에서|~|～|–|—|to)\s*'
        short=re.match(connector+r'(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?\s*(?:까지|동안|사이)',tail,re.I)
        if short and (match['month'] or match['im']):
            year,month=int(match['year']),int(short[1])
            day=int(short[2]) if short[2] else calendar.monthrange(year,month)[1]
            end=date(year,month,day).isoformat()
        else:
            short=re.match(connector+r'(\d{1,2})\s*일\s*(?:까지|동안|사이)',tail,re.I)
            if short and (match['day'] or match['iday']):
                end=date(int(match['year']),int(match['month'] or match['im']),int(short[1])).isoformat()
            else:
                short=re.match(connector+r'([1-4])\s*분기\s*(?:까지|동안|사이)',tail,re.I)
                if not short or not match['quarter']:continue
                year,month=int(match['year']),int(short[1])*3
                end=date(year,month,calendar.monthrange(year,month)[1]).isoformat()
        start=_boundary(match)
        if start>end:raise ValueError('텍스트의 시작일이 종료일보다 늦습니다.')
        inherited.append({'start':start,'end':end,'source':'text',
                          'expression':original[match.start():match.end()+short.end()]})
    if inherited:
        # Several comparison periods must not collapse into a single interval.
        full_pairs=sum(bool(re.fullmatch(connector,text[a.end():b.start()],re.I))
                       for a,b in zip(matches,matches[1:]))
        if len(inherited)>1 or full_pairs:return None
        return inherited[0]
    if not matches:return None
    if len(matches)>2:return None
    if len(matches)==2:
        between=text[matches[0].end():matches[1].start()]
        connected=re.search(r'부터|에서|~|～|–|—|-|\bto\b',between,re.I)
        through=re.fullmatch(r'\s*(?:과|와)\s*',between) and re.match(r'\s*(?:까지|사이)',text[matches[1].end():])
        adjacent_years = (re.fullmatch(r'\s*(?:과|와)\s*',between) and re.search(r'연간|연도별',text)
                          and int(matches[1]['year']) == int(matches[0]['year'])+1
                          and not any(m['month'] or m['im'] or m['quarter'] for m in matches))
        if not connected and not through and not adjacent_years:return None
        start,end=_boundary(matches[0]),_boundary(matches[1],True)
    elif re.search(r'현재|지금|최신|오늘',text) and re.search(r'부터|이후',text):
        if not latest:return None
        start,end=_boundary(matches[0]),latest
    elif re.search(r'부터|이후|까지|이전',text):
        return None  # One open bound must not become an invented closed range.
    else:
        start,end=_boundary(matches[0]),_boundary(matches[0],True)
    if start>end:raise ValueError('텍스트의 시작일이 종료일보다 늦습니다.')
    return {'start':start,'end':end,'source':'text','expression':text[matches[0].start():matches[-1].end()]}
