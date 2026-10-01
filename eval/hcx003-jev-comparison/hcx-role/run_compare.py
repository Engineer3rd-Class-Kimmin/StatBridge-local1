import argparse, json, os, re, time, math, statistics, urllib.request, urllib.error
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parent

def load_env(p):
    if not Path(p).exists(): return
    for line in Path(p).read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip())

def post(url, body, headers, timeout=120):
    data=json.dumps(body,ensure_ascii=False).encode('utf-8'); req=urllib.request.Request(url,data=data,headers=headers,method='POST')
    t=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: raw=r.read().decode('utf-8'); status=r.status
    except urllib.error.HTTPError as e:
        raw=e.read().decode('utf-8','replace'); status=e.code
    return status,json.loads(raw),time.perf_counter()-t

def json_obj(text):
    text=str(text or '').strip(); text=re.sub(r'^```(?:json)?\s*','',text,flags=re.I); text=re.sub(r'\s*```$','',text)
    try: x=json.loads(text); return x if isinstance(x,dict) else {}
    except: pass
    m=re.search(r'\{.*\}',text,re.S)
    if m:
        try: x=json.loads(m.group(0)); return x if isinstance(x,dict) else {}
        except: pass
    return {}

def hcx_call(query,prompt):
    key=os.environ['HCX_API_KEY']; url=os.getenv('HCX_ENDPOINT','https://clovastudio.stream.ntruss.com/v1/chat-completions/HCX-003')
    body={'messages':[{'role':'system','content':prompt['system']},{'role':'user','content':prompt['user_template'].format(query=query)}],
          'temperature':0.01,'topP':0.1,'topK':0,'repetitionPenalty':1.05,'maxTokens':700}
    st,p,lat=post(url,body,{'Authorization':f'Bearer {key}','Content-Type':'application/json'})
    result=(p.get('result') or {}) if isinstance(p,dict) else {}; msg=result.get('message') or {}
    text=msg.get('content') or ''
    return {'status':st,'latency_s':lat,'raw':p,'parsed':json_obj(text),'usage':result.get('usage') or {}}

def jev_call(case, model, lex):
    key=os.environ['TYPESAFE_API_KEY']; ep=os.getenv('TYPESAFE_ENDPOINT','https://api.typesafe.ai').rstrip('/')
    domain_labels=sorted(lex['concept_lexicon'].keys())
    intents=sorted(lex['analysis_intent_lexicon'].keys())
    quals=sorted(lex['qualifier_lexicon'].keys())
    qs={
      'domain':{'type':'choice','instructions':'사용자 질문의 핵심 통계 분야를 하나 선택하라. 다중 지표 질문이면 가장 먼저 언급된 지표의 분야를 선택하라.','options':{k:lex['concept_lexicon'][k]['canonical'] for k in domain_labels}},
      'frequency':{'type':'choice','instructions':'질문에 명시된 자료 주기를 선택하라. 명시되지 않았으면 NONE.','options':{'M':'월별/매달','Q':'분기별','Y':'연간/연도별','NONE':'명시 없음'}},
      'series_count':{'type':'choice','instructions':'질문에서 서로 독립적으로 검색해야 하는 명시적 통계 지표의 개수를 선택하라. 단일 지표면 0.','options':{'0':'단일 지표','2':'2개','3':'3개','4':'4개','5':'5개'}}
    }
    for k in intents:
        qs['intent__'+k]={'type':'noul','instructions':f"이 질문에 분석 의도 '{k}'가 명시되어 있는가?"}
    for k in quals:
        qs['qual__'+k]={'type':'noul','instructions':f"이 질문에 조건/기준 '{k}'가 명시되어 있는가?"}
    body={'state':case['query'],'model':model,'questions':qs}
    st,p,lat=post(ep+'/v1/systemone',body,{'Authorization':f'Bearer {key}','Content-Type':'application/json','User-Agent':'StatBridge-HCX-Jev-Eval/1.0'})
    return {'status':st,'latency_s':lat,'raw':p,'answers':(p.get('answers') or {}) if isinstance(p,dict) else {},'usage':p.get('usage') or {} if isinstance(p,dict) else {}}

def norm(s): return re.sub(r'\s+','',str(s or '').lower())
def all_hcx_text(p):
    parts=[]
    for k in ['normalized_query','concepts','subjects','measures','time_terms','comparison_terms','qualifiers']:
        v=p.get(k,[])
        if isinstance(v,list): parts.extend(map(str,v))
        elif v: parts.append(str(v))
    for s in p.get('series') or []:
        if isinstance(s,dict): parts += [str(s.get('label','')),str(s.get('query',''))]
    return ' '.join(parts)

def hcx_domain(parsed,lex):
    tx=norm(all_hcx_text(parsed)); scores={}
    for k,v in lex['concept_lexicon'].items():
        terms=[v.get('canonical',''),*(v.get('aliases') or [])]
        scores[k]=sum(max(1,len(norm(t))) for t in terms if norm(t) and norm(t) in tx)
    m=max(scores.values() or [0]); return max(scores,key=scores.get) if m>0 else None

def hcx_multilabel(parsed, group):
    tx=norm(' '.join(map(str, parsed.get('comparison_terms') or []))+' '+str(parsed.get('normalized_query') or '')+' '+' '.join(map(str,parsed.get('qualifiers') or [])))
    return sorted([k for k,terms in group.items() if any(norm(t) and norm(t) in tx for t in terms)])

def hcx_freq(parsed):
    tx=norm(' '.join(map(str,parsed.get('time_terms') or []))+' '+str(parsed.get('normalized_query') or ''))
    if '월별' in tx or '매달' in tx: return 'M'
    if '분기' in tx: return 'Q'
    if '연간' in tx or '연도별' in tx or '년별' in tx: return 'Y'
    return None

def ans_choice(a):
    if not isinstance(a,dict): return None
    return a.get('choice')
def ans_yes(a):
    if not isinstance(a,dict): return False
    x=a.get('noul')
    return isinstance(x,(int,float)) and not isinstance(x,bool) and float(x)>=0.5

def f1(pred,gold):
    p=set(pred); g=set(gold)
    if not p and not g:return 1.0
    if not p or not g:return 0.0
    tp=len(p&g); pr=tp/len(p); rc=tp/len(g); return 2*pr*rc/(pr+rc) if pr+rc else 0.0

def pct(x): return round(100*x,2)
def percentile(xs,p):
    if not xs:return None
    ys=sorted(xs); i=(len(ys)-1)*p; lo=int(math.floor(i)); hi=int(math.ceil(i))
    return ys[lo] if lo==hi else ys[lo]+(ys[hi]-ys[lo])*(i-lo)

def score_record(case,pred,kind):
    # domain: for multi-metric Jev rule chooses first domain; HCX may mention several; core score accepts any expected domain.
    dom_ok=pred.get('domain') in set(case.get('expected_domains') or [])
    intent=f1(pred.get('intents') or [],case.get('expected_intents') or [])
    qual=f1(pred.get('qualifiers') or [],case.get('expected_qualifiers') or [])
    ef=case.get('expected_frequency'); freq_ok=None if ef is None else pred.get('frequency')==ef
    series_ok=int(pred.get('series_count') or 0)==int(case.get('expected_series_count') or 0)
    vals=[1.0 if dom_ok else 0.0,intent,qual,1.0 if series_ok else 0.0]
    if freq_ok is not None: vals.append(1.0 if freq_ok else 0.0)
    return {'domain_correct':dom_ok,'intent_f1':intent,'qualifier_f1':qual,'frequency_correct':freq_ok,'series_count_correct':series_ok,'role_score':sum(vals)/len(vals)}

def run_model(model,cases,lex,prompt,repeat,outdir):
    rows=[]
    for rep in range(1,repeat+1):
      for i,c in enumerate(cases,1):
        if model=='HCX-003':
            r=hcx_call(c['query'],prompt); p=r['parsed']
            pred={'domain':hcx_domain(p,lex),'intents':hcx_multilabel(p,lex['analysis_intent_lexicon']),
                  'qualifiers':hcx_multilabel(p,lex['qualifier_lexicon']),'frequency':hcx_freq(p),
                  'series_count':len(p.get('series') or [])}
            valid=bool(p)
        else:
            r=jev_call(c,model,lex); a=r['answers']
            pred={'domain':ans_choice(a.get('domain')),
                  'intents':sorted([k for k in lex['analysis_intent_lexicon'] if ans_yes(a.get('intent__'+k))]),
                  'qualifiers':sorted([k for k in lex['qualifier_lexicon'] if ans_yes(a.get('qual__'+k))]),
                  'frequency':None if ans_choice(a.get('frequency')) in (None,'NONE') else ans_choice(a.get('frequency')),
                  'series_count':int(ans_choice(a.get('series_count')) or 0)}
            valid=bool(a)
        sc=score_record(c,pred,model); row={'model':model,'repeat':rep,'case_id':c['case_id'],'query':c['query'],'http_status':r['status'],'latency_s':r['latency_s'],'valid':valid,'pred':pred,'score':sc,'usage':r.get('usage') or {},'raw':r['raw']}
        rows.append(row)
        print(f"{model} repeat {rep} {i}/{len(cases)} score={sc['role_score']:.3f} status={r['status']}",flush=True)
        time.sleep(float(os.getenv('EVAL_DELAY_SECONDS','0.3')))
    p=outdir/(model.replace('/','_')+'.jsonl')
    with p.open('w',encoding='utf-8') as f:
        for x in rows:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    return rows

def summarize(rows,cases):
    n=len(rows); scores=[x['score'] for x in rows]; lats=[x['latency_s'] for x in rows]
    # consistency = same pred across repeats per case
    by=defaultdict(list)
    for x in rows: by[x['case_id']].append(json.dumps(x['pred'],ensure_ascii=False,sort_keys=True))
    cons=sum(len(set(v))==1 for v in by.values())/len(by) if by else 0
    freqs=[x['score']['frequency_correct'] for x in rows if x['score']['frequency_correct'] is not None]
    return {'n_runs':n,'n_cases':len(cases),'role_score_pct':pct(sum(s['role_score'] for s in scores)/n),
      'domain_accuracy_pct':pct(sum(s['domain_correct'] for s in scores)/n),
      'intent_f1_pct':pct(sum(s['intent_f1'] for s in scores)/n),
      'qualifier_f1_pct':pct(sum(s['qualifier_f1'] for s in scores)/n),
      'frequency_accuracy_pct':pct(sum(bool(x) for x in freqs)/len(freqs)) if freqs else None,
      'series_count_accuracy_pct':pct(sum(s['series_count_correct'] for s in scores)/n),
      'valid_output_pct':pct(sum(x['valid'] and x['http_status']==200 for x in rows)/n),
      'api_error_pct':pct(sum(x['http_status']!=200 for x in rows)/n),
      'consistency_pct':pct(cons),'latency_p50_s':round(percentile(lats,.5),3),'latency_p95_s':round(percentile(lats,.95),3)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--models',default='HCX-003,jev-latest,jev-preview'); ap.add_argument('--repeat',type=int,default=3); ap.add_argument('--limit',type=int); ap.add_argument('--env',default='.env'); args=ap.parse_args()
    load_env(args.env)
    cases=[json.loads(x) for x in (ROOT/'goldset_statbridge_hcx003_role_100.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    if args.limit: cases=cases[:args.limit]
    lex=json.load(open(ROOT/'canonical_lexicons.json',encoding='utf-8')); prompt=json.load(open(ROOT/'hcx003_prompt_frozen.json',encoding='utf-8'))
    stamp=time.strftime('%Y%m%d_%H%M%S'); od=ROOT/'results'/stamp; od.mkdir(parents=True)
    summaries={}
    for m in [x.strip() for x in args.models.split(',') if x.strip()]:
        rows=run_model(m,cases,lex,prompt,args.repeat,od); summaries[m]=summarize(rows,cases)
    (od/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    # CSV-ish markdown report
    headers=['model','role_score_pct','domain_accuracy_pct','intent_f1_pct','qualifier_f1_pct','frequency_accuracy_pct','series_count_accuracy_pct','valid_output_pct','consistency_pct','latency_p50_s','latency_p95_s','api_error_pct']
    lines=['|'+ '|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']
    for m,s in summaries.items(): lines.append('|'+ '|'.join(str(m if h=='model' else s.get(h)) for h in headers)+'|')
    (od/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summaries,ensure_ascii=False,indent=2)); print('RESULT_DIR='+str(od))
if __name__=='__main__': main()
