"""Scope Korean query constraints before catalog matching and operation routing.

Only user wording is transformed here. No table IDs, observations, or evaluation
cases belong in this layer. The original request remains available to the UI.
"""
import re
from functools import lru_cache

QUOTED = r'[‘“「《\"\']([^‘’“”「」《》\"\']+)[’”」》\"\']'


@lru_cache(maxsize=1024)
def active_request(query):
    text = str(query)
    # A quoted value remains a constraint unless its surrounding words explicitly
    # identify it as a title, abandoned proposal, or list of possible choices.
    def quote(match):
        before = re.split(r'[.!?\n]',text[max(0, match.start()-35):match.start()])[-1]
        after = re.split(r'[.!?\n]',text[match.end():match.end()+65])[0]
        context = before + ' ' + after
        if re.search(r'제목|절의?\s*이름|절에\s*넣을|선택지|후보\s*목록|가능한\s*비교|양식.*예시|이전\s*검토안|제외\s*대상|취소|검토하다\s*버린|참고\s*메모.*남아\s*있지만', context):
            return ' '
        return match[0]
    text = re.sub(QUOTED, quote, text)
    # These clauses describe the document, not the requested observations.
    text = re.sub(r'(?:참고\s*문헌|참고\s*자료|다른\s*문서|참고\s*문서|보고서\s*양식|양식\s*머리말|중간\s*메모|검토\s*메모|메모|초안|후보\s*목록)[^.!?\n]*?(?:이전\s*검토안이고|제외\s*대상이고|적었지만|함께\s*있는데|라고\s*되어\s*있지만|취소할게|쓰지\s*말고)', ' ', text)
    text = re.sub(r'[^.!?\n]*(?:선택지\s*목록|후보\s*목록|가능한\s*비교\s*방식|두\s*체크\s*항목|양식\s*작성\s*연도)[^.!?\n]*(?:[.!?]|$)', ' ', text)
    text = re.sub(r'(?:보고서의|보고서\s*전체|도표)?\s*제목(?:은|을|이)?[^.!?\n]*?(?:둘게|두되|이고|이야|쓰더라도)', ' ', text)
    text = re.sub(r'[^.!?\n]*(?:설명\s*절의\s*이름|참고\s*문헌\s*제목|참고\s*자료의\s*제목)[^.!?\n]*?(?:이고|인데|바꾸지는\s*않아|[.!?])', ' ', text)
    # Preserve the positive statistical concept when its opposite is negated.
    replacements = [
        (r'계절\s*요인을?\s*제거한\s*계열은?\s*제외하고', '원계열'),
        (r'계절\s*(?:요인을?\s*따로\s*)?조정하지\s*않(?:은|고)?|계절조정\s*전\s*값', '원계열'),
        (r'계절\s*요인(?:까지|을)?\s*조정(?:된|한)|계절적\s*움직임을\s*조정한|계절\s*요인을\s*조정한', '계절조정'),
        (r'한\s*달(?:\s*동안)?(?:의)?\s*평균잔액|월\s*평균잔액', '평잔'),
        (r'(?:매월|매달)\s*마지막(?:\s*시점)?(?:의)?\s*잔액|월말(?:에\s*남아\s*있는)?\s*잔액', '말잔'),
        (r'(?:각\s*달에|그달)\s*새로\s*(?:취급한|받은)\s*대출', '신규취급액 대출'),
        (r'새로\s*유치한', '신규취급액'),
        (r'가계가\s*(?=신규취급액)', '가계대출 '),
        (r'각\s*달에\s*남아\s*있는\s*대출\s*잔액\s*전체', '잔액 기준 대출'),
        (r'금융채가\s*빠진', '금융채 제외'),
        (r'그해\s*가격으로\s*표시한', '명목'),
        (r'해외에\s*보유한\s*금융자산', '대외금융자산'),
        (r'계절\s*요인을\s*제거한', '계절조정'),
    ]
    for pattern, value in replacements:
        text = re.sub(pattern, value, text)
    if '국내총생산' not in text:text=re.sub(r'시장가격\s*GDP','국내총생산 시장가격 GDP',text)
    text = re.sub(r'초안의\s*[^.!?]+?선택을(?=[^.!?]*(?:바꿔|변경))', ' ', text)
    text = re.sub(r'부채를\s*차감한\s*순자산이나\s*대외금융부채는?\s*제외해줘', ' ', text)
    text = re.sub(r'같은\s*이름의\s*분기별\s*수치나\s*실질\s*계열은\s*제외해줘', ' ', text)
    text = re.sub(r'전국\s*값과\s*다른\s*금융기관\s*값은\s*빼고', ' ', text)
    text = re.sub(r'상품수지\s*하나만\s*뽑는\s*건\s*아니야', ' ', text)
    text = re.sub(r'대출\s*쪽은\s*빼고', ' ', text)
    text = re.sub(r'[가-힣]+에서\s*다시\s*계산하지\s*말고', ' ', text)
    text = re.sub(r'순상품\s*계열', '순상품교역조건지수 계열', text)
    # Remove only an excluded alternative, retaining the surrounding subject.
    alternatives = r'(?:신규취급액|기존\s*잔액\s*전체를\s*대상으로\s*한\s*금리|명목|실질|원계열|계절조정(?:계열)?|평잔|말잔|구\s*M2|대기업|중소기업|상품수지(?:\s*하나)?|판매신용|대외금융부채|순자산|거래\s*흐름|거래표|잔액표|순환변동치|수출금액지수|수출물량지수)'
    text = re.sub(r'(?:참고용\s*)?' + alternatives + r'(?:이나|나|와|과|·)\s*' + alternatives + r'\s*(?:후보)?(?:는|은|를|을)?\s*(?:제외하고|빼고|빼줘|섞지\s*말고)', ' ', text, flags=re.I)
    text = re.sub(r'(?:참고용\s*)?' + alternatives + r'\s*(?:후보)?(?:는|은|를|을|만)?\s*(?:제외하고|제외해줘|제외할게|제외하고|빼고|빼줘|섞지\s*말고)', ' ', text, flags=re.I)
    text = re.sub(r'대기업만이나\s*중소기업만이\s*아니라', ' ', text)
    text = re.sub(r'기존에\s*남아\s*있는\s*대출\s*전체의\s*금리는\s*제외하고', ' ', text)
    text = re.sub(r'검토하다\s*버린\s*[^.!?]*?쓰지\s*말고', ' ', text)
    text = re.sub(r'비교에\s*쓰는\s*(?:19|20)\d{2}년\s*값은\s*가져와도\s*되지만', ' ', text)
    text = re.sub(r'순환\s*부분만\s*따로\s*뽑은\s*계열은\s*빼고', '원계열', text)
    # An explicit override supersedes earlier currency/default constraints.
    currency = list(re.finditer(r'(원화|달러|계약통화)\s*기준', text))
    if len(currency) > 1 and re.search(r'이번\s*표|예외|변경|바꿀|되돌리지', text):
        last = currency[-1]
        prefix = re.sub(r'(원화|달러|계약통화)\s*기준', '이전 기준', text[:last.start()])
        text = prefix + text[last.start():]
    text = re.sub(r'(?:보고서\s*공통\s*설정은)\s*(?:원화|달러|계약통화)지만', ' ', text)
    if re.search(r'\d\s*[~～∼-]\s*\d\s*분기',text) and not re.search(r'월별|매달|매월|분기별',text):
        text+=' 분기별'
    return re.sub(r'\s+', ' ', text).strip()


def operation_text(query):
    """Keep positive arithmetic directives; discard negated/future operations."""
    text = active_request(query)
    text = re.sub(r'(?:이미\s*)?(?:공표|발표)된\s*(?:증가율|감소율|변화율|비중|비율)', '공표값', text)
    text = re.sub(r'[‘“\"\']합계[’”\"\'](?:로|를)?\s*선택', '분류 선택', text)
    clauses = re.split(r'(?<=[.!?])\s+|[;\n]', text)
    kept = []
    for clause in clauses:
        # Retain a later positive directive after a negated alternative.
        clause = re.sub(r'[^,]*?(?:계산|평균|증가율|변화|비중|비율|누계|이동평균)[^,]*?(?:하지\s*말고|내라는\s*뜻은\s*아니야|처리는\s*빼줘|작업은\s*포함하지\s*않아|내지\s*말아줘)', ' ', clause)
        if re.search(r'다음\s*작업에서\s*다룰|따로\s*다룰\s*예정|내가\s*따로.*예정|포함하지\s*않|누계.*사용하지|평균.*대신.*각\s*월', clause):
            # A lookup in an earlier clause is sufficient; a future operation
            # must not route the current request into calculation planning.
            continue
        clause = re.sub(r'(?:\d+|네|세|두)\s*분기로\s*나눠|분기로\s*나눠', '분기별', clause)
        clause = re.sub(r'(?:다시\s*)?계산하지\s*말고', ' ', clause)
        kept.append(clause)
    return ' '.join(kept)
