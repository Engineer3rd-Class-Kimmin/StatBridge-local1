"""Closed display ranges independent of base dates and catalog title years."""
import re
import calendar
from datetime import date
from request_period import explicit_period,TOKEN


def requested_period_edit(query):
    """Recognize an explicit date-only edit, never a formula/data/style edit."""
    text = str(query).strip()
    context = re.match(r'^(.*?)\s*(?:현재|기존)\s+(?:19|20)\d{2}.*?그래프에서\s*', text)
    if context:
        # Names can precede a reference to the current graph. Instructions cannot.
        if re.search(r'계산식|바꿔|변경|수정|코드|exec|python|그리고|하고', context[1], re.I):
            return None
        text = text[context.end():]
    if not re.match(r'^(?:(?:표시|조회)\s*)?기간', text):
        return None
    period = explicit_period(text)
    if not period:
        return None
    residual = TOKEN.sub('', text)
    residual = re.sub(r'표시|조회|기간|날짜|시작일|종료일|부터|까지|에서|변경|수정|줄여|늘려|바꿔|바꾸어|다시|해|보여|그려|주세요|줘|으로|로|을|를|만|[\s.,!?~～–—-]', '', residual)
    return period if not residual else None


def requested_display_period(query,table_names=()):
    text=str(query)
    # A historical table's title is not the requested observation interval.
    for name in sorted(set(table_names),key=len,reverse=True):
        if name:text=text.replace(name,' ')
    single_result = re.search(r'한\s*(?:숫자|점|값)|(?:숫자|간격|중간값|결과|금액|폭)(?:이|은|는|을|를)?\s*하나', text)
    calculation = re.search(r'1/\d+\s*씩|평균|합계|더한|더해|간격|차이|퍼센트|변화|폭|중간값|얼마에\s*해당', text)
    if single_result and calculation and not re.search(r'(?:표시|출력)\s*기간', text):
        requested = explicit_period(text)
        if requested:
            end = date.fromisoformat(requested['end'])
            start_month = end.month
            if re.search(r'분기', text) and not re.search(r'\d{1,2}\s*월', text):
                target = re.search(r'[1-4]\s*분기(?:(?![1-4]\s*분기).)*?(?:비해|대비|기준|에서)(?:(?![1-4]\s*분기).)*?([1-4])\s*분기', text)
                if target:
                    start_month = (int(target[1]) - 1) * 3 + 1
                else:
                    start_month = (end.month - 1) // 3 * 3 + 1
                finish_month = start_month + 2
            else:
                target = re.search(r'(?<!\d)\d{1,2}\s*월(?:(?!\d{1,2}\s*월).)*?(?:비해|대비|기준|에서)(?:(?!\d{1,2}\s*월).)*?(\d{1,2})\s*월', text)
                if target:
                    start_month = int(target[1])
                finish_month = start_month
            return {**requested, 'start': date(end.year, start_month, 1).isoformat(),
                    'end': date(end.year, finish_month, calendar.monthrange(end.year, finish_month)[1]).isoformat(),
                    'basis': '명시한 입력 기간과 구분한 단일 계산 결과 표시기간'}
    # A period mean may explicitly request one result at its ending month.
    # That is the output range; the source lookback remains in the DAG and is
    # fetched independently. Do not force the model to emit earlier averages.
    if re.search(r'월별.*산술\s*평균.*(?:대표값|결과).*하나|각\s*달에\s*1/\d+씩\s*같은\s*비중',text):
        requested=explicit_period(text)
        if requested:return {**requested,'start':requested['end'][:8]+'01','basis':'월별 값의 기간 산술평균을 종료 월 한 점으로 표시'}
    single=re.search(r'(?:결과|출력)(?:는|을|은)?\s*(종료|마지막|\d{1,2})\s*월(?:에|의|만)?\s*(?:한\s*점|하나|1개)',text)
    if single:
        requested=explicit_period(text)
        if requested and (single[1] in {'종료','마지막'} or int(single[1])==int(requested['end'][5:7])):
            return {**requested,'start':requested['end'][:8]+'01','basis':'요청한 종료 월 단일 출력; 원자료 기간은 계산 계획에서 검증'}
    matches=list(TOKEN.finditer(text));ranges=[]
    for left,right in zip(matches,matches[1:]):
        connector=text[left.end():right.start()]
        if not re.fullmatch(r'\s*(?:부터|에서|~|～|–|—|to)\s*',connector,re.I):continue
        # A closed interval must end here, not 'from ... onward'.
        tail=text[right.end():]
        if not re.match(r'\s*(?:까지|동안|사이|$|[,.])',tail):continue
        found=explicit_period(text[left.start():right.end()]+'까지')
        if found:ranges.append(found)
    identities={(r['start'],r['end']) for r in ranges}
    if len(identities)==1:return ranges[0]
    if len(identities)>1:return None  # Multiple comparison intervals need clarification.
    return explicit_period(text)
