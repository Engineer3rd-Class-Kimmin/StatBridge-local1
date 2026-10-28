"""Resolve explicit catalog references without treating title words as filters."""
import re
from itertools import product
from functools import lru_cache


@lru_cache(maxsize=1024)
def _title_pattern(name):
    return re.compile(r"\s*".join(re.escape(c) for c in name if not c.isspace()),re.I)
from request_match_guard import compact


def references(query, tables):
    # Preserve raw spans so title vintage years cannot become date filters.
    boundary=re.search(r'\d+\s*번째\s*분류',query)
    searchable=query[:boundary.start()] if boundary else query
    hits=[]
    for table in tables:
        name=table['table_name']
        for m in _title_pattern(name).finditer(searchable):
            hits.append((m.start(),m.end(),table))
    hits=[h for h in hits if query[h[0]:h[1]] == h[2]['table_name'] or not any(a==h[0] and b==h[1] and query[a:b]==t['table_name'] for a,b,t in hits)]
    return [h for h in hits if not any(a<=h[0] and b>=h[1] and b-a>h[1]-h[0] for a,b,_ in hits)]


def instruction_text(query, tables):
    from query_semantics import active_request
    query=active_request(query)
    chars=list(query)
    for start,end,_ in references(query,tables):
        chars[start:end]=' '* (end-start)
    text=''.join(chars)
    text=re.sub(r'(?<!\d)(?:19|20)\d{2}\s*SNA(?:\s*기준)?', ' ', text, flags=re.I)
    text=re.sub(r'\[분류\s*코드[^\]]*\]',' ',text)
    text=re.sub(r'\bDT_[0-9A-Za-z]+\b',' ',text)
    values={v['value_name'] for _,_,t in references(query,tables) for d in t.get('dimensions',[]) for v in d.get('values',[])}
    return re.sub(r'[「《“\"](.*?)[」》”\"]',lambda m:' ' if m.group(1) in values else m.group(0),text)


def selections(query,tables, *, allow_combined_values=False):
    hits=references(query,tables)
    # Explicitly named catalog objects establish a boundary. Short product words
    # inside semantic questions remain owned by the measured-concept planner.
    if not hits or not (re.search(r'[「《“"].*[」》”"]',query) or
                        any('(' in t['table_name'] for _,_,t in hits) or
                        re.search(r'\bDT_[0-9A-Za-z]+\b',query)):
        return []
    groups={}
    for a,b,t in hits:groups.setdefault((a,b),[]).append(t)
    chosen=[]
    assigned_ids=set()
    spans=sorted(groups)
    for i,(a,b) in enumerate(spans):
        candidates=groups[(a,b)]
        # Bind only an immediately adjacent ID. A later clause's prefix ID
        # cannot narrow the current title; duplicate titles retain their own IDs.
        before=re.search(r'(?:\[(DT_[0-9A-Za-z]+)\]|\((DT_[0-9A-Za-z]+)\))\s*$',query[:a])
        after=re.match(r'\s*[」》”"]?\s*(?:\[(DT_[0-9A-Za-z]+)\]|\((DT_[0-9A-Za-z]+)\))',query[b:])
        ids={next(g for g in m.groups() if g) for m in (before,after) if m}
        if len(ids)>1:return []
        if ids:
            candidates=[t for t in candidates if t['table_id'] in ids]
            assigned_ids.update(ids)
        if len(candidates)!=1:return []
        chosen.append(candidates[0])
    if assigned_ids != set(re.findall(r'\bDT_[0-9A-Za-z]+\b',query)):
        return []
    chars=list(query)
    for a,b,_ in hits:chars[a:b]=' '*(b-a)
    residual=''.join(chars)
    result=[]
    for table in chosen:
        groups=[]
        missing=[]
        for dimension in table.get('dimensions',[]):
            level=dimension.get('level',1)
            scoped=re.search(rf'{level}\s*번째\s*분류(?:의|는|에서)?(.*?)(?=\d+\s*번째\s*분류|조회\s*기간|$)',residual,re.S)
            text=scoped.group(1) if scoped else residual
            codes=re.findall(r'분류\s*코드\s+([^\]\s]+)',text)
            values=dimension.get('values',[])
            if codes:
                found=[v for v in values if v['value_id'] in codes]
            else:
                quoted=re.findall(r'[「《“"](.*?)[」》”"]',text)
                found=[v for v in values if v['value_name'] in quoted]
                if not found and not scoped and len(chosen)==1:
                    from semantic_catalog_planner import matches
                    found=matches(text,dimension)
            if not found and allow_combined_values:
                q=compact(text)
                found=[v for v in values if compact(v['value_name']) in q or ((m:=re.fullmatch(r'(.+?)\s*\((.+)\)',v['value_name'])) and all(compact(part) in q for part in m.groups()))]
                found=[v for v in found if not any(compact(v['value_name'])!=compact(w['value_name']) and compact(v['value_name']) in compact(w['value_name']) for w in found)]
            if not found:
                representative=dimension.get('representative_value_id')
                if not representative and len(values)>1 and compact(values[0]['value_name']) not in {'총지수','합계','총계','전체','전국','계','종합'}:
                    label=dimension.get('object_name') or f"{level}번째 분류"
                    examples=' / '.join(v['value_name'] for v in values[:3])
                    missing.append(f"{table['table_name']} · {label} ({examples} 등 {len(values)}개)")
                found=[next((v for v in values if v['value_id']==representative),values[0])] if values else [None]
            groups.append(found)
        for combo in product(*groups):
            hits=[{'api_param':d['api_param'],'value_id':v['value_id'],'value_name':v['value_name']} for d,v in zip(table.get('dimensions',[]),combo) if v]
            result.append({'table_id':table['table_id'],'table_name':table['table_name'],'dimension_hits':hits,'score':300,'reasons':['explicit_catalog_scope'],'_missing_dimensions':missing})
    return result
