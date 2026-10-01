import copy, json, os, re, unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

@dataclass
class QueryState:
    metrics: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    company_sizes: list[str] = field(default_factory=list)
    measure_basis: str | None = None
    valuation_basis: str | None = None
    frequency: str | None = None
    period: str | None = None
    comparison: bool = False
    series: list[str] = field(default_factory=list)

class StatLanguageResolver:
    """StatBridge v5 resolver.

    Flow:
      query -> score candidates -> detect ambiguity -> ask one clarification
      -> store confirmed option -> re-search -> resolve.

    API ids are never invented. Only ids already stored in the dictionary are returned.
    """
    def __init__(self, dictionary_path):
        dictionary_path=Path(dictionary_path)
        self.data=json.loads(dictionary_path.read_text(encoding='utf-8'))
        self.tables=self.data['tables']
        extension_path=dictionary_path.with_name('clarification_extensions.json')
        self.extensions=json.loads(extension_path.read_text(encoding='utf-8')) if extension_path.exists() else {}
        self.groups=sorted([*self.data.get('clarification_groups',[]),*self.extensions.get('groups',[])], key=lambda x:x.get('priority',999))
        self.catalog_only_tables=[t for t in self.extensions.get('catalog_only_tables',[])
                                  if str(t['table_id']) not in {str(x['table_id']) for x in self.tables}]
        self.policy=self.data.get('clarification_policy',{})
        self.tables_by_id={str(t['table_id']):t for t in self.tables}
        self._score_cache={}
        self.last_followup_trace={}
        self._catalog_search_text=' '.join(
            self.norm(t.get('table_name',''))+' '+' '.join(self.norm(x) for x in t.get('aliases',[])+t.get('semantic_aliases',[]))
            for t in self.tables
        ).replace(' ','')

    def parse_query_state(self, query):
        """Extract conservative typed state without inventing catalog identifiers."""
        text=self.norm(query); compact=text.replace(' ','')
        state=QueryState()
        metric_terms=('예금금리','수신금리','대출금리','기준금리','주택담보대출','가계대출','채권','통화량','소비자물가','생산자물가','국내공급물가','수출물량','수입물량','신용카드','지급수단','전자지급결제대행','기업경기','소비자심리','경제심리','금융거래','금융자산부채잔액')
        state.metrics=[term for term in metric_terms if term in compact]
        state.series=list(state.metrics)
        state.regions=[term for term in ('전국','서울','부산','제주','지역별') if term in compact]
        state.institutions=[term for term in ('예금은행','비은행금융기관','금융기관') if term in compact]
        state.company_sizes=[term for term in ('대기업','중소기업','중견기업','기업규모별') if term in compact]
        state.measure_basis=next((term for term in ('신규취급액','잔액','비중','금액','물량') if term in compact),None)
        state.valuation_basis=next((term for term in ('명목','시가','실질') if term in compact),None)
        state.frequency=next((value for term,value in (('시간별','H'),('일별','D'),('월별','M'),('분기별','Q'),('연간','A')) if term in compact),None)
        years=re.findall(r'(?<!\d)((?:19|20)?\d{2})년',text)
        state.period=years[-1] if years else None
        state.comparison=bool(re.search(r'(비교|나란히|같이|함께|그리고|및|,)',text))
        return state

    def apply_followup_state(self, previous_query, current_query):
        previous=self.parse_query_state(previous_query); current=self.parse_query_state(current_query)
        additive=bool(re.search(r'(같이|함께|추가|도(?=\s*(?:같이|함께|추가|보여|비교)))',str(current_query)))
        remove=bool(re.search(r'(빼|제외|삭제)',str(current_query)))
        clear=bool(re.search(r'(전부\s*지워|초기화|조건\s*없)',str(current_query)))
        operations=[]
        result=copy.deepcopy(previous)
        for name in ('metrics','subjects','regions','institutions','company_sizes','series'):
            before=list(getattr(previous,name)); incoming=list(getattr(current,name))
            if clear:
                after=[]; op='CLEAR'
            elif remove and incoming:
                after=[x for x in before if x not in incoming]; op='REMOVE'
            elif additive and incoming:
                after=list(dict.fromkeys([*before,*incoming])); op='ADD'
            elif incoming:
                after=incoming; op='REPLACE' if before and before!=incoming else 'SET'
            else:
                after=before; op='KEEP'
            setattr(result,name,after)
            if op!='KEEP': operations.append({'field':name,'operation':op,'before':before,'after':after,'evidence':current_query})
        for name in ('measure_basis','valuation_basis','frequency','period'):
            before=getattr(previous,name); incoming=getattr(current,name)
            if clear: after=None; op='CLEAR'
            elif incoming is not None: after=incoming; op='REPLACE' if before and before!=incoming else 'SET'
            else: after=before; op='KEEP'
            setattr(result,name,after)
            if op!='KEEP': operations.append({'field':name,'operation':op,'before':before,'after':after,'evidence':current_query})
        result.comparison=current.comparison or additive or (previous.comparison and not clear)
        effective=self.merge_followup_query(previous_query,current_query)
        return result,operations,effective

    def _has_unsupported_core_metric(self, query):
        """Reject modifier-only matches when an explicit measured quantity is outside the catalog."""
        q=self.norm(query); compact=q.replace(' ','')
        catalog=self._catalog_search_text
        stop=('지역별','산업별','기관별','시간별','일별','월별','분기별','연간','자료','추이','비교','보고','보여')
        tokens=[]
        for raw in re.findall(r'[가-힣a-z]+',q):
            token=re.sub(r'(을|를|이|가|은|는|의|로|으로)$','',raw)
            if len(token)>=2 and not any(s in token for s in stop): tokens.append(token)
        quantity=[x for x in tokens if x.endswith(('사용량','생산량','소비량','발전량'))]
        return bool(quantity and not any(x in catalog for x in quantity))

    def candidate_shell(self, table_id):
        t=self.tables_by_id[str(table_id)]
        return {'table_id':t['table_id'],'table_name':t['table_name'],'score':0.0,'reasons':[],
                'dimension_hits':[],'prd_se':t.get('prd_se'),'api_call_params':t.get('api_call_params',{}),
                'clarification_tags':t.get('clarification_tags',[])}

    @staticmethod
    def norm(s):
        s=unicodedata.normalize('NFKC',str(s or '')).lower()
        s=re.sub(r'[^0-9a-z가-힣%]+',' ',s)
        return re.sub(r'\s+',' ',s).strip()

    def _contains(self,q,term,minlen=2):
        t=self.norm(term)
        return bool(t and len(t)>=minlen and (t in q or t.replace(' ','') in q.replace(' ','')))

    def _confirmed_terms(self, confirmed):
        out=[]
        if not confirmed: return out
        for gid,value in confirmed.items():
            g=next((x for x in self.groups if x['id']==gid),None)
            if not g: continue
            o=next((x for x in g['options'] if x['value']==value),None)
            if o:
                out.extend(o.get('confirmed_terms',[]))
        return list(dict.fromkeys(out))

    def _matches_confirmed_filters(self, table, confirmed):
        """Confirmed button selections are hard constraints, not just soft scores."""
        if not confirmed: return True
        tn=str(table.get('table_name','')).lower()
        for gid,value in confirmed.items():
            g=next((x for x in self.groups if x['id']==gid),None)
            if not g: continue
            o=next((x for x in g['options'] if x['value']==value),None)
            if not o: continue
            selectors=o.get('table_name_any',[])
            excludes=o.get('table_name_none',[])
            if selectors and not any(str(x).lower() in tn for x in selectors):
                return False
            if excludes and any(str(x).lower() in tn for x in excludes):
                return False
        return True

    def _matches_metric_intent(self, query, table):
        """Reject look-alike tables that share broad aliases but measure another concept."""
        q=self.norm(query); tn=self.norm(table.get('table_name',''))
        qc=q.replace(' ',''); tc=tn.replace(' ','')
        # Detailed 2018+ accounts are a distinct family, not the aggregate
        # financial-transaction / asset-liability tables with similar words.
        if '상세자금순환' in qc and '상세자금순환' not in ''.join(self.norm(x).replace(' ','') for x in table.get('aliases',[])):
            return False
        if '상세자금순환' in qc:
            if any(x in qc for x in ('잔액','보유액')) and tc != '잔액표':
                return False
            if any(x in qc for x in ('거래','순거래')) and '잔액' not in qc and tc != '거래표':
                return False
        searchable=[tc]
        searchable.extend(self.norm(x).replace(' ','') for x in table.get('aliases',[]))
        for dimension in table.get('dimensions',[]):
            searchable.extend(self.norm(v.get('normalized') or v.get('value_name')).replace(' ','') for v in dimension.get('values',[]))
        haystack=' '.join(searchable)
        comparison=bool(re.search(r'(비교|차이|함께|같이|나란히|와|과|,)',q))
        requested_rate=[]
        if '예금금리' in qc or '수신금리' in qc: requested_rate.append(('수신금리','예금금리'))
        if '대출금리' in qc: requested_rate.append(('대출금리',))
        if requested_rate:
            matches=[any(term in tc for term in family) for family in requested_rate]
            if not (any(matches) if comparison and len(requested_rate)>1 else all(matches)):
                return False
        if '기준금리' in qc and '기준금리' not in haystack:
            return False
        if '소비자물가' in qc and '소비자물가' not in haystack:
            return False
        price_terms=[term for term in ('생산자물가','국내공급물가','총산출물가','수출물가','수입물가') if term in qc]
        if price_terms and not (any(term in haystack for term in price_terms) if comparison and len(price_terms)>1 else all(term in haystack for term in price_terms)):
            return False
        if ('비중' in qc or '구성비' in qc) and not any(x in haystack for x in ('비중','구성')):
            return False
        if '금리' in qc and '비중' not in qc and '비중' in tc:
            return False
        if '잔액' in qc and '금리' not in qc and '금리' in tc:
            return False
        # A subject-qualified M2 request must use the economic-subject table;
        # product breakdown tables cannot represent household/non-profit holders.
        if 'm2' in qc and any(x in qc for x in ('가계','개인','비영리')) and '경제주체' not in tc:
            return False
        return True

    def _score(self, query, confirmed=None, top_k=12):
        cache_key=(self.norm(query),tuple(sorted((confirmed or {}).items())),int(top_k))
        cached=self._score_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
        q=self.norm(query)
        explicit_ids={tid for tid in self.tables_by_id if self.norm(tid) in q}
        if not explicit_ids and self._has_unsupported_core_metric(query):
            self._score_cache[cache_key]=[]
            return []
        confirmed=confirmed or {}
        confirmed_terms=self._confirmed_terms(confirmed)
        confirmed_weight=float(self.policy.get('confirmed_term_weight',220))

        # Keep the useful v4 public-intent gates, but clarification can override them.
        intent_rules=[
            (['시중에 돈','돈이 얼마나 풀','시장에 도는 돈','통화량'],['m2'],95),
            (['주담대','집 담보','아파트 대출','주택 대출'],['주택담보대출'],110),
            (['산업별대출','산업별 대출'],['산업별대출금'],110),
            (['대출 이자','대출 금리','돈 빌리면 이자','돈 빌릴 때 이자'],['대출금리'],100),
            (['예금 이자','예금 금리','돈 맡기면 이자'],['수신금리'],100),
            (['경제가 얼마나 성장','경제 성장','경제 규모','나라 경제 크기'],['gdp'],100),
            (['국민이 번 돈','국민소득'],['gni'],100),
            (['경제 분위기','기업과 소비자','경제 심리'],['경제심리지수'],105),
            (['기업들이','회사들이','기업 체감'],['기업경기'],80),
            (['사람들이 요즘 경기를','소비자들이','소비 심리','가계가 경기를'],['소비자동향'],85),
            (['공장에서 물건','생산 단계 가격','생산자 물가'],['생산자물가'],100),
            (['해외에서 번 돈과 쓴 돈','외국과 거래해서 돈을 얼마나 남','경상수지 흑자','경상수지 적자'],['경상수지'],100),
            (['외국과 거래해서','해외 거래 수지','돈이 얼마나 들어오고 나갔'],['국제수지'],85),
            (['수출해서 달러','수출대금','수출 원화 결제'],['결제통화','수출'],105),
            (['수입할 때 달러','수입대금','수입 원화 결제'],['결제통화','수입'],105),
            (['가계가 가진 돈','사람들이 가진 돈','개인이 보유한 돈'],['m2','경제주체'],90),
        ]
        scored=[]
        for t in self.tables:
            if explicit_ids and str(t['table_id']) not in explicit_ids:
                continue
            if not self._matches_confirmed_filters(t, confirmed):
                continue
            if not explicit_ids and not self._matches_metric_intent(query, t):
                continue
            score=0.0; reasons=[]; dim_hits=[]
            tid=self.norm(t['table_id']); tn=self.norm(t['table_name'])
            if not explicit_ids and '상세자금순환' in q.replace(' ',''):
                score+=160; reasons.append('semantic:상세자금순환')

            # Confirmed button/text selections are hard anchors.
            if confirmed_terms:
                for term in confirmed_terms:
                    nt=self.norm(term)
                    hit = nt in tn or any(nt in self.norm(x) for x in t.get('aliases',[])+t.get('semantic_aliases',[])+t.get('public_query_terms',[]))
                    if hit:
                        score += confirmed_weight
                        reasons.append('confirmed:'+term)

            for triggers,targets,pts in intent_rules:
                if any(self.norm(x) in q for x in triggers) and all(self.norm(x) in tn for x in targets):
                    score+=pts; reasons.append('public_intent:'+'+'.join(targets))
            if tid and tid in q: score+=1000; reasons.append('table_id')
            if tn and tn in q: score+=70; reasons.append('table_name')
            qc=q.replace(' ',''); tc=tn.replace(' ','')
            # General noun-phrase coverage: table suffix words such as 표/통계/별
            # need not be spoken by the user.
            table_core=re.sub(r'(별|표|통계|자료|지수)+$','',tc)
            if len(table_core)>=4 and table_core in qc:
                score+=55; reasons.append('core_table_phrase:'+table_core)
            if '주요경제지표' in qc and '주요지표' in tc:
                score+=120; reasons.append('concept_normalization:주요경제지표')
            for a in t.get('aliases',[]):
                if self._contains(q,a): score+=20; reasons.append('alias:'+a)
                else:
                    alias_core=re.sub(r'(별|표|통계|자료|지수)+$','',self.norm(a).replace(' ',''))
                    if len(alias_core)>=4 and alias_core in qc:
                        score+=45; reasons.append('alias_core:'+alias_core)
            for a in t.get('semantic_aliases',[]):
                if self._contains(q,a): score+=9; reasons.append('semantic:'+a)
            for a in t.get('public_query_terms',[]):
                na=self.norm(a)
                if na and na in q:
                    score+=28; reasons.append('public_query:'+a)
                else:
                    at=set(re.findall(r'[가-힣a-z0-9]+',na)); qt=set(re.findall(r'[가-힣a-z0-9]+',q))
                    overlap=[x for x in at & qt if len(x)>=2]
                    if len(overlap)>=2:
                        score+=min(14,4*len(overlap)); reasons.append('public_tokens:'+','.join(overlap[:4]))

            for d in t.get('dimensions',[]):
                best=None
                for v in d.get('values',[]):
                    matched=[]; vscore=0
                    nv=v.get('normalized') or v.get('value_name')
                    if self._contains(q,nv): vscore=max(vscore,22); matched.append((nv,'normalized'))
                    nnv=self.norm(nv)
                    component_hits=[component for component in re.findall(r'[가-힣a-z0-9]+',nnv)
                                    if len(component)>=2 and component in q]
                    if len(set(component_hits))>=2:
                        vscore=max(vscore,28+8*(len(set(component_hits))-1))
                        matched.extend((component,'normalized_component') for component in component_hits)
                    if '가계' in q and ('가계' in nnv or '비영리' in nnv):
                        vscore=max(vscore,35); matched.append((nv,'subject_exact'))
                    for field,pts in [('aliases',18),('layman_terms',15),('value_question_terms',12),('related_concepts',6)]:
                        for term in v.get(field,[]):
                            nt=self.norm(term)
                            if nt and len(nt)>=2 and nt in q:
                                vscore=max(vscore,pts); matched.append((term,field)); break
                    # Confirmed canonical terms can also lock onto dimension values.
                    for cterm in confirmed_terms:
                        nct=self.norm(cterm)
                        candidates=[nv]+v.get('aliases',[])+v.get('layman_terms',[])+v.get('related_concepts',[])
                        if nct and any(nct in self.norm(x) or self.norm(x) in nct for x in candidates if x):
                            vscore=max(vscore,confirmed_weight)
                            matched.append((cterm,'confirmed_value'))
                    neg_hits=[x for x in v.get('negative_terms',[]) if self._contains(q,x)]
                    if neg_hits and not matched: vscore-=20
                    if vscore>0 and matched and (best is None or vscore>best[0]):
                        term,kind=matched[0]
                        best=(vscore,{'api_param':d.get('api_param'),'value_id':v.get('value_id'),'value_name':v.get('value_name'),
                                     'normalized_value':v.get('normalized'),'matched_term':term,'match_kind':kind,'negative_hits':neg_hits})
                if best:
                    score+=best[0]; dim_hits.append(best[1])

            requested_frequency=None
            if any(term in q for term in ('월별','매월','월마다')): requested_frequency='M'
            elif any(term in q for term in ('분기별','분기마다')): requested_frequency='Q'
            elif any(term in q for term in ('연간','연도별','매년')): requested_frequency='A'
            actual_frequency=str(t.get('prd_se') or '').upper()
            if requested_frequency and not explicit_ids:
                if requested_frequency=='A' and actual_frequency not in {'A','Y'}: continue
                if requested_frequency!='A' and actual_frequency!=requested_frequency: continue
                score+=15; reasons.append('frequency_covered')

            # Simple table qualifiers.
            qualifiers=[(['계절조정','계절 영향 제거','sa'],'계절조정',11),(['원계열','비계절조정','nsa'],'원계열',11),
                        (['말잔','기말잔액','월말잔액'],'말잔',9),(['평잔','평균잔액'],'평잔',9),
                        (['실질','물가 영향 제외'],'실질',9),(['명목','현재 가격'],'명목',9),
                        (['신규','새로 받은'],'신규',8),(['잔액','남아 있는'],'잔액',8),
                        (['지역별','지역'],'지역별',6),(['품목별','품목'],'품목별',6)]
            for qterms,target,pts in qualifiers:
                if any(self._contains(q,x) for x in qterms) and target in tn: score+=pts
            qtokens=set(re.findall(r'[가-힣a-z0-9]+',q)); ntokens=set(re.findall(r'[가-힣a-z0-9]+',tn))
            lexical=[tok for tok in (qtokens & ntokens) if len(tok)>=2]
            if lexical:
                score += 12*len(lexical); reasons.append('table_tokens:'+','.join(sorted(lexical)[:6]))
            anchors=[x for x in ('가계','목적별','최종소비지출','업종별','기업경기실사지수','실적','전망','자산','회전율','주택담보대출','신규취급액','잔액','비중','지역별','국가별','예금은행','기업규모별','산업별대출금','용도별','gdp','디플레이터','국고금수급','대출수요','신용위험') if x in q and x in tn]
            if 'gdp' in q and '국내총생산' in tn:
                anchors.append('gdp=국내총생산')
            if anchors:
                score+=16*len(anchors); reasons.append('table_anchors:'+','.join(anchors))
            dimension_region_match=any(sum(1 for component in re.findall(r'[가-힣a-z0-9]+',self.norm(hit.get('value_name'))) if len(component)>=2 and component in q)>=2 for hit in dim_hits)
            if '지역별' in tn and not dimension_region_match and not any(term in q for term in ('지역별','지역','전국','서울','부산','대구','인천','광주','대전','울산','세종','강원','충북','충남','전북','전남','경북','경남','제주')):
                score-=30; reasons.append('unrequested_region_modifier')
            if '국고금수급' in q and '결제금액' in q and '지급결제동향' in tn:
                score+=120; reasons.append('payment_amount_concept')
            # Requested years are hard evidence. Historical revisions that ended before
            # the requested year must not outrank the current table with a similar name.
            years=[int(x) for x in re.findall(r'(?<!\d)((?:19|20)?\d{2})(?!\d)',q)]
            years=[2000+y if y < 100 else y for y in years]
            if years and not explicit_ids:
                start_raw=str(t.get('period_start_observed') or '')[:4]
                end_raw=str(t.get('period_end_observed') or '')[:4]
                if start_raw.isdigit() and end_raw.isdigit():
                    requested=max(years)
                    if not int(start_raw) <= requested <= int(end_raw):
                        continue
                    score+=12; reasons.append('period_covered')
            # Prefer the most specific table phrase. This prevents a broad sibling such
            # as 대외채권 from beating an explicitly requested 순대외채권.
            compact_q=q.replace(' ',''); compact_name=tn.replace(' ','')
            if compact_name and compact_name in compact_q:
                score+=35; reasons.append('exact_table_phrase')
            for other in self.tables:
                other_name=self.norm(other.get('table_name','')).replace(' ','')
                if other_name and other_name in compact_q and compact_name != other_name and compact_name in other_name:
                    score-=25; reasons.append('less_specific_than_explicit_table')
                    break
            if any(x in q for x in ['최근','최신','현재','지금','요즘']):
                pe=str(t.get('period_end_observed') or '')
                if pe.startswith('2026'): score+=10
                elif pe.startswith('2025'): score+=6
            min_candidate_score=float(os.getenv('STATBRIDGE_MIN_RULE_SCORE', str(self.policy.get('min_candidate_score',25))))
            if score>=min_candidate_score:
                scored.append({'table_id':t['table_id'],'table_name':t['table_name'],'score':round(score,2),'reasons':reasons[:30],
                               'dimension_hits':dim_hits[:30],'prd_se':t.get('prd_se'),'api_call_params':t.get('api_call_params',{}),
                               'clarification_tags':t.get('clarification_tags',[])})
        scored.sort(key=lambda x:(-x['score'],x['table_id']))
        result=scored[:top_k]
        if len(self._score_cache)>512:
            self._score_cache.clear()
        self._score_cache[cache_key]=copy.deepcopy(result)
        return result

    def rank(self, query, confirmed=None, top_k=12):
        return self._score(query, confirmed=confirmed, top_k=top_k)

    def _clarification(self, query, candidates, confirmed=None, asked=None):
        confirmed=confirmed or {}; asked=set(asked or [])
        # Later clarification gates must see the canonical terms established by
        # earlier buttons (for example loan_type=산업별대출), not only the user's
        # original broad wording.
        q=self.norm(f"{query} {' '.join(self._confirmed_terms(confirmed))}")
        # v5: ambiguity gates are checked before trusting candidate scores.
        # A short everyday term such as '대출' or '물가' can be too weak to rank tables well,
        # but the dictionary already knows that it branches into several valid concepts.
        for g in self.groups:
            gid=g['id']
            if gid in confirmed or gid in asked: continue
            if not any(self._contains(q,x) for x in g.get('trigger_terms',[])): continue
            if any(self._contains(q,x) for x in g.get('skip_if_terms',[])): continue
            # "기준금리" is an explicit policy-rate metric, not an ambiguous
            # shorthand for deposit/loan rates.  Do not route it through the
            # commercial-rate type/basis questions unless another commercial
            # rate is explicitly present in the same comparison.
            commercial_rate_named=any(self._contains(q,x) for x in ('대출금리','대출 금리','수신금리','예금금리','예금 금리'))
            specific_noncommercial_rate=any(self._contains(q,x) for x in ('기준금리','정책금리','콜금리','시장금리'))
            if gid == 'rate_type' and specific_noncommercial_rate:
                continue
            # 신규취급액/잔액 기준은 예금·대출 가중평균금리의 축이다.
            # 단순히 이름에 "금리"가 들어간 정책·시장금리에 적용하지 않는다.
            if gid == 'interest_basis' and not commercial_rate_named:
                continue
            if g.get('id') in {'loan_type','loan_measure'} and any(self._contains(q,x) for x in ('산업대출','대출태도','대출수요','신용위험','한국은행 원화대출')):
                continue
            if g.get('id') == 'loan_measure' and confirmed.get('loan_type') == '산업별대출':
                continue
            if g.get('id')=='loan_measure' and any(self._contains(q,x) for x in ('업권별','용도별','지역별','기업규모별')):
                continue
            # If exactly one option is explicitly named, the branching concept is no
            # longer ambiguous even when a broad trigger word is also present.
            named_options=[]
            for option in g.get('options',[]):
                terms=[*option.get('confirmed_terms',[]),option.get('value','')]
                if any(self._contains(q,term) for term in terms if term):
                    named_options.append(option.get('value'))
            if len(set(named_options))==1:
                continue
            # An exact table phrase is stronger than an unrelated broad ambiguity gate
            # (e.g. 한국은행 원화대출금 must not ask which kind of retail loan).
            if candidates and any(reason in {'table_name','exact_table_phrase'} or str(reason).startswith('alias:')
                                  for reason in candidates[0].get('reasons',[])):
                top_name=self.norm(candidates[0].get('table_name','')).replace(' ','')
                if len(top_name)>=5 and top_name in q.replace(' ',''):
                    continue
            options=[]
            for o in g['options']:
                matching=[]
                # First use current candidates when possible.
                for c in candidates:
                    tn=c['table_name'].lower()
                    if any(x.lower() in tn for x in o.get('table_name_any',[])) and not any(x.lower() in tn for x in o.get('table_name_none',[])):
                        matching.append(c['table_id'])
                # On the initial turn, ranking can omit valid sibling branches
                # from its top-k candidates (for example, household-loan measures
                # or corporate-statement types).  Complete the clarification from
                # the catalog so a partial ranking cannot silently auto-select one
                # branch.  After an earlier answer has constrained the search,
                # however, an option with no candidate match is genuinely
                # irrelevant and must stay hidden.
                initial_unconstrained_turn = not confirmed and not asked
                if not matching and (not candidates or initial_unconstrained_turn):
                    for t in self.tables:
                        tn=t['table_name'].lower()
                        if any(x.lower() in tn for x in o.get('table_name_any',[])) and not any(x.lower() in tn for x in o.get('table_name_none',[])):
                            matching.append(t['table_id'])
                if matching:
                    options.append({
                      'label':o['label'],'value':o['value'],'confirmed_terms':o.get('confirmed_terms',[]),
                      'matching_table_ids':matching[:20]
                    })
            if len(options)>=2:
                return {'clarification_id':gid,'question':g['question'],'ui':g.get('ui','single_select_buttons'),'options':options[:self.policy.get('max_options',5)]}
        return None

    @staticmethod
    def merge_followup_query(previous, current):
        """Carry prior intent while applying explicit axis changes from the new turn."""
        previous=str(previous or '').strip(); current=str(current or '').strip()
        if not previous or not current or previous==current:
            return current or previous
        axes=[
            ('수출','수입'), ('금액','비중'), ('신규취급액','잔액'), ('신규','잔액'),
            ('실적','전망'), ('명목','실질'), ('기본분류','품목별','용도별','특수분류'),
            ('대외채무','대외채권','순대외채권'),
            ('재무상태표','손익계산서','성장성','손익 지표','자산회전율','생산성'),
            ('금융거래표','금융자산부채잔액표','잔액표'),
        ]
        retained=previous
        compact_current=StatLanguageResolver.norm(current).replace(' ','')
        for axis in axes:
            chosen=[term for term in axis if StatLanguageResolver.norm(term).replace(' ','') in compact_current]
            if chosen:
                for term in sorted(axis,key=len,reverse=True):
                    if term not in chosen:
                        retained=retained.replace(term,' ')
        retained=re.sub(r'\s+',' ',retained).strip()
        return f"{retained} {current}".strip()

    def rank_many(self, query, confirmed=None, top_k=12):
        """Resolve explicitly requested comparison series without inventing IDs."""
        raw_query=str(query or '')
        ranked=self.rank(query,confirmed=confirmed,top_k=max(40,top_k))
        q=self.norm(query); compact=q.replace(' ','')
        if ranked and any(token in str(ranked[0].get('table_name') or '') for token in ('과','및')):
            top_name=self.norm(ranked[0]['table_name']).replace(' ','')
            # norm() removes parentheses, so trim common descriptive suffixes rather
            # than attempting to split on a parenthesis that no longer exists.
            top_base=re.sub(r'(계절조정|원계열|명목|실질|분기|월|연간)+$','',top_name)
            conjuncts=[x for x in re.split(r'(?:과|및)',top_base) if len(x)>=2]
            if top_name in compact or (len(top_base)>=4 and top_base in compact) or (
                len(conjuncts)>=2 and all(term in compact for term in conjuncts)
            ):
                return ranked[:1]
        comparison=bool(re.search(r'(비교|차이|함께|같이|나란히|한 그래프|추가|와|과|,)',raw_query))
        if not comparison:
            return ranked[:1]
        selected=[]
        shared=' '.join(re.findall(r'(?:19|20)?\d{2}년|월별|분기별|연간|신규취급액|잔액 기준|계절조정|원계열|명목|실질',q))
        parts=[part.strip() for part in re.split(r'\s*(?:,|와|과|그리고|및)\s*',raw_query) if part.strip()]
        if len(parts)>=2:
            for part in parts:
                part_ranked=self.rank(f'{part} {shared}'.strip(),confirmed=confirmed,top_k=1)
                if part_ranked and any(str(reason).startswith(('table_name','alias:','alias_core:','core_table_phrase:','concept_normalization:','semantic:','public_','table_tokens:','table_anchors:')) for reason in part_ranked[0].get('reasons',[])):
                    selected.append(part_ranked[0])
            distinct=[]; part_seen=set()
            for item in selected:
                if item['table_id'] not in part_seen:
                    part_seen.add(item['table_id']); distinct.append(item)
            sufficiently_specific=all(len(self.norm(part).replace(' ',''))>=4 for part in parts)
            has_shared_contrast=any(sum(term in compact for term in axis)>=2 for axis in (
                ('신규취급액','잔액'),('수출','수입'),('실적','전망'),('명목','실질'),('금액','물량')
            ))
            if sufficiently_specific and not has_shared_contrast and len(distinct)>=min(5,len(parts)):
                return distinct[:5]
        generic_suffixes=('지수','기준','기본분류','분기','월','연간')
        explicit_by_concept={}
        for item in ranked:
            name=self.norm(item['table_name']).replace(' ','')
            base=re.sub(r'\([^)]*\)','',name)
            variants={name,base}
            if '지수' in base:
                variants.add(base.split('지수',1)[0])
            raw_name=str(item.get('table_name') or '')
            variants.update(self.norm(value).replace(' ','') for value in re.findall(r'\(([^)]+)\)',raw_name))
            for suffix in generic_suffixes:
                variants.update({value.removesuffix(suffix) for value in list(variants) if value.endswith(suffix)})
            explicit=any(len(value)>=4 and value in compact for value in variants)
            if explicit:
                concept=(base.split('지수',1)[0] if '지수' in base else base.split('기본분류',1)[0])
                explicit_by_concept.setdefault(concept,item)
        selected.extend(explicit_by_concept.values())
        # Shared-subject comparisons often elide the repeated prefix: "신규취급액과 잔액".
        contrast_axes=[('신규취급액','잔액'),('수출','수입'),('실적','전망'),('명목','실질'),('금액','물량')]
        for axis in contrast_axes:
            if sum(term in compact for term in axis)<2:
                continue
            # The strongest whole-query result retains the shared subject that is
            # often omitted after the conjunction (e.g. "주담대 신규와 잔액").
            anchors=ranked[:1]
            for anchor in anchors:
                anchor_name=self.norm(anchor['table_name']).replace(' ','')
                for source in axis:
                    if source not in anchor_name: continue
                    for target in axis:
                        if target==source: continue
                        sibling=anchor_name.replace(source,target)
                        # A correct sibling can be outside the ordinary Top-K when a
                        # broad table shares more generic aliases. Search the trusted
                        # dictionary, never an evaluation answer map.
                        exact_siblings=[table for table in self.tables if self.norm(table['table_name']).replace(' ','')==sibling]
                        anchor_active=bool(re.search(r'\d+(?:\.\d+)?~',str(anchor.get('table_name') or '')))
                        if exact_siblings:
                            exact_siblings.sort(key=lambda table:(
                                0 if bool(re.search(r'\d+(?:\.\d+)?~',str(table.get('table_name') or '')))==anchor_active else 1,
                                table['table_id']))
                        sibling_table=exact_siblings[0] if exact_siblings else None
                        if sibling_table and bool(re.search(r'\d+(?:\.\d+)?~',str(sibling_table.get('table_name') or '')))!=anchor_active:
                            sibling_table=None
                        if sibling_table is None:
                            # Releases of the same survey may carry slightly different
                            # date labels. Match the nearest same-prefix target sibling
                            # after removing dates and the contrast axis.
                            def family_key(value):
                                value=re.sub(r'[0-9.~]+','',self.norm(value).replace(' ',''))
                                for term in axis: value=value.replace(term,'')
                                return value
                            source_key=family_key(anchor['table_name'])
                            family=[]
                            for table in self.tables:
                                candidate_name=self.norm(table['table_name']).replace(' ','')
                                if target not in candidate_name or str(table['table_id']).split('Y',1)[0]!=str(anchor['table_id']).split('Y',1)[0]:
                                    continue
                                candidate_key=family_key(table['table_name'])
                                common=len(set(source_key) & set(candidate_key))
                                period_end=re.sub(r'\D','',str(table.get('period_end_observed') or ''))
                                raw_candidate=str(table.get('table_name') or '')
                                active_series=1 if re.search(r'\d+(?:\.\d+)?~',raw_candidate) else 0
                                family.append((common,active_series,period_end,table))
                            if family:
                                family.sort(key=lambda pair:(-pair[0],-pair[1],-int(pair[2] or 0),pair[3]['table_id']))
                                sibling_table=family[0][3]
                        match=(next((candidate for candidate in ranked if candidate['table_id']==sibling_table['table_id']),None) or self.candidate_shell(sibling_table['table_id'])) if sibling_table else None
                        if match:
                            selected=[anchor,match]
                            return selected[:5]
        dedup=[]; seen=set()
        for item in selected:
            if item['table_id'] not in seen:
                seen.add(item['table_id']); dedup.append(item)
        return dedup[:5] if len(dedup)>=2 else ranked[:1]

    def rank_followup(self, previous_query, current_query, top_k=12):
        """Resolve a follow-up inside the prior table's semantic family.

        The previous table supplies retained context; the current utterance supplies
        replacement axes. Only dictionary tables can enter the result.
        """
        effective_state,operations,effective_query=self.apply_followup_state(previous_query,current_query)
        self.last_followup_trace={
            'previous_state':asdict(self.parse_query_state(previous_query)),
            'parsed_operations':operations,
            'effective_query_state':asdict(effective_state),
            'effective_query':effective_query,
        }
        previous=self.rank(previous_query,top_k=8)
        previous_compact=self.norm(previous_query).replace(' ','')
        def query_core(text):
            compact=self.norm(text).replace(' ','')
            for filler in ('이번에는','이번엔','기준으로','으로변경','로변경','으로바꿔줘','로바꿔줘','변경해주세요','변경해줘','바꿔줘','보여주세요','보여줘','보고싶어','추이를','전체'):
                compact=compact.replace(filler,'')
            return re.sub(r'(을|를|으로|로)$','',compact)
        prior_core=query_core(previous_query)
        if len(prior_core)>=5:
            suffix_matches=[]
            for table in self.tables:
                name=self.norm(table.get('table_name','')).replace(' ','')
                if prior_core in name:
                    extra_qualifier=1 if any(term in name and term not in prior_core for term in ('비중','금리','지역별','산업별','용도별')) else 0
                    suffix_matches.append((extra_qualifier,len(name)-len(prior_core),table))
            if suffix_matches:
                suffix_matches.sort(key=lambda pair:(pair[0],pair[1],pair[2]['table_id']))
                exact_prior=self.candidate_shell(suffix_matches[0][2]['table_id'])
                previous=[exact_prior,*[item for item in previous if item['table_id']!=exact_prior['table_id']]]
        # Keep the scored prior result order. Reordering solely by phrase length lets
        # generic aliases ("결제통화", "잔액") displace the actual prior table.
        if not previous:
            current_compact=self.norm(current_query).replace(' ','')
            direct=[]
            for table in self.tables:
                for term in [table.get('table_name',''),*table.get('aliases',[])]:
                    nt=self.norm(term).replace(' ','')
                    if len(nt)>=4 and nt in current_compact:
                        direct.append((len(nt),table)); break
            if direct:
                direct.sort(key=lambda pair:(-pair[0],pair[1]['table_id']))
                return [self.candidate_shell(direct[0][1]['table_id'])]
            return self.rank(self.merge_followup_query(previous_query,current_query),top_k=top_k)[:1]
        prior=previous[0]; prior_table=self.tables_by_id[prior['table_id']]
        raw=effective_query
        broad=self.rank(raw,top_k=max(80,top_k))
        broad_by_id={item['table_id']:item for item in broad}
        prior_prefix=str(prior['table_id']).split('Y',1)[0]
        prior_domains=set(prior_table.get('domains') or [])
        prior_name=self.norm(prior_table.get('table_name',''))
        prior_tokens={x for x in re.findall(r'[가-힣a-z0-9]+',prior_name) if len(x)>=2}
        current_compact=self.norm(current_query).replace(' ','')
        additive=bool(re.search(r'(같이|함께|추가|도(?=\s*(?:같이|함께|추가|보여)))',str(current_query)))

        # ADD keeps the selected prior series and independently resolves the newly
        # named metric under retained institution/measure qualifiers.
        current_state=self.parse_query_state(current_query)
        if additive and current_state.metrics:
            additions=[]
            for table in self.tables:
                name=self.norm(table.get('table_name','')).replace(' ','')
                if not any(metric in name for metric in current_state.metrics):
                    continue
                qualifier_score=0
                for qualifier in ('예금은행','비은행금융기관','신규취급액','잔액','지역별','업종별'):
                    if qualifier in prior_name.replace(' ','') and qualifier in name:
                        qualifier_score+=1
                additions.append((qualifier_score,table))
            if additions:
                additions.sort(key=lambda pair:(-pair[0],pair[1]['table_id']))
                addition=self.candidate_shell(additions[0][1]['table_id'])
                if addition['table_id']!=prior['table_id']:
                    return [prior,addition]

        # A sufficiently specific name/alias in the new turn means the user has
        # replaced the statistic itself. Prefer the longest trusted catalog phrase.
        explicit_tables=[]
        current_core=query_core(current_query)
        for table in self.tables:
            phrases=[table.get('table_name',''),*table.get('aliases',[])]
            matched=max((len(nt) for term in phrases if (nt:=self.norm(term).replace(' ','')) and len(nt)>=4 and (
                nt in current_compact or (len(current_core)>=4 and (current_core in nt or current_core in nt.replace('전체','')))
            )),default=0)
            if matched:
                explicit_tables.append((matched,table))
        if explicit_tables and not additive:
            explicit_tables.sort(key=lambda pair:(-pair[0],pair[1]['table_id']))
            return [self.candidate_shell(explicit_tables[0][1]['table_id'])]
        if explicit_tables and additive:
            explicit_tables.sort(key=lambda pair:(-pair[0],pair[1]['table_id']))
            addition=self.candidate_shell(explicit_tables[0][1]['table_id'])
            if addition['table_id']!=prior['table_id']:
                return [prior,addition]

        # Replacement follow-ups commonly state only the changed axis. Resolve an
        # exact sibling of the prior trusted table before fuzzy family ranking.
        replacement_axes=[
            ('수출','수입'),('신규취급액','잔액'),('신규','잔액'),
            ('실적','전망'),('명목','시가','실질'),('금액','비중'),
        ]
        prior_compact=self.norm(prior_table.get('table_name','')).replace(' ','')
        for axis in replacement_axes:
            requested=[term for term in axis if term in current_compact]
            if not requested:
                continue
            for source in axis:
                if source not in prior_compact or source in requested:
                    continue
                for target in requested:
                    sibling=prior_compact.replace(source,target)
                    sibling_table=next((table for table in self.tables if self.norm(table.get('table_name','')).replace(' ','')==sibling),None)
                    prior_active=bool(re.search(r'\d+(?:\.\d+)?~',str(prior_table.get('table_name') or '')))
                    if sibling_table and bool(re.search(r'\d+(?:\.\d+)?~',str(sibling_table.get('table_name') or '')))!=prior_active:
                        sibling_table=None
                    if sibling_table is None:
                        family=[]
                        base=prior_compact
                        for term in axis: base=base.replace(term,'')
                        base=re.sub(r'[0-9]+','',base)
                        for table in self.tables:
                            name=self.norm(table.get('table_name','')).replace(' ','')
                            if target not in name or str(table['table_id']).split('Y',1)[0]!=prior_prefix:
                                continue
                            candidate_base=name
                            for term in axis: candidate_base=candidate_base.replace(term,'')
                            candidate_base=re.sub(r'[0-9]+','',candidate_base)
                            overlap=len(set(base)&set(candidate_base))
                            active=bool(re.search(r'\d+(?:\.\d+)?~',str(table.get('table_name') or '')))
                            family.append((overlap,active==prior_active,table))
                        if family:
                            family.sort(key=lambda value:(-value[0],-int(value[1]),value[2]['table_id']))
                            sibling_table=family[0][2]
                    if sibling_table:
                        sibling_name=self.norm(sibling_table.get('table_name','')).replace(' ','')
                        missing_qualifier=any(term in current_compact and term not in sibling_name for term in ('비중','지역별','국가별','산업별','용도별','품목별'))
                        if missing_qualifier:
                            continue
                        replacement=self.candidate_shell(sibling_table['table_id'])
                        return [prior,replacement] if additive and replacement['table_id']!=prior['table_id'] else [replacement]

        # When the new turn explicitly names a different statistic, it is a topic
        # replacement rather than a reason to preserve the old table family.
        current_ranked=self.rank(current_query,top_k=3)
        if current_ranked:
            evidence=current_ranked[0].get('reasons') or []
            explicit_current=any(str(reason).startswith(('table_name','exact_table_phrase','alias:')) for reason in evidence)
            if len(current_compact)>=4 and explicit_current and float(current_ranked[0].get('score') or 0)>=60:
                return [current_ranked[0]]

        def ngrams(text,n=2):
            compact=re.sub(r'[^0-9a-z가-힣]','',self.norm(text))
            return {compact[i:i+n] for i in range(max(0,len(compact)-n+1))}

        prior_grams=ngrams(prior_table.get('table_name',''))

        def fit(item):
            table=self.tables_by_id[item['table_id']]
            name=self.norm(table.get('table_name',''))
            compact=name.replace(' ','')
            tokens={x for x in re.findall(r'[가-힣a-z0-9]+',name) if len(x)>=2}
            shared=len(prior_tokens & tokens)
            grams=ngrams(table.get('table_name',''))
            char_similarity=len(prior_grams & grams)/max(1,len(prior_grams | grams))
            prefix_bonus=30 if str(item['table_id']).split('Y',1)[0]==prior_prefix else 0
            domain_bonus=15 if prior_domains & set(table.get('domains') or []) else 0
            explicit=0
            terms=[table.get('table_name',''),*table.get('aliases',[]),*table.get('semantic_aliases',[])]
            for term in terms:
                nt=self.norm(term).replace(' ','')
                if len(nt)>=2 and nt in current_compact:
                    explicit=max(explicit,min(80,8*len(nt)))
            for marker in ('잔액','비중','신규취급액','품목별','용도별','실질','명목','실적','전망','수출물량','수입물량','수출','수입','주택담보대출','가계대출','신용위험','대출수요','순취득액','자산회전율','손익계산서','지급수단별','경제심리지수','경제심리','국내공급물가','총산출물가'):
                if marker in current_compact and marker in compact:
                    explicit+=110
            if '자산회전율' in current_compact and '자산' in compact and '회전율' in compact:
                explicit+=110
            for dimension in table.get('dimensions') or []:
                for value in dimension.get('values') or []:
                    terms=[value.get('value_name'),value.get('normalized'),*value.get('aliases',[])]
                    if any(len(self.norm(term).replace(' ',''))>=2 and self.norm(term).replace(' ','') in current_compact for term in terms if term):
                        explicit+=80
            prior_end=str(prior_table.get('period_end_observed') or '')[:4]
            candidate_end=str(table.get('period_end_observed') or '')[:4]
            continuity=45 if prior_end and candidate_end==prior_end else 0
            current_year=__import__('datetime').datetime.now().year
            active_bonus=50 if candidate_end and int(candidate_end)>=current_year-2 else 0
            stale_penalty=50 if candidate_end and int(candidate_end)<current_year-8 else 0
            return float(item.get('score') or 0)+prefix_bonus+domain_bonus+continuity+active_bonus-stale_penalty+8*shared+80*char_similarity+explicit

        pool=[]
        for table in self.tables:
            same_family=str(table['table_id']).split('Y',1)[0]==prior_prefix
            same_domain=bool(prior_domains & set(table.get('domains') or []))
            pool.append(dict(broad_by_id.get(table['table_id']) or self.candidate_shell(table['table_id'])))
        ranked=sorted(pool or broad,key=lambda item:(-fit(item),item['table_id']))
        choice=ranked[0] if ranked else prior
        choice_table=self.tables_by_id[choice['table_id']]
        dimension_change=False
        for dimension in choice_table.get('dimensions') or []:
            for value in dimension.get('values') or []:
                terms=[value.get('value_name'),value.get('normalized'),*value.get('aliases',[])]
                if any(len(self.norm(term).replace(' ',''))>=2 and self.norm(term).replace(' ','') in current_compact for term in terms if term):
                    dimension_change=True; break
            if dimension_change: break
        if additive and dimension_change:
            return [choice]
        if additive and choice['table_id']!=prior['table_id']:
            return [prior,choice]
        return [choice]

    def resolve(self, query, confirmed=None, asked_clarifications=None, top_k=8):
        confirmed=confirmed or {}
        asked_clarifications=asked_clarifications or []
        explicit_ids=[tid for tid in self.tables_by_id if self.norm(tid) in self.norm(query)]
        if len(explicit_ids)==1:
            candidates=self._score(query,confirmed=None,top_k=top_k)
            return {'status':'resolved','selected_table':candidates[0],'candidates':candidates,
                    'state':{'original_query':query,'confirmed':confirmed,'confirmed_terms':[],
                             'asked_clarifications':asked_clarifications,'status':'resolved'}}
        compact_query=self.norm(query).replace(' ','')
        # Specific metrics must never be silently substituted with a broader
        # supported family. If the current dictionary has no authoritative table
        # for an explicitly named metric, fail before showing unrelated buttons.
        explicit_metrics=('기준금리','정책금리','콜금리','시장금리','소비자물가지수','소비자물가')
        unsupported=[metric for metric in explicit_metrics if metric in compact_query and metric not in self._catalog_search_text]
        if unsupported:
            return {
              'status':'no_match','selected_table':None,'candidates':[],
              'missing_series':list(dict.fromkeys(unsupported)),
              'state':{'original_query':query,'confirmed':confirmed,'confirmed_terms':self._confirmed_terms(confirmed),
                       'asked_clarifications':asked_clarifications,'status':'no_match'}
            }
        candidates=self._score(query,confirmed=confirmed,top_k=max(top_k,12))
        nq=self.norm(query).replace(' ','')
        catalog_matches=[]
        for table in self.catalog_only_tables:
            if any(self.norm(term).replace(' ','') in nq for term in table.get('terms',[])):
                catalog_matches.append(table)
        grounded=[]
        for candidate in candidates:
            reasons=candidate.get('reasons',[])
            has_table_evidence=any(str(reason).startswith(('table_name','exact_table_phrase','alias:','alias_core:','core_table_phrase:','concept_normalization:','semantic:','public_query:','public_tokens:','public_intent:','table_tokens:','table_anchors:','confirmed:')) for reason in reasons) or len(candidate.get('dimension_hits') or [])>=2
            if has_table_evidence:
                grounded.append(candidate)
        if candidates and not grounded:
            candidates=[]
        top1=candidates[0]['score'] if candidates else 0
        top2=candidates[1]['score'] if len(candidates)>1 else 0
        gap=top1-top2

        clarification=self._clarification(query,candidates,confirmed=confirmed,asked=asked_clarifications)
        auto=self.policy.get('auto_resolve',{})
        clearly_resolved = bool(candidates and top1>=auto.get('min_top1_score',180) and gap>=auto.get('min_gap_to_top2',40))

        # Explicit ambiguity groups take precedence over score dominance. If the query contains
        # a branching everyday concept and the user has not specified the branch, ask.
        if clarification:
            state={
              'original_query':query,'confirmed':confirmed,
              'confirmed_terms':self._confirmed_terms(confirmed),
              'asked_clarifications':asked_clarifications+[clarification['clarification_id']],
              'candidate_tables':[{'table_id':x['table_id'],'table_name':x['table_name'],'score':x['score']} for x in candidates[:top_k]],
              'status':'need_clarification'
            }
            return {'status':'need_clarification',**clarification,'state':state}

        if catalog_matches:
            return {
              'status':'catalog_only','selected_table':catalog_matches[0],
              'candidates':catalog_matches[:top_k],
              'state':{'original_query':query,'confirmed':confirmed,'confirmed_terms':self._confirmed_terms(confirmed),
                       'asked_clarifications':asked_clarifications,'status':'catalog_only'}
            }

        return {
          'status':'resolved' if candidates else 'no_match',
          'selected_table':candidates[0] if candidates else None,
          'candidates':candidates[:top_k],
          'state':{'original_query':query,'confirmed':confirmed,'confirmed_terms':self._confirmed_terms(confirmed),
                   'asked_clarifications':asked_clarifications,'status':'resolved' if candidates else 'no_match'}
        }

    def collect_clarifications(self, query, confirmed=None, top_k=8):
        """Return every currently applicable clarification group for one-screen UI."""
        confirmed=confirmed or {}; candidates=self._score(query,confirmed=confirmed,top_k=max(top_k,12))
        asked=[]; result=[]
        while True:
            item=self._clarification(query,candidates,confirmed=confirmed,asked=asked)
            if not item: break
            # Never display a button that cannot produce a valid dictionary candidate.
            valid=[]
            for option in item.get('options',[]):
                probe=f"{query} {' '.join(option.get('confirmed_terms') or [option.get('value','')])}".strip()
                executable=bool(self._score(probe,confirmed=None,top_k=1))
                if not executable:
                    for prior in result:
                        for prior_option in prior.get('options',[]):
                            prior_terms=' '.join(prior_option.get('confirmed_terms') or [prior_option.get('value','')])
                            if self._score(f"{probe} {prior_terms}",confirmed=None,top_k=1):
                                executable=True; break
                        if executable: break
                if executable: valid.append(option)
            if valid:
                item=dict(item); item['options']=valid; result.append(item)
            asked.append(item['clarification_id'])
        return result

    def apply_clarification(self, prior_state, clarification_id, value, top_k=8):
        confirmed=dict(prior_state.get('confirmed') or {})
        confirmed[clarification_id]=value
        asked=list(prior_state.get('asked_clarifications') or [])
        return self.resolve(prior_state['original_query'],confirmed=confirmed,asked_clarifications=asked,top_k=top_k)

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('dictionary'); ap.add_argument('query')
    args=ap.parse_args()
    r=StatLanguageResolver(args.dictionary)
    print(json.dumps(r.resolve(args.query),ensure_ascii=False,indent=2))
