"""Coordinates planning, the existing KOSIS service, calculation and lineage."""
from statbridge_mcp.observability import observed
import copy
from expression_tree_schema import ExpressionPlan
from expression_tree_validator import DerivedValidationError, validate_plan, canonical_bound, shift_period
from derived_catalog_tools import DerivedCatalogTools
from derived_indicator_planner import DerivedIndicatorPlanner
from derived_indicator_evidence import DerivedIndicatorEvidence
from dynamic_expression_executor import DynamicExpressionExecutor, source_series, Series


def validate_derived_output_row(result, row, planned_ids):
    internal = result.get('_derived') or {}
    plan = (internal.get('record') or {}).get('plan') or {}
    output_nodes = {out['node'] for out in plan.get('outputs', [])}
    source = row.get('_SOURCE_SERIES_ID')
    node = internal.get('series', {}).get(source)
    labels = {out['label'] for out in plan.get('outputs', []) if out['node'] == source}
    if ({s['table_id'] for s in plan.get('sources', [])} != planned_ids or source not in output_nodes or not node
            or node['unit'] != row.get('UNIT_NM') or node['frequency'] != row.get('_FREQUENCY')
            or row.get('_SERIES_LABEL') not in labels or row.get('ITM_NM') != row.get('_SERIES_LABEL')
            or node['values'].get(row.get('PRD_DE')) != row.get('DT')):
        raise DerivedValidationError('파생지표 출력값이 검증된 계산 결과 또는 원자료 출처와 다릅니다.')


class DerivedIndicatorRuntime:
    def __init__(self, gateway, client, evidence_directory):
        self.gateway = gateway
        self.catalog = DerivedCatalogTools(gateway.service.store)
        self.planner = DerivedIndicatorPlanner(client, self.catalog)
        self.evidence = DerivedIndicatorEvidence(evidence_directory)
        self.engine = DynamicExpressionExecutor()
        from derived_plan_cache import VerifiedPlanCache, fingerprint
        from pathlib import Path
        # Any deployed planner, schema or validation change invalidates reuse.
        files = ('derived_indicator_planner.py', 'expression_tree_schema.py',
                 'expression_tree_validator.py', 'derived_support_policy.py',
                 'derived_indicator_runtime.py', 'derived_plan_cache.py', 'verified_source_units.py', 'derived_provider_scope.py')
        self.plan_policy_hash = fingerprint([Path(__file__).with_name(f).read_text(encoding='utf-8') for f in files])
        self.plan_cache = VerifiedPlanCache()

    def plan(self, query, **kwargs):
        from derived_plan_cache import fingerprint
        from statbridge_mcp.observability import annotate
        import sys
        # The authenticated request context supplies ownership, never user input.
        auth = sys.modules.get('account_auth')
        user = auth.USER.get() if auth else None
        eligible = bool(user and not kwargs.get('previous') and not getattr(self.catalog, 'allow_file_inputs', False))
        if not eligible:
            return self._plan_uncached(query, **kwargs)
        key = fingerprint({'owner': user['id'], 'query': query, 'arguments': kwargs,
                           'catalog': self.catalog.version, 'policy': self.plan_policy_hash,
                           'model': getattr(getattr(self.planner.client, 'settings', None), 'main_model', None)})
        entry = self.plan_cache.get(key)
        if entry:
            result = entry['result']
            try:
                bounds = self.validate(result['plan'])
                current = self._plan_metadata_hash(result['plan'])
                valid = current == entry['metadata_hash'] and result['semantic_review'].get('approved') is True
            except (ValueError, TypeError, KeyError):
                valid = False
            if valid:
                annotate(plan_cache_hit=True, semantic_review_reused=True)
                return {**result, 'bounds': bounds, 'usage': [], 'seconds': 0,
                        'plan_cache_hit': True,
                        'plan_reuse': {'metadata_sha256': current, 'policy_sha256': self.plan_policy_hash,
                                       'reviewed_plan_sha256': fingerprint(result['plan'].model_dump()),
                                       'semantic_review_sha256': fingerprint(result['semantic_review']),
                                       'semantic_review_reused': True}}
            self.plan_cache.discard(key)
        result = self._plan_uncached(query, **kwargs)
        if (result.get('status') == 'planned' and result.get('semantic_review', {}).get('approved') is True
                and all(s.table_id in self.catalog.approved for s in result['plan'].sources)):
            self.validate(result['plan'])
            self.plan_cache.put(key, result, self._plan_metadata_hash(result['plan']))
        annotate(plan_cache_hit=False)
        return result

    def _plan_metadata_hash(self, plan):
        from derived_plan_cache import fingerprint
        return fingerprint([self.catalog.get_table_metadata(t) for t in sorted({s.table_id for s in plan.sources})])

    def _plan_uncached(self, query, **kwargs):
        from derived_private_inputs import private_metric
        if private_metric(query):
            return self.planner.plan(query, **kwargs)
        from derived_indicator_planner import is_availability_check, needs_source_preflight
        if not kwargs.get('previous') and not getattr(self.catalog,'allow_file_inputs',False):
            from derived_provider_scope import explicit_provider_check
            explicit=explicit_provider_check(self,query)
            if explicit is not None:return explicit
        if not kwargs.get('previous') and needs_source_preflight(query) and getattr(self, 'availability_unconfigured', False):
            return {'status':'need_details','question':'KOSIS API 연결 설정이 없어 실제 자료 확보 여부를 확인하지 못했습니다. 서버의 KOSIS 키 설정을 확인해 주세요. 자료 부재 판정이 아니며 계산은 실행하지 않았습니다.',
                'availability':{'status':'verification_failed','checkedTables':0,'requiredTables':349,'sources':[],'questions':[],'error':'KOSIS_API_KEY_missing'},'usage':[],'searches':[]}
        if not kwargs.get('previous') and needs_source_preflight(query) and hasattr(self, 'api_inventory'):
            from derived_availability import check_availability
            checked=check_availability(self, query)
            assessment=checked['availability']
            from derived_support_policy import assessment_scope, unsupported
            if assessment_scope(assessment)=='unsupported':
                return {**checked,**unsupported(query),'availability':assessment,'usage':checked.get('usage',[])}
            if (is_availability_check(query) or assessment['status']!='checked' or assessment.get('questions')
                    or not assessment.get('sources') or any(s['status']!='available' for s in assessment['sources'])):
                return checked
            verified=[{**s['selection'],'alias':f'verified_{i+1}'} for i,s in enumerate(assessment['sources']) if s['status']=='available' and s.get('selection')]
            units=[]
            for source in assessment['sources']:
                checks=source.get('observationChecks') or []
                observed={c['unit'] for c in checks if c.get('status')=='available' and c.get('unit')}
                if source.get('selection') and len(observed)==1 and len(checks)==sum(c.get('status')=='available' and bool(c.get('unit')) for c in checks):
                    units.append({**{k:source['selection'][k] for k in ('table_id','item_id','classifications','frequency')},
                                  'unit':next(iter(observed)), 'origin':'validated_KOSIS_observation_response'})
            planned=self.planner.plan(query,verified_sources=verified,verified_source_units=units,**kwargs)
            planned['usage']=checked.get('usage',[])+planned.get('usage',[])
            planned['source_preflight']=assessment
            return planned
        planned=self.planner.plan(query, **kwargs)
        if not kwargs.get('previous') and hasattr(self,'api_inventory') and (planned.get('unsupported') or planned.get('status')=='unavailable'):
            from derived_availability import check_availability
            from derived_support_policy import assessment_scope, unsupported
            checked=check_availability(self,query)
            scope=assessment_scope(checked['availability'])
            all_usage=planned.get('usage',[])+checked.get('usage',[])
            if scope=='unsupported':
                checked.update(unsupported(query),availability=checked['availability'])
            elif scope=='api_only':
                checked['question']='요청한 원자료는 실제 API에서 확인했습니다. 계산식 또는 계산 조건을 확인해 주세요. '+(planned.get('question') or '')
            checked['usage']=all_usage
            return checked
        return planned

    def input_series(self, source, response):
        return source_series(source, response)

    def source_origin(self, source):
        return 'kosis_api'

    @observed('plan.validate', 'span')
    def validate(self, plan):
        bounds = validate_plan(plan, self.catalog)
        from derived_support_policy import require_api_dependency
        require_api_dependency(plan,self.catalog)
        selected = [self.catalog.selection_text(s) for s in plan.sources]
        # Do not let an unknown metadata property be asserted by the model.
        seasonal = ['계절조정' in text for text in selected]
        raw = ['원계열' in text for text in selected]
        if any(seasonal) and any(raw):
            raise DerivedValidationError('원계열과 계절조정 계열을 혼합한 계산은 지원하지 않습니다.')
        for source in plan.sources:
            text = self.catalog.selection_text(source)
            if '소비자' in source.required_data and '물가' in source.required_data and '소비자물가' not in text.replace(' ', ''):
                raise DerivedValidationError('소비자물가 원자료를 다른 지표로 대체할 수 없습니다.')
        return bounds

    @observed('sources.fetch', 'span')
    def fetch(self, plan, bounds):
        # Validate before crossing the IO boundary, including direct callers.
        bounds = self.validate(plan)
        responses = {}
        batch = getattr(self.gateway, 'get_statistics_batch', None)
        if len(plan.sources) > 1 and callable(batch):
            requests = [dict(request_id=s.alias, table_id=s.table_id, item_id=s.item_id,
                classifications=s.classifications, frequency=s.frequency, start_period=bounds['fetch_start'],
                end_period=bounds['fetch_end'], prefer_local=False, allow_fallback=False) for s in plan.sources]
            outcomes = batch(requests, timeout_seconds=20).get('results') or []
            by_alias = {row.get('request_id'): row for row in outcomes}
            if len(by_alias) != len(plan.sources) or len(outcomes) != len(plan.sources):
                raise DerivedValidationError('필수 원자료의 배치 조회 결과가 누락되거나 중복되었습니다.')
            for source in plan.sources:
                outcome = by_alias.get(source.alias) or {}
                if outcome.get('status') != 'success' or outcome.get('table_id') != source.table_id:
                    raise DerivedValidationError(f'{source.required_data}: 필수 원자료 배치 조회에 실패했습니다. 성공한 일부 자료만으로 계산하지 않습니다.')
                response = outcome.get('data') or {}
                source_series(source, response)
                responses[source.alias] = response
            return responses
        for source in plan.sources:
            response = self.gateway.get_statistics(table_id=source.table_id, item_id=source.item_id,
                classifications=source.classifications, frequency=source.frequency,
                start_period=bounds['fetch_start'], end_period=bounds['fetch_end'],
                prefer_local=False, allow_fallback=False)
            source_series(source, response)  # Validate before any calculation.
            responses[source.alias] = response
        return responses

    @observed('dag.calculate', 'span')
    def calculate(self, query, planning, responses, *, previous=None, cached=None, changed=None):
        planning = dict(planning)
        plan = planning['plan']
        from derived_private_inputs import require_private_input
        require_private_input(query, plan, self.catalog)
        planning['bounds'] = self.validate(plan)
        inputs = {s.alias: self.input_series(s, responses[s.alias]) for s in plan.sources}
        from verified_source_units import unit_resolution
        unit_resolutions = {s.alias: resolved for s in plan.sources
                            if (resolved := unit_resolution(s, responses[s.alias]))}
        computed = self.engine.execute(plan, inputs, cached=cached, changed=changed)
        if cached:
            # Retain missing-value evidence for untouched cached subtrees.
            recomputed = set(changed or [])
            for node in plan.nodes:
                if any(ref in recomputed for ref in node.inputs): recomputed.add(node.id)
            previous_issues = planning.get('previous_issues', [])
            computed['issues'] += [i for i in previous_issues if i['node'] not in recomputed]
        serialized = {key: {'values': value.values, 'unit': value.unit, 'frequency': value.frequency,
                            'source_ids': value.source_ids} for key, value in computed['series'].items()}
        safe_responses = {}
        allowed = {'ORG_ID', 'TBL_ID', 'ITM_ID', 'ITM_NM', 'PRD_DE', 'PRD_SE', 'DT', 'UNIT_NM',
                   *{f'C{i}' for i in range(1, 9)}, *{f'C{i}_NM' for i in range(1, 9)}}
        for alias, response in responses.items():
            safe_responses[alias] = {k: response.get(k) for k in ('status', 'source', 'table_id')}
            if response.get('source')=='user_file':
                safe_responses[alias].update({k:response.get(k) for k in ('sha256','filename','provenance','frequency','unit','file_id')})
            safe_responses[alias]['rows'] = [{k: v for k, v in row.items() if k in allowed} for row in response['rows']]
        coverage = planning.get('coverage') or {s.alias: {'start': planning['bounds']['fetch_start'], 'end': planning['bounds']['fetch_end']} for s in plan.sources}
        record = {'query': query, 'catalog_version': self.catalog.version, 'plan': plan.model_dump(),
                  'source_metadata': [{'table_id':s.table_id, **{k:v for k,v in self.catalog.get_table_metadata(s.table_id).items() if k in ('metadata_origin','metadata_sha256','sha256')}} for s in plan.sources],
                  'bounds': planning['bounds'], 'source_data': safe_responses, 'node_results': serialized,
                  'source_coverage': coverage,
                  'issues': computed['issues'], 'calculations': computed['calculations'],
                  'searches': planning.get('searches', []), 'semantic_review': planning.get('semantic_review'),
                  'usage': planning.get('usage', []), 'seconds': planning.get('seconds'), 'previous': previous,
                  'source_preflight':planning.get('source_preflight'),
                  'comparison_settings':planning.get('comparison_settings'),
                  'plan_cache_hit':planning.get('plan_cache_hit',False)}
        record['plan_reuse'] = planning.get('plan_reuse')
        record['unit_resolutions'] = unit_resolutions
        from derived_period_comparison import average_receipt
        record['category_averages']=average_receipt(plan,computed['series'])
        receipt = self.evidence.save(record)
        public = {**receipt, 'plan': plan.model_dump(), 'bounds': planning['bounds'],
                  'issues': computed['issues'], 'catalogVersion': self.catalog.version,
                  'categoryAverages':record['category_averages'],
                  'usage': planning.get('usage', []), 'seconds': planning.get('seconds'),
                  'sourceSelections': [{'alias': s.alias, 'selection': self.catalog.selection_text(s),
                                        'tableId': s.table_id, 'itemId': s.item_id, 'frequency': s.frequency,
                                        'classifications': s.classifications, 'origin':self.source_origin(s),
                                        **({'unitResolution':unit_resolutions[s.alias]} if s.alias in unit_resolutions else {}),
                                        **({k:responses[s.alias].get(k) for k in ('sha256','filename','provenance')} if self.source_origin(s)=='user_file' else {})} for s in plan.sources],
                  'limitations': ['경제학적 의미는 모델 검토와 서버 규칙으로 확인하며 일반적인 수학적 증명은 아닙니다.',
                                  *dict.fromkeys(r['limitation'] for r in unit_resolutions.values())],
                  'missingPolicy': '결측 및 계산 불가능한 값은 0으로 채우지 않고 제외하며 해당 시점을 기록합니다.'}
        plans = []
        for source in plan.sources:
            meta = self.catalog.get_table_metadata(source.table_id)
            plans.append({'table_id': source.table_id, 'table_name': meta['table_name'], 'org_id': meta.get('organization_id','301'),
                          'item_id': source.item_id, 'classifications': source.classifications, 'frequency': source.frequency,
                          'start_period': plan.start_period, 'end_period': plan.end_period,
                          'exact_params': {}, 'series_label': source.required_data})
        return {'status': 'resolved', 'query': query, 'api_plan': plans[0], 'api_plans': plans,
                'selected_table': {'table_id': plans[0]['table_id'], 'table_name': plans[0]['table_name']},
                'execution': {'status': 'success', 'rows': computed['rows'], 'row_count': len(computed['rows']),
                              'sources': [{'table_id': s.table_id, 'source': self.source_origin(s)} for s in plan.sources],
                              'calculations': list(computed['calculations'].values())},
                'derivedEvidence': public,
                '_derived': {'record': record, 'responses': safe_responses, 'series': serialized, 'coverage': coverage}}

    @observed('dag.revise', 'span')
    def revise(self, result, instruction=None, *, node_patch=None, start=None, end=None):
        old = result['_derived']
        prior = old['record']
        if instruction:
            planning = self.plan(instruction, previous=prior['plan'], start_period=start, end_period=end)
            if planning['status'] != 'planned':
                raise DerivedValidationError(planning.get('question') or '계산 조건을 확정하지 못했습니다.')
        else:
            plan_data = copy.deepcopy(prior['plan'])
            frequency = plan_data['sources'][0]['frequency']
            if start: plan_data['start_period'] = canonical_bound(start, frequency)
            if end: plan_data['end_period'] = canonical_bound(end, frequency)
            if (start or end) and not node_patch:
                # The previous model explanation can contain its old date range.
                # Describe the unchanged calculation from the validated DAG; the
                # original economic explanation remains in the immutable receipt.
                selections = '; '.join(self.catalog.selection_text(s) for s in
                                       ExpressionPlan.model_validate(plan_data).sources)
                operations = '; '.join(f"{n['id']} = {n['operator']}({', '.join(n['inputs'])})"
                                       for n in plan_data['nodes'])
                plan_data['economic_definition'] = (
                    f"표시 기간: {plan_data['start_period']} ~ {plan_data['end_period']}. "
                    f"기존에 검증한 원자료와 계산식을 유지한 기간 변경입니다. 원자료: {selections}. "
                    f"계산 단계: {operations}. 이전 설명은 직전 계산 근거에 보존되어 있습니다.")
            if node_patch:
                if set(node_patch) - {'id', 'operator', 'inputs', 'lag', 'window', 'base_period', 'exponent'}:
                    raise DerivedValidationError('허용되지 않은 계산 노드 수정입니다.')
                match = next((n for n in plan_data['nodes'] if n['id'] == node_patch.get('id')), None)
                if match is None:
                    raise DerivedValidationError('수정할 계산 노드가 없습니다.')
                if any(key in node_patch and node_patch[key] != match[key] for key in ('operator', 'inputs')):
                    raise DerivedValidationError('연산·입력 변경은 자연어로 요청해 경제학적 의미를 다시 검증해 주세요.')
                match.update(node_patch)
            plan = ExpressionPlan.model_validate(plan_data)
            planning = {'plan': plan, 'bounds': self.validate(plan), 'usage': [], 'searches': [],
                        'semantic_review': prior['semantic_review']}
            if node_patch:
                changed_parameters = {k: v for k, v in node_patch.items() if k != 'id'}
                plan.economic_definition += f' / 계산 조건 변경: {node_patch["id"]} {changed_parameters}'
        plan = planning['plan']
        bounds = self.validate(plan)
        planning['bounds'] = bounds
        old_sources = {s['alias']: s for s in prior['plan']['sources']}
        responses, dirty, coverage_map = {}, set(), {}
        for source in plan.sources:
            prior_source = old_sources.get(source.alias) or {}
            same = all(prior_source.get(field) == getattr(source, field)
                       for field in ('table_id', 'item_id', 'classifications', 'frequency'))
            old_range = old.get('coverage', {}).get(source.alias) or {'start': prior['bounds']['fetch_start'], 'end': prior['bounds']['fetch_end']}
            coverage = old_range['start'] <= bounds['fetch_start'] and bounds['fetch_end'] <= old_range['end']
            coverage_map[source.alias] = (dict(old_range) if same else {'start': bounds['fetch_start'], 'end': bounds['fetch_end']})
            if same and coverage:
                responses[source.alias] = old['responses'][source.alias]
            else:
                dirty.add(source.alias)
                ranges = [(bounds['fetch_start'], bounds['fetch_end'])]
                retained = []
                if same:
                    ranges = []
                    retained = list(old['responses'][source.alias]['rows'])
                    if bounds['fetch_start'] < old_range['start']:
                        ranges.append((bounds['fetch_start'], shift_period(old_range['start'], source.frequency, -1)))
                    if bounds['fetch_end'] > old_range['end']:
                        ranges.append((shift_period(old_range['end'], source.frequency, 1), bounds['fetch_end']))
                    coverage_map[source.alias] = {'start': min(old_range['start'], bounds['fetch_start']), 'end': max(old_range['end'], bounds['fetch_end'])}
                response = None
                for first, last in ranges:
                    fetched = self.gateway.get_statistics(table_id=source.table_id, item_id=source.item_id,
                        classifications=source.classifications, frequency=source.frequency,
                        start_period=first, end_period=last, prefer_local=False, allow_fallback=False)
                    source_series(source, fetched)
                    retained.extend(fetched['rows'])
                    response = fetched
                response = {**(response or old['responses'][source.alias]), 'rows': retained}
                source_series(source, response)
                responses[source.alias] = response
        old_nodes = {n['id']: n for n in prior['plan']['nodes']}
        for node in plan.nodes:
            if old_nodes.get(node.id) != node.model_dump() or plan.start_period != prior['plan']['start_period'] or plan.end_period != prior['plan']['end_period']:
                dirty.add(node.id)
        cached = {key: Series(**value) for key, value in old['series'].items()}
        planning['previous_issues'] = prior['issues']
        planning['coverage'] = coverage_map
        revised = self.calculate(result['query'], planning, responses, previous=result['derivedEvidence']['calculationId'], cached=cached, changed=dirty)
        return revised
