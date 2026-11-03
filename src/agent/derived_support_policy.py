"""A derived request must actually use at least one approved API input."""
from expression_tree_validator import DerivedValidationError


def approved_dependencies(plan, catalog):
    nodes = {n.id:n for n in plan.nodes}
    used = set()
    def visit(alias):
        if alias in used:return
        used.add(alias)
        if alias in nodes:
            for child in nodes[alias].inputs:visit(child)
    for output in plan.outputs:visit(output.node)
    sources = [s for s in plan.sources if s.alias in used and not s.table_id.startswith('USER_FILE:')]
    for source in sources:catalog.validate_source_selection(source)
    return sources


def require_api_dependency(plan, catalog):
    if not approved_dependencies(plan, catalog):
        raise DerivedValidationError('최종 계산식에 필요한 승인 한국은행 KOSIS API 원자료가 하나도 없습니다. 파일만 사용하는 계산은 지원하지 않습니다. KOSIS 원자료를 실제로 사용하는 질문으로 다시 작성해 주세요.', failure_kind='unsupported_request')


def assessment_scope(assessment):
    sources=[s for s in assessment.get('sources') or [] if s.get('role') != 'definition_only']
    if assessment.get('status')!='checked' or not sources:return 'verification_pending'
    if any(s.get('status')=='available' for s in sources):
        return 'api_only' if all(s.get('status')=='available' for s in sources) else 'mixed'
    if all(s.get('status')=='not_found' for s in sources):return 'unsupported'
    return 'verification_pending'


def unsupported(query, reason=None):
    message=reason or '이 요청에 필요한 원자료 중 승인 한국은행 KOSIS API에서 사용할 수 있는 자료가 하나도 확인되지 않아 지원하지 않는 범위입니다. 파일만 업로드하는 계산은 지원하지 않습니다. KOSIS 제공 원자료를 실제로 사용하는 질문으로 다시 작성해 주세요.'
    return {'status':'need_details','unsupported':True,'analysisKind':'derived','query':query,'interpretedQuery':query,
        'question':message,'summary':message,'period':{'start':'','end':''},'frequency':'',
        'chart':[],'tables':[],'insights':[],'lineage':[],'warnings':[], 'state':{},
        'recovery':{'issue':'unsupported','question':message,'choices':[{'id':'revise','label':'질문 다시 작성'}],'preserveState':True},'usage':[],'searches':[]}


def verification_pending(query):
    result=unsupported(query,'한국은행 KOSIS API 입력의 확보 여부를 아직 확인하지 못했습니다. API 장애·빈 응답·선택 미확정을 자료 부재나 지원 범위 밖으로 판정하지 않습니다. 자료 확인을 다시 시도해 주세요. 확인 전에는 파일 업로드를 열지 않습니다.')
    result.pop('unsupported')
    result['recovery'].update(issue='source_verification',choices=[{'id':'retry','label':'자료 확인 다시 시도'},{'id':'revise','label':'질문 수정'}])
    return result
