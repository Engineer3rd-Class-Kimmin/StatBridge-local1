"""Owner-scoped, explicit user-file inputs alongside approved KOSIS API data."""
from statbridge_mcp.observability import observed
import copy,csv,hashlib,io,json,re,time,shutil
from pathlib import Path
from threading import RLock
from uuid import uuid4
from derived_catalog_tools import DerivedCatalogTools
from derived_indicator_runtime import DerivedIndicatorRuntime
from derived_indicator_planner import DerivedIndicatorPlanner
from dynamic_expression_executor import Series,finite,source_series
from expression_tree_schema import ExpressionPlan,SourceSelection,MAX_SOURCES
from expression_tree_validator import DerivedValidationError,canonical_bound,period_index,shift_period,MAX_POINTS
from derived_file_inputs import decode_file, FIELDS, FORMATS
from derived_input_guidance import input_guidance

FILE_PREFIX='USER_FILE:'
UPLOAD_MAX_POINTS=6000

def source_key(source):
    return json.dumps([source.table_id,source.item_id,source.classifications,source.frequency],sort_keys=True)

class SupplementalCatalog(DerivedCatalogTools):
    allow_file_inputs=True
    def __init__(self,base,files,cached=None):
        super().__init__(base.store);self.base=base;self.files=files;self.cached=cached or {}
        self.version=hashlib.sha256((base.version+json.dumps({k:v['sha256'] for k,v in files.items()},sort_keys=True)).encode()).hexdigest()

    def file_metadata(self):
        return [self.get_table_metadata(FILE_PREFIX+alias) for alias in self.files]

    def get_table_metadata(self,tid):
        if not tid.startswith(FILE_PREFIX):
            meta=self.base.get_table_metadata(tid)
            units=[]
            for value in self.cached.values():
                source=SourceSelection.model_validate(value['source'])
                if source.table_id==tid:
                    units.append({'selection':source.model_dump(),'unit':source_series(source,value['response']).unit})
            return {**meta,'server_verified_observation_units':units} if units else meta
        alias=tid[len(FILE_PREFIX):]
        if alias not in self.files:raise DerivedValidationError('현재 계정이 제공하지 않은 파일 입력입니다.')
        f=self.files[alias];periods=[r['PRD_DE'] for r in f['rows']]
        return {'table_id':tid,'table_name':f['required_data'],'organization_id':'user_file','metadata_origin':'user_uploaded_file',
                'items':[{'item_id':'value','item_name':f['required_data']}],'dimensions':[],
                'frequencies':[f['frequency']],'periods':[{'frequency':f['frequency'],'start_period':min(periods),'end_period':max(periods)}],
                'comments':['사용자가 제공한 자료입니다. 한국은행·KOSIS 검증 자료가 아닙니다.'],'unit':f['unit'],'sha256':f['sha256']}

    def search_approved_tables(self,query,limit=6):
        return self.base.search_approved_tables(query,limit)+[{'table_id':m['table_id'],'table_name':m['table_name'],'score':0} for m in self.file_metadata()]

    def selection_text(self,source):
        text=super().selection_text(source)
        if source.table_id.startswith(FILE_PREFIX):
            text+=' · 사용자 선언 단위: '+self.files[source.table_id[len(FILE_PREFIX):]]['unit']
        else:
            saved=self.cached.get(source_key(source))
            if saved:text+=' · 실제 API 확인 단위: '+source_series(source,saved['response']).unit
        return text

class SourceInputsRequired(DerivedValidationError):
    def __init__(self,missing,responses):
        super().__init__('일부 필수 원자료를 확보하지 못했습니다.',failure_kind='source_input_required')
        self.missing=missing;self.responses=responses

class SupplementalRuntime(DerivedIndicatorRuntime):
    def __init__(self,gateway,client,evidence_directory,catalog,files,cached):
        super().__init__(gateway,client,evidence_directory)
        self.catalog=catalog;self.files=files;self.prefetched=cached
        self.planner=DerivedIndicatorPlanner(client,catalog)

    def _plan_uncached(self,query,**kwargs):
        # The owner-scoped workspace already has validated files and API inputs.
        # Continue arithmetic planning with those selections, never rediscovery.
        if not kwargs.get('previous') and not kwargs.get('verified_sources'):
            kwargs['verified_sources']=[v['source'] for v in self.prefetched.values()]
        return self.planner.plan(query,**kwargs)

    def source_origin(self,source):return 'user_file' if source.table_id.startswith(FILE_PREFIX) else 'kosis_api'

    def input_series(self,source,response):
        if self.source_origin(source)=='kosis_api':return source_series(source,response)
        self.catalog.validate_source_selection(source)
        f=self.files[source.table_id[len(FILE_PREFIX):]]
        if response.get('source')!='user_file' or response.get('sha256')!=f['sha256'] or response.get('rows')!=f['rows']:
            raise DerivedValidationError('사용자 파일의 출처 또는 내용 무결성이 일치하지 않습니다.')
        return Series({r['PRD_DE']:finite(r['DT']) for r in f['rows']},f['unit'],f['frequency'],[source.alias])

    def fetch(self,plan,bounds):
        bounds=self.validate(plan);responses={};missing=[]
        for source in plan.sources:
            if self.source_origin(source)=='user_file':
                response=self.files[source.table_id[len(FILE_PREFIX):]]
            else:
                cached=self.prefetched.get(source_key(source))
                if cached and cached['first']<=bounds['fetch_start'] and cached['last']>=bounds['fetch_end']:
                    response=cached['response']
                else:
                    try:
                        response=self.gateway.get_statistics(table_id=source.table_id,item_id=source.item_id,classifications=source.classifications,
                            frequency=source.frequency,start_period=bounds['fetch_start'],end_period=bounds['fetch_end'],prefer_local=False,allow_fallback=False)
                    except Exception as exc:
                        response={'status':'failed','source':'none','error_type':type(exc).__name__}
            try:self.input_series(source,response);responses[source.alias]=response
            except DerivedValidationError as exc:
                missing.append({'alias':source.alias,'required_data':source.required_data,'reason':str(exc),'unit':'',
                                'frequency':source.frequency,'lookback':bounds['lookback']})
        if missing:raise SourceInputsRequired(missing,responses)
        return responses

    def revise(self,result,instruction=None,*,node_patch=None,start=None,end=None):
        prior=result['_derived']['record'];data=copy.deepcopy(prior['plan'])
        if instruction:
            planning=self.plan(instruction,previous=data,start_period=start,end_period=end)
            if planning['status']!='planned':raise DerivedValidationError(planning.get('question') or '추가 자료 또는 조건이 필요합니다.')
        else:
            freq=data['sources'][0]['frequency']
            if start:data['start_period']=canonical_bound(start,freq)
            if end:data['end_period']=canonical_bound(end,freq)
            if node_patch:raise DerivedValidationError('파일 결합 지표의 계산식은 자연어로 수정해 의미를 다시 검증해 주세요.')
            data['economic_definition']=f"표시 기간: {data['start_period']} ~ {data['end_period']}. API·사용자 파일의 동일한 원자료와 검증된 계산식을 유지합니다."
            planning={'status':'planned','plan':ExpressionPlan.model_validate(data),'usage':[],'semantic_review':prior.get('semantic_review')}
        bounds=self.validate(planning['plan']);responses=self.fetch(planning['plan'],bounds)
        revised=self.calculate(result['query'],planning,responses,previous=result['derivedEvidence']['calculationId'])
        revised['_derived']['supplemental_workspace_id']=result['_derived']['supplemental_workspace_id']
        return revised

class DerivedSupplementService:
    def __init__(self,base_runtime,directory):
        self.base=base_runtime;self.directory=Path(directory);self.lock=RLock()
        self.pending={};self.raw_uploads={}
        self.clear_legacy_pending()

    def clear_legacy_pending(self):
        """Discard only unfinished upload caches; completed evidence remains durable."""
        root=self.directory.resolve()
        for path in self.directory.glob('user-*/*/workspace.json'):
            if not re.fullmatch(r'user-\d+',path.parent.parent.name) or not re.fullmatch(r'[a-f0-9]{32}',path.parent.name):continue
            if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():continue
            if not path.parent.resolve().is_relative_to(root):continue
            try:w=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            if not w.get('calculation_id'):shutil.rmtree(path.parent)

    def expire_pending(self):
        for key,w in list(self.pending.items()):
            if time.time()-w['created']>12*3600:
                self.pending.pop(key,None);self.raw_uploads.pop(key,None)

    def path(self,owner,identifier):
        if not re.fullmatch(r'[a-f0-9]{32}',identifier) or not re.fullmatch(r'\d+',str(owner)):raise KeyError('자료 보완 작업을 찾을 수 없습니다.')
        return self.directory/('user-'+str(owner))/identifier/'workspace.json'

    def load(self,owner,identifier):
        path=self.path(owner,identifier)
        self.expire_pending()
        if (owner,identifier) in self.pending:return copy.deepcopy(self.pending[(owner,identifier)])
        if not path.exists():raise KeyError('자료 보완 작업을 찾을 수 없습니다.')
        if path.stat().st_size>20_000_000:raise DerivedValidationError('보완 작업 저장 한도를 초과했습니다.')
        w=json.loads(path.read_text(encoding='utf8'))
        if not w.get('calculation_id'):raise KeyError('자료 보완 작업이 만료됐습니다. 질문을 다시 입력해 주세요.')
        if w['owner']!=owner:raise KeyError('자료 보완 작업을 찾을 수 없습니다.')
        return w

    def save(self,w):
        path=self.path(w['owner'],w['id'])
        w['revision']=w.get('revision',0)+1
        content=json.dumps(w,ensure_ascii=False,allow_nan=False)
        if len(content.encode())>20_000_000:raise DerivedValidationError('보완 작업 저장 한도를 초과했습니다.')
        key=(w['owner'],w['id'])
        if not w.get('calculation_id'):
            self.expire_pending();self.pending[key]=json.loads(content)
            return
        # Completed calculations retain reproducible inputs for saved analyses.
        path.parent.mkdir(parents=True,exist_ok=True)
        for filename,raw_bytes in self.raw_uploads.get(key,{}).items():
            folder=path.parent/'files';folder.mkdir(exist_ok=True)
            target=folder/filename
            if not target.exists():target.write_bytes(raw_bytes)
        revision=path.with_name(f"revision-{w['revision']}-{uuid4().hex}.json")
        with revision.open('x',encoding='utf8') as f:f.write(content)
        temporary=path.with_suffix('.tmp');temporary.write_text(content,encoding='utf8');temporary.replace(path)
        self.pending.pop(key,None);self.raw_uploads.pop(key,None)

    def runtime(self,w):
        catalog=SupplementalCatalog(self.base.catalog,w['files'],w['prefetched'])
        return SupplementalRuntime(self.base.gateway,self.base.planner.client,self.base.evidence.directory,catalog,w['files'],w['prefetched'])

    def start(self,owner,query,start=None,end=None,*,planning=None):
        from derived_private_inputs import private_metric
        if private_metric(query):
            raise DerivedValidationError('회사·개인 원자료는 지원하지 않습니다. KOSIS 제공 자료로 질문을 다시 작성해 주세요.')
        if len(query)>8000:raise DerivedValidationError('질문 길이 한도를 초과했습니다.')
        self.expire_pending()
        if sum(key[0]==owner for key in self.pending)>=20:raise DerivedValidationError('자료 보완 작업은 계정당 최대 20개입니다.')
        w={'id':uuid4().hex,'owner':owner,'query':query,'files':{},'prefetched':{},'missing':[],'input_slots':{},
           'start':start,'end':end,'created':time.time(),'revision':0,'attempts':[]}
        return self.advance(w,planning=planning)

    def from_assessment(self,owner,query,assessment):
        """Only server-owned API checks may open concrete upload slots."""
        sources=[s for s in assessment.get('sources',[]) if s.get('role') != 'definition_only']
        if assessment.get('status')!='checked' or not sources or len(sources)>MAX_SOURCES:
            raise DerivedValidationError('실제 API 확인을 완료한 요청에서 파일 연결을 시작해 주세요.')
        from derived_support_policy import assessment_scope, unsupported, verification_pending
        scope=assessment_scope(assessment)
        if scope=='unsupported':return unsupported(query)
        if scope=='verification_pending':return verification_pending(query)
        if any(s['status'] not in {'available','not_found'} for s in sources):return verification_pending(query)
        if scope=='api_only':return self.start(owner,query)
        # Reuse exact server-verified selections and requirements, not a new GPT plan.
        periods=[s.get('periods',[]) for s in sources]
        if any(len(ps)!=1 or ps[0].get('frequency') not in {'D','M','Q','S','Y'} for ps in periods):
            return verification_pending(query)
        signature=lambda ps:(ps[0]['start'],ps[0]['end'],ps[0]['frequency'])
        if len({signature(ps) for ps in periods})!=1:return verification_pending(query)
        available=[];missing=[]
        for i,source in enumerate(sources):
            alias=f'input_{i+1:02d}'
            if source['status']=='available':
                selection=SourceSelection.model_validate({**source['selection'],'alias':alias})
                self.base.catalog.validate_source_selection(selection)
                available.append(selection.model_dump())
            else:
                missing.append({'alias':alias,'required_data':source['name'],'reason':source['reason'],
                    'unit':source.get('unit',''),'frequency':periods[i][0]['frequency'],'lookback':0})
        planning={'status':'inputs_required','start_period':periods[0][0]['start'],'end_period':periods[0][0]['end'],
                  'available_sources':available,'missing':missing,'usage':[],'searches':[],
                  'calculation_guide':assessment.get('calculationGuide','')+' 실제 DAG 검증에서 시차·이동평균 이력이 필요하면 과거 자료를 추가로 요청합니다.'}
        return self.start(owner,query,planning=planning)

    @observed('file.configure', 'span')
    def configure_comparison(self,owner,identifier,settings):
        from expression_tree_schema import ComparisonPeriod
        from calendar import monthrange
        w=self.load(owner,identifier)
        if not w.get('comparison_required'):raise DerivedValidationError('기간 비교 요청이 아닙니다.')
        periods=[ComparisonPeriod.model_validate(p) for p in settings['periods']]
        originals=w['source_preflight']['sources'][0]['periods']
        if len(periods)!=len(originals):raise DerivedValidationError('원래 비교기간 개수와 다릅니다.')
        for p,original in zip(periods,originals):
            if p.label!=original['label']:raise DerivedValidationError('비교기간 이름을 변경할 수 없습니다.')
            for actual,key in [(p.start,'start'),(p.end,'end')]:
                requested=original[key]
                if not actual.startswith(requested):raise DerivedValidationError('요청한 비교기간과 입력 날짜가 다릅니다.')
                period_index(actual,'D')
            if p.start>=p.end:raise DerivedValidationError('시작일과 종료일을 확인해 주세요.')
            if len(original['start'])==8 and p.basis!='exact':raise DerivedValidationError('명시한 날짜는 특정일 기준으로 비교해 주세요.')
            if p.basis=='monthly_mean' and (p.start[-2:]!='01' or int(p.end[-2:])!=monthrange(int(p.end[:4]),int(p.end[4:6]))[1]):
                raise DerivedValidationError('월평균 비교는 시작 월 전체와 종료 월 전체를 지정해 주세요.')
        aliases=list(w['input_slots']);declarations=settings['series']
        if [s['alias'] for s in declarations]!=aliases:raise DerivedValidationError('입력 순서 또는 자료 목록이 다릅니다.')
        labels=[s['label'] for s in declarations]
        if len(set(labels))!=len(labels) or any(not s.strip() or len(s)>100 for s in labels):raise DerivedValidationError('서로 다른 가로축 이름을 입력해 주세요.')
        if not settings['average_aliases'] or len(set(settings['average_aliases']))!=len(settings['average_aliases']) or not set(settings['average_aliases'])<=set(aliases):
            raise DerivedValidationError('평균선에 포함할 대상을 선택해 주세요.')
        if not settings['complete_observations_confirmed']:raise DerivedValidationError('월평균 자료의 원자료 완전성을 확인해 주세요.')
        for s in declarations:
            if s['direction'] not in {'direct','inverse'}:raise DerivedValidationError('원자료 방향과 계산 방향을 선택해 주세요.')
            f=w['files'].get(s['alias'])
            if not f:raise DerivedValidationError('파일을 먼저 연결해 주세요.')
            unit=f['unit'].replace(' ','').upper()
            if '/' in unit and 'USD' in unit:
                left,right=unit.split('/',1)
                expected='direct' if left=='USD' and right!='USD' else 'inverse' if right=='USD' and left!='USD' else None
                if expected is None or s['direction']!=expected:raise DerivedValidationError(f'{s["label"]}: 파일 단위({f["unit"]})와 선택한 환율 방향이 다릅니다.')
            elif re.search(r'DXY|USDX|달러.*(?:인덱스|지수)',w['input_slots'][s['alias']]['required_data'],re.I) and s['direction']!='direct':
                raise DerivedValidationError('DXY 원지수는 지수 자체의 변화율로 계산해 주세요.')
        w['comparison_settings']={**settings,'periods':[p.model_dump() for p in periods]}
        w['start']=min(p.start for p in periods);w['end']=max(p.end for p in periods)
        self.save(w);return self.public(w)

    @observed('file.validate', 'span')
    def advance(self,w,*,planning=None):
        query=w['query']
        if w.get('comparison_required') and not w.get('prefetched'):
            from derived_support_policy import unsupported
            return unsupported(query)
        runtime=self.runtime(w)
        if w.get('comparison_required'):
            if not w.get('comparison_settings'):raise DerivedValidationError('계산 기준·원자료 방향·평균 대상을 먼저 저장해 주세요.')
        if w.get('comparison_settings'):
            from derived_period_comparison import plan_comparison
            fingerprint=hashlib.sha256(json.dumps({'query':w['query'],'settings':w['comparison_settings'],
                'files':{a:{k:f[k] for k in ('sha256','unit','frequency')} for a,f in w['files'].items()},
                'catalog':runtime.catalog.version},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            cached=w.get('validated_comparison_plan')
            if cached and cached['fingerprint']==fingerprint:
                plan=ExpressionPlan.model_validate(cached['plan']);runtime.validate(plan)
                planning={'status':'planned','plan':plan,'usage':[],'semantic_review':cached['semantic_review'],
                          'searches':[],'plan_cache_hit':True}
            else:
                planning=plan_comparison(runtime,query,w['comparison_settings'])
                w['validated_comparison_plan']={'fingerprint':fingerprint,'plan':planning['plan'].model_dump(),
                    'semantic_review':planning['semantic_review']}
                self.save(w)
        elif planning is None:planning=runtime.plan(query,start_period=w['start'],end_period=w['end'])
        w['attempts'].append({k:v for k,v in planning.items() if k!='plan'})
        if planning.get('unsupported'):
            return planning
        if planning['status']=='inputs_required':
            from derived_support_policy import unsupported, verification_pending
            if not any(not s['table_id'].startswith(FILE_PREFIX) for s in planning['available_sources']):
                return unsupported(query)
            w['calculation_guide']=planning.get('calculation_guide','')
            w['start'],w['end']=planning['start_period'],planning['end_period'];w['missing']=planning['missing']
            selected_keys={source_key(SourceSelection.model_validate(s)) for s in planning['available_sources'] if not s['table_id'].startswith(FILE_PREFIX)}
            w['prefetched']={key:value for key,value in w['prefetched'].items() if key in selected_keys}
            # Fetch every confirmed API source, even when another input is missing.
            for data in planning['available_sources']:
                s=SourceSelection.model_validate(data);runtime.catalog.validate_source_selection(s)
                if s.table_id.startswith(FILE_PREFIX):continue
                first=shift_period(canonical_bound(w['start'],s.frequency),s.frequency,-max(x['lookback'] for x in w['missing']))
                last=canonical_bound(w['end'],s.frequency)
                if period_index(last,s.frequency)-period_index(first,s.frequency)+1>MAX_POINTS:raise DerivedValidationError('요청 관측 수 한도를 초과했습니다.')
                try:
                    response=runtime.gateway.get_statistics(table_id=s.table_id,item_id=s.item_id,classifications=s.classifications,frequency=s.frequency,
                        start_period=first,end_period=last,prefer_local=False,allow_fallback=False)
                    source_series(s,response)
                    w['prefetched'][source_key(s)]={'source':data,'first':first,'last':last,'response':response}
                except Exception as exc:
                    reason=str(exc) if isinstance(exc,DerivedValidationError) else f'API 조회 실패 ({type(exc).__name__}). 자료 부재로 단정하지 않습니다.'
                    if s.alias not in {x['alias'] for x in w['missing']}:
                        w['missing'].append({'alias':s.alias,'required_data':s.required_data,'reason':reason,'unit':'','frequency':s.frequency,'lookback':0})
            if not w['prefetched']:
                return verification_pending(query)
            if len(w['missing'])+len(w['prefetched'])>MAX_SOURCES:raise DerivedValidationError('입력 자료 개수 한도를 초과했습니다.')
            w.setdefault('input_slots',{}).update({s['alias']:s for s in w['missing']})
            self.save(w);return self.public(w)
        if planning['status']=='unavailable' and not w.get('prefetched'):
            from derived_support_policy import unsupported
            return {**unsupported(query),'usage':planning.get('usage',[]),'searches':planning.get('searches',[])}
        if planning['status']!='planned':
            w['question']=planning.get('question') or '자료의 의미·단위·기간을 확인해 주세요.';self.save(w);return self.public(w)
        w['last_plan']=planning['plan'].model_dump()
        if w.get('comparison_settings'):
            from derived_period_comparison import validate_contract
            validate_contract(planning['plan'],w['comparison_settings'])
        try:
            responses=runtime.fetch(planning['plan'],runtime.validate(planning['plan']))
        except SourceInputsRequired as exc:
            w['start'],w['end']=planning['plan'].start_period,planning['plan'].end_period;w['missing']=exc.missing
            for s in planning['plan'].sources:
                if s.alias in exc.responses and not s.table_id.startswith(FILE_PREFIX):
                    bounds=runtime.validate(planning['plan']);w['prefetched'][source_key(s)]={'source':s.model_dump(),'first':bounds['fetch_start'],'last':bounds['fetch_end'],'response':exc.responses[s.alias]}
            if not w['prefetched']:
                from derived_support_policy import verification_pending
                return verification_pending(query)
            w.setdefault('input_slots',{}).update({s['alias']:s for s in w['missing']})
            self.save(w);return self.public(w)
        try:
            planning['source_preflight']=w.get('source_preflight')
            planning['comparison_settings']=w.get('comparison_settings')
            result=runtime.calculate(w['query'],planning,responses,previous=w.get('calculation_id'))
        except DerivedValidationError as exc:
            w['question']=str(exc)
            runtime.evidence.save({'query':w['query'],'status':'failed','stage':'supplement_calculation',
                'plan':w['last_plan'],'error':str(exc),'usage':planning.get('usage',[]),'workspace_id':w['id']})
            self.save(w)
            raise
        result['_derived']['supplemental_workspace_id']=w['id'];w['missing']=[];w.pop('question',None);w['calculation_id']=result['derivedEvidence']['calculationId'];self.save(w)
        return {'status':'calculated','result':result,'workspace':self.public(w)}

    def public(self,w):
        if (w.get('missing') or w.get('files')) and not w.get('prefetched'):
            from derived_support_policy import unsupported
            return unsupported(w['query'])
        return {'status':'need_details','analysisKind':'derived','query':w['query'],'interpretedQuery':w['query'],
                'summary':w.get('question') or '확보한 API 자료는 유지했습니다. 필요한 자료의 구할 곳과 파일 형식을 확인한 뒤 계산을 이어가 주세요.',
                'period':{'start':w['start'] or '', 'end':w['end'] or ''},'frequency':'','chart':[],'tables':[],'insights':[],'lineage':[],'warnings':[],
                'supplement':{'workspaceId':w['id'],'revision':w['revision'],
                    'calculationGuide':w.get('calculation_guide') or w.get('source_preflight',{}).get('calculationGuide',''),
                    'question':w.get('question'),
                    'comparison':{'periods':w['source_preflight']['sources'][0]['periods'],'series':[{'alias':a,'label':s['required_data'],'unit':w['files'].get(a,{}).get('unit','')} for a,s in w['input_slots'].items()],
                                  'configured':bool(w.get('comparison_settings')),'settings':w.get('comparison_settings')} if w.get('comparison_required') else None,
                    'acceptedFormats':FORMATS,'maxFileBytes':2_000_000,'maxRows':UPLOAD_MAX_POINTS,
                    'missing':[{**s,'guidance':input_guidance(s,w['query'])} for s in w['missing']],
                    'available':[{'required_data':v['source']['required_data'],'origin':'kosis_api','tableId':v['source']['table_id'],'observations':len(v['response']['rows'])} for v in w['prefetched'].values()],
                    'uploaded':[{'alias':alias,'filename':f['filename'],'sha256':f['sha256'],'origin':'user_file'} for alias,f in w['files'].items()]}}

    def template(self,owner,identifier,alias,format='csv'):
        if format not in ('csv','json','tsv'):raise DerivedValidationError('양식은 CSV, JSON, TSV 중 선택해 주세요.')
        w=self.load(owner,identifier);slot=next((x for x in w['missing'] if x['alias']==alias),None)
        if not slot:raise KeyError('필요한 자료 항목을 찾을 수 없습니다.')
        if w.get('comparison_required'):
            rows=[]
            # Until the user chooses dates or monthly averages, a date-filled
            # template would silently choose their calculation basis.
            if not w.get('comparison_settings'):
                if format=='json':return json.dumps([dict(zip(FIELDS,['',None,'','D','']))],ensure_ascii=False,indent=2)
                stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,delimiter='\t' if format=='tsv' else ',');writer.writeheader();return stream.getvalue()
            for p in slot['periods']:
                for key in ('start','end'):
                    date=p[key]+('01' if len(p[key])==6 else '')
                    rows.append(dict(zip(FIELDS,[date,None,'',slot['frequency'],''])))
            if format=='json':return json.dumps(rows,ensure_ascii=False,indent=2)
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,delimiter='\t' if format=='tsv' else ',');writer.writeheader();writer.writerows(rows)
            return stream.getvalue()
        first=shift_period(canonical_bound(w['start'],slot['frequency']),slot['frequency'],-slot['lookback']);last=canonical_bound(w['end'],slot['frequency'])
        count=period_index(last,slot['frequency'])-period_index(first,slot['frequency'])+1
        if not 1<=count<=UPLOAD_MAX_POINTS:raise DerivedValidationError('파일 양식의 기간 한도를 초과했습니다.')
        rows=[]
        unit=slot['unit']
        if unit.startswith(('=','+','-','@')):unit="'"+unit
        for i in range(count):rows.append(dict(zip(FIELDS,[shift_period(first,slot['frequency'],i),None,unit,slot['frequency'],''])))
        if format=='json':return json.dumps(rows,ensure_ascii=False,indent=2)
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,delimiter='\t' if format=='tsv' else ',');writer.writeheader();writer.writerows(rows)
        return stream.getvalue()

    @observed('file.upload', 'span')
    def upload(self,owner,identifier,alias,filename,content,encoding='utf8'):
        with self.lock:
            w=self.load(owner,identifier);slot=next((x for x in w['missing'] if x['alias']==alias),None)
            if not slot and alias in w['files']:
                old=w['files'][alias];slot=w.get('input_slots',{}).get(alias) or {k:old[k] for k in ('required_data','frequency','unit')}
            if not slot:raise DerivedValidationError('현재 필요한 자료 항목이 아닙니다.')
            raw_bytes,reader,file_format=decode_file(filename,content,encoding)
            values={};units=set();provenance=set()
            for number,row in enumerate(reader,2):
                if len(values)>=UPLOAD_MAX_POINTS or None in row:raise DerivedValidationError('관측 수 또는 파일 열 한도를 초과했습니다.')
                p=(row['period'] or '').strip();period_index(p,slot['frequency'])
                if p in values:raise DerivedValidationError(f'{number}행: 중복 시점 {p}')
                if row['frequency']!=slot['frequency']:raise DerivedValidationError('필요한 자료와 파일의 주기가 다릅니다.')
                value=finite(row['value'])
                if value is None:raise DerivedValidationError(f'{number}행: 실제 관측값을 입력해 주세요. 빈 양식은 계산에 사용할 수 없습니다.')
                unit=(row['unit'] or '').strip();source=(row['source'] or '').strip()
                if not unit or len(unit)>100 or not source or len(source)>500 or any(t.startswith(('=','+','-','@')) or '\ufffd' in t for t in [unit,source]):raise DerivedValidationError('단위와 자료 출처를 명시하고 실행 수식을 입력하지 마세요.')
                units.add(unit);provenance.add(source);values[p]=value
            if not values or len(units)!=1:raise DerivedValidationError('빈 파일 또는 단위가 혼합된 파일은 사용할 수 없습니다.')
            unit=next(iter(units))
            if slot['unit'] and unit!=slot['unit']:raise DerivedValidationError(f"필요한 단위({slot['unit']})와 업로드 단위가 다릅니다.")
            digest=hashlib.sha256(raw_bytes).hexdigest()
            w['files'][alias]={'status':'success','source':'user_file','table_id':FILE_PREFIX+alias,'file_id':uuid4().hex,'filename':filename,'sha256':digest,
                'required_data':slot['required_data'],'frequency':slot['frequency'],'unit':unit,'provenance':sorted(provenance),
                'rows':[{'PRD_DE':p,'DT':v,'UNIT_NM':unit} for p,v in sorted(values.items())]}
            w['files'][alias]['file_format']=file_format
            w.pop('comparison_settings',None)
            raw_path=self.path(owner,identifier).parent/'files'/(w['files'][alias]['file_id']+'.'+file_format)
            self.raw_uploads.setdefault((owner,identifier),{})[raw_path.name]=raw_bytes
            w['missing']=[s for s in w['missing'] if s['alias']!=alias];w.pop('question',None);self.save(w)
            return self.public(w)

    @observed('file.resume', 'span')
    def resume(self,owner,identifier):
        with self.lock:
            w=self.load(owner,identifier)
            if w['missing']:raise DerivedValidationError('필요한 파일을 모두 올린 후 계산을 이어가 주세요.')
            return self.advance(w)
