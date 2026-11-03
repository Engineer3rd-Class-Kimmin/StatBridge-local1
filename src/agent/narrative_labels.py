"""Short names for prose only; source labels and lookup keys stay intact."""
import re


def narrative_name(label, *, table_name=''):
    def parts(value):
        text = str(value or '')
        # Repeated removal handles nested annotations without eating the name.
        while True:
            clean = re.sub(r'\([^()]*\)|（[^（）]*）', '', text)
            if clean == text:
                break
            text = clean
        text = re.sub(r'\bDT_[A-Z0-9_]+\b', '', text, flags=re.I)
        result = []
        # Spaced dots separate catalog fields; unspaced dots may be arithmetic
        # subjects (two regions) or a calculation's month/window definition.
        for part in re.split(r'\s+[·ㆍ]\s+', text):
            part = re.sub(r'\s+\d+\s*\)\s*$', '', part)
            part = re.sub(r'\s+', ' ', part).strip(' ·ㆍ,')
            if part:
                result.append(part)
        return result

    names = parts(label)
    table = parts(table_name)
    generic = {'신규취급액', '신규취급액 기준', '잔액', '잔액 기준', '대출평균', '저축성수신', '총지수'}
    meaningful = list(dict.fromkeys(name for name in names if name not in generic))
    if meaningful:
        return ' · '.join(meaningful)
    return table[0] if table else '해당 지표'


def narrative_names(series):
    """Keep distinct selections distinct even when their short names collide."""
    names = [narrative_name(item['label'], table_name=item.get('tableName', '')) for item in series]
    short_names = tuple(names)
    for index, item in enumerate(series):
        if item.get('calculation') or any(
                other != index and short_names[other] == short_names[index]
                and series[other]['label'] != item['label'] for other in range(len(series))):
            label = re.sub(r'\bDT_[A-Z0-9_]+\b', '', str(item['label']), flags=re.I)
            label = re.sub(r'(?<=\S)\s+\d+\)\s*$', '', label)
            names[index] = re.sub(r'\s+', ' ', label).strip(' ·ㆍ,') or names[index]
    return names
