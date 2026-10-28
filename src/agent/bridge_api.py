from __future__ import annotations
from statbridge_mcp.observability import observed
from execution_trace import initial_traces, edit_trace

import os
import copy
import json
import re
import sys
import time
import calendar
from uuid import uuid4
from itertools import product
from pathlib import Path
from typing import Any, Literal
from threading import RLock

from runtime_paths import layout
PATHS = layout()
ROOT = PATHS["root"]
MCP_ROOT = PATHS["backend"]
DATA_DIR = PATHS["data"]

os.environ.setdefault("STATBRIDGE_DATA_DIR", str(DATA_DIR))
os.environ.setdefault("STATBRIDGE_TABLES_DIR", str(PATHS["tables"]))
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from statbridge_mcp.statistics_service import StatisticsService
from agent_runtime import StatBridgeAgent
from mcp_gateway import McpToolGateway
from chart_edit_agent import ChartEditAgent, Hcx007EditModel
from output_schema import VisualEditContext, ChartSpec, ChartSticker, ChartEditCommand
from advanced_chart_spec import ReportOptions, TEMPLATES
from request_match_guard import structure_request, catalog_mentions, unsupported_quantity_terms
from request_period import explicit_period
from chart_edit_period import revise_period, period_redraw
from canonical_request_planner import instruction_text


class _NoKeyClient:
    """Keeps dictionary/MCP metadata planning usable until KOSIS_API_KEY is configured."""
    def get_prd_meta(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")

    def get_statistics(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("KOSIS_API_KEY가 설정되지 않았습니다.")

app = FastAPI(title="StatBridge Agent API", version="0.6.0")
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import http_exception_handler
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from recovery import recovery_question


@app.exception_handler(RequestValidationError)
async def readable_reference_validation(request: Request, exc: RequestValidationError):
    errors=exc.errors()
    if any('reference_files' in error.get('loc',()) for error in errors):
        # An invalid upload must not echo megabytes of base64 into the chat.
        details=[{key:value for key,value in error.items() if key in {'type','loc','msg'}} for error in errors]
        return JSONResponse(status_code=422,content={'detail':details})
    return await request_validation_exception_handler(request,exc)


@app.exception_handler(HTTPException)
async def actionable_error(request: Request, exc: HTTPException):
    if exc.status_code in (400, 409, 422, 429, 500, 502, 503, 504) and request.url.path.startswith('/api/'):
        stage = 'edit' if request.url.path.endswith('/edit') else 'output' if '/output' in request.url.path else 'query'
        return JSONResponse(status_code=exc.status_code, content={'detail':exc.detail,'recovery':recovery_question(exc.detail,stage,issue='connection' if exc.status_code in (429,500,502,503,504) else None)},headers=exc.headers)
    return await http_exception_handler(request,exc)
# The agent is the only UI-facing orchestration layer. It calls the same service exposed as MCP tools.
try:
    service = StatisticsService()
except RuntimeError as exc:
    if "KOSIS_API_KEY" not in str(exc):
        raise
    service = StatisticsService(client=_NoKeyClient())
mcp_gateway = McpToolGateway(service)
agent = StatBridgeAgent(service=mcp_gateway)
from openai_chart_client import OpenAIChartClient, OpenAIEditModel, text_model_client
from derived_indicator_runtime import DerivedIndicatorRuntime
from derived_indicator_planner import is_derived_request, is_availability_check
from derived_intent_router import is_calculation_edit
strong_client = OpenAIChartClient()
agent.workflow.derived_runtime = DerivedIndicatorRuntime(
    mcp_gateway, strong_client, ROOT / 'runtime_data' / 'derived_evidence')
if getattr(service.client,'api_key',None):
    from live_derived_catalog import LiveDerivedCatalog
    live_derived_catalog=LiveDerivedCatalog(service.store,service.client)
    agent.workflow.derived_runtime.catalog=live_derived_catalog
    agent.workflow.derived_runtime.planner.catalog=live_derived_catalog
    from derived_api_inventory import DerivedApiInventory
    agent.workflow.derived_runtime.api_inventory=DerivedApiInventory(live_derived_catalog,ROOT/'runtime_data/derived_api_inventory')
else:
    agent.workflow.derived_runtime.availability_unconfigured=True
from derived_supplement import DerivedSupplementService
derived_supplements=DerivedSupplementService(agent.workflow.derived_runtime,Path(os.getenv('STATBRIDGE_DERIVED_SUPPLEMENT_DIR',str(ROOT/'runtime_data'/'derived_supplements'))))
from chart_reference import ChartReference
report_client = agent.output_agent.ncp_client
if os.getenv('STATBRIDGE_OUTPUT_PROVIDER', 'openai') == 'ncp':
    edit_agent = ChartEditAgent(Hcx007EditModel(agent.ncp), agent.output_agent)
else:
    # Only fallback-backed fact selection and style proposals use the text tier.
    chart_client = strong_client
    text_client = text_model_client(strong_client)
    agent.output_agent.ncp_client = text_client
    edit_agent = ChartEditAgent(OpenAIEditModel(strong_client), agent.output_agent)
    report_client = strong_client


class ClarificationSelection(BaseModel):
    clarification_id: str
    value: str


class QueryRequest(BaseModel):
    query: str
    state: dict[str, Any] | None = None
    clarification: ClarificationSelection | None = None
    selections: list[dict[str, Any]] | None = None
    dimension_values: dict[str, str] | None = None
    execute: bool = True
    period_start: str | None = None
    period_end: str | None = None
    chart_mode: str | None = None
    chart_type: str | None = None
    chart_options: dict[str, Any] | None = None


class OutputRenderRequest(BaseModel):
    quick_output: bool = False
    session_ids: list[str] = Field(min_length=1)
    chart_type: str
    chart_mode: str = "combined"
    title: str | None = None
    show_legend: bool = True
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    natural_language: str | None = None


class OutputOptionsRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=50)


class EditConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class OutputEditRequest(BaseModel):
    edit_target: Literal["graph", "table"] = "graph"
    derived_node_patch: dict[str, Any] | None = None
    reference_files: list[ChartReference] = Field(default_factory=list, max_length=3)
    edit_session_id: str
    instruction: str = Field(default="", max_length=1000)
    visual: VisualEditContext | None = None
    conversation: list[EditConversationMessage] = Field(default_factory=list)
    action: Literal["edit", "undo", "redo"] = "edit"
    revision: int | None = Field(default=None, ge=0)
    stickers: list[ChartSticker] | None = Field(default=None, max_length=40)
    report: ReportOptions | None = None
    clear_report: bool = False
    commands: list[ChartEditCommand] = Field(default_factory=list, max_length=12)
    document: dict[str, Any] | None = None
    append_session_ids: list[str] = Field(default_factory=list,max_length=8)
    replace_session_ids: list[str] = Field(default_factory=list,max_length=8)
    restore_initial: bool = False
    undo_property: Literal['title','subtitle','legend_position','series_color'] | None = None
    property_label: str | None = Field(default=None,max_length=300)


class DerivedDataRequest(BaseModel):
    edit_session_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


@app.post('/api/output/derived-data')
def derived_data(payload: DerivedDataRequest):
    session = EDIT_SESSIONS.get(payload.edit_session_id)
    if not session or not session['result'].get('_derived'):
        raise HTTPException(status_code=404, detail='파생지표 계산 근거를 찾을 수 없습니다.')
    record = session['result']['_derived']['record']
    return {'sources': [{'alias': alias, 'total': len(source['rows']),
                         'rows': source['rows'][payload.offset:payload.offset + payload.limit]}
                        for alias, source in record['source_data'].items()],
            'plan': record['plan'], 'bounds': record['bounds']}


@app.get('/api/chart/designs')
def chart_designs():
    from chart_designs import DESIGNS
    return {'items':[{'id':key,**value} for key,value in DESIGNS.items()]}


def workspace_data(session, ids, replace=False):
    added=[]
    for identifier in ids:
        item=OUTPUT_SESSIONS.get(identifier)
        if not item:raise ValueError('데이터 조회 세션이 만료되었습니다. 다시 조회해 주세요.')
        plans=item['result'].get('api_plans') or [item['result'].get('api_plan')]
        confirmed={str(p['table_id']):(_api_period(item['period']['start'],str(p['frequency'])),_api_period(item['period']['end'],str(p['frequency']),end=True)) for p in plans if p}
        if agent.validate_resolution(item['query'],item['result'],confirmed_periods=confirmed).get('status')!='resolved':
            raise ValueError('추가할 데이터의 요청·조회 일치 검증에 실패했습니다.')
        added.append(item)
    sessions=added if replace else [*session['sessions'],*added]
    results=[s['result'] for s in sessions]
    merged=copy.deepcopy(results[0]);plans=[p for r in results for p in (r.get('api_plans') or [r.get('api_plan')]) if p]
    merged.update(api_plans=plans,request_validation={'valid':True,'table_ids':[p['table_id'] for p in plans]})
    rows=[row for r in results for row in r.get('execution',{}).get('rows',[])]
    merged['execution']={'status':'success','rows':rows,'row_count':len(rows),'sources':[s for r in results for s in r.get('execution',{}).get('sources',[])]}
    spec=ChartSpec.model_validate(session['output']['chartState'])
    old_series=agent.output_agent._series(session['result'].get('execution',{}).get('rows',[]))
    labels={s['label'] for s in agent.output_agent._series(rows)}
    if not replace:
        old_labels={s['label'] for s in agent.output_agent._series(session['result'].get('execution',{}).get('rows',[]))}
        if not (labels-old_labels):raise ValueError('같은 데이터 항목이 이미 포함되어 있습니다. 다른 통계나 분류를 선택해 주세요.')
        if spec.series_order:spec.series_order=[*spec.series_order,*[s['label'] for s in agent.output_agent._series(rows) if s['label'] not in spec.series_order]]
    if replace:
        for field in ['series_colors','series_styles','series_dashes','display_names','series_chart_types']:
            setattr(spec,field,{k:v for k,v in getattr(spec,field).items() if k in labels})
        spec.hidden_series=[s for s in spec.hidden_series if s in labels];spec.series_order=[];spec.secondary_axis_series=[]
        spec.range_styles=[];spec.guides=[];spec.notes=[n for n in spec.notes if not n.label];spec.annotations=[];spec.highlights=[]
        if spec.report:spec.report=spec.report.model_copy(update={'intervals':[],'events':[],'right_axis':[],'forecast_start':None,'kind':'forecast'})
    series=agent.output_agent._series(rows)
    if replace and {s['frequency'] for s in old_series}!={s['frequency'] for s in series}:spec.x_axis_label=''
    if replace or len({s['unit'] for s in series})>1:
        spec.y_axis_label=''
        spec.axes={name:axis.model_copy(update={'minimum':None,'maximum':None,'title':None,'tick_step':None}) if name in {'y','y2'} else axis for name,axis in spec.axes.items()}
    if len({s['unit'] for s in series})>1 or len({s['frequency'] for s in series})>1:
        spec.layout='grid'
        if spec.report:spec.report=spec.report.model_copy(update={'kind':'panels'})
    return merged,sessions,agent.output_agent._build(merged,spec,generate_explanation=False)


@app.get("/api/stickers")
def get_stickers() -> dict[str, Any]:
    from chart_stickers import sticker_catalog
    return {"items": sticker_catalog(), "maxItems": 40}


@app.get('/api/report/templates')
def report_templates():
    return {'schemaVersion':'statbridge.report/1','templates':[{'id':key,**value} for key,value in TEMPLATES.items()],
        'unsupported':['dotplot','yield_curve','network'],'confidencePolicy':'실제 경계·방법·출처를 제공해야 함'}


class ReportExportRequest(BaseModel):
    edit_session_id: str
    format: Literal['json','png','svg','pdf']
    revision: int | None = Field(default=None,ge=0)


class TableExportRequest(BaseModel):
    edit_session_id: str
    format: Literal['csv', 'xlsx', 'png', 'pdf']
    revision: int = Field(ge=0)



class StatisticalQuestionRequest(BaseModel):
    edit_session_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    question: str = Field(min_length=1, max_length=1000)
    conversational: bool = False
    display_mode: Literal['graph','table'] = 'graph'
    conversation_version: int | None = Field(default=None,ge=0,strict=True)


def _report_displayed_series(snapshot,display_mode='graph'):
    spec=ChartSpec.model_validate(snapshot['output']['chartState'])
    displayed=agent.output_agent._display_series(snapshot['result'],spec)
    if spec.report_table and display_mode=='table':
        labels=spec.report_table.labels
        displayed=[s for s in displayed if not labels or s['label'] in labels]
        if spec.report_table.recent_periods:
            displayed=[{**s,'points':s['points'][-spec.report_table.recent_periods:]} for s in displayed]
    if len(displayed)>40:raise HTTPException(422,'현재 자료 대화는 최대 40개 계열을 지원합니다.')
    return displayed


@app.get('/api/report/conversation/{edit_session_id}')
@observed('report.conversation.refresh','span')
def report_conversation_history(edit_session_id: str,display_mode: Literal['graph','table']='graph'):
    from report_conversation import refresh,public_state
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(edit_session_id)
        user=account_auth.USER.get() or {}
        if not session or session.get('account_id')!=user.get('id') or not user.get('id'):
            raise HTTPException(404,'분석 대화를 찾을 수 없습니다.')
        snapshot=copy.deepcopy(session);revision=snapshot['output'].get('editVersion',0)
        version=snapshot.get('report_conversation',{}).get('version',0)
    updated=refresh(snapshot.get('report_conversation'),_report_displayed_series(snapshot,display_mode),revision,display_mode)
    with EDIT_LOCK:
        if session['output'].get('editVersion',0)!=revision or session.get('report_conversation',{}).get('version',0)!=version:
            raise HTTPException(409,'분석이나 대화가 변경되었습니다. 다시 불러와 주세요.')
        updated['markdown_entries']=copy.deepcopy(session.get('report_conversation',{}).get('markdown_entries',[]))
        session['report_conversation']=updated
    return public_state(updated)


class ReportMarkdownRequest(BaseModel):
    edit_session_id: str = Field(min_length=1,max_length=128)
    revision: int = Field(ge=0,strict=True)
    turn_id: int | None = Field(default=None,ge=1,strict=True)


@app.post('/api/report/markdown/append')
@observed('report.markdown.append','span')
def append_report_markdown(payload: ReportMarkdownRequest):
    from report_markdown import append_answer
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(payload.edit_session_id)
        if not session:raise HTTPException(404,'분석 세션이 없습니다.')
        if payload.revision!=session['output'].get('editVersion',0):raise HTTPException(409,'최신 자료의 검산이 끝난 뒤 추가해 주세요.')
        if payload.turn_id is None:raise HTTPException(422,'추가할 답변을 선택해 주세요.')
        conversation=session.get('report_conversation',{})
        try:entries=append_answer(conversation,payload.turn_id,payload.revision)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        conversation['markdown_entries']=entries
        session['report_conversation']=conversation
    return {'entries':entries}


@app.post('/api/report/markdown/export')
@observed('report.markdown.export','span')
def export_report_markdown(payload: ReportMarkdownRequest):
    from report_markdown import markdown
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(payload.edit_session_id)
        if not session:raise HTTPException(404,'분석 세션이 없습니다.')
        entries=copy.deepcopy(session.get('report_conversation',{}).get('markdown_entries',[]))
    if not entries:raise HTTPException(422,'저장할 답변의 MD에 추가 버튼을 먼저 눌러 주세요.')
    return Response(markdown(entries).encode('utf-8'),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="statbridge-report.md"'})


@app.post('/api/report/question')
@observed('report.question', 'span')
def statistical_question(payload: StatisticalQuestionRequest):
    from statistical_question import answer_question
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail='분석할 질문을 입력해 주세요.')
    with EDIT_LOCK:
        session = EDIT_SESSIONS.get(payload.edit_session_id)
        if not session:
            raise HTTPException(status_code=404, detail='분석 편집 세션이 없습니다. 자료를 다시 조회해 주세요.')
        if payload.revision != session['output'].get('editVersion', 0):
            raise HTTPException(status_code=409, detail='그래프가 변경되었습니다. 최신 자료에서 다시 질문해 주세요.')
        snapshot = copy.deepcopy(session)
    if payload.conversational:
        from report_conversation import reply,public_state
        before_version=snapshot.get('report_conversation',{}).get('version',0)
        if payload.conversation_version is not None and payload.conversation_version!=before_version:
            raise HTTPException(409,'다른 질문이 먼저 처리됐어. 대화를 다시 불러온 뒤 이어서 질문해 줘.')
        try:
            answer,conversation=reply(question,_report_displayed_series(snapshot,payload.display_mode),snapshot.get('report_conversation'),payload.revision,report_client,payload.display_mode)
        except RuntimeError as exc:
            raise HTTPException(503,str(exc)) from exc
        with EDIT_LOCK:
            live=EDIT_SESSIONS.get(payload.edit_session_id)
            if not live or payload.revision!=live['output'].get('editVersion',0) or live.get('report_conversation',{}).get('version',0)!=before_version:
                raise HTTPException(409,'답변 중 분석이나 대화가 변경됐어. 최신 대화에서 다시 질문해 줘.')
            conversation['markdown_entries']=copy.deepcopy(live.get('report_conversation',{}).get('markdown_entries',[]))
            live['report_conversation']=conversation
        return {**answer,'conversation':public_state(conversation)}
    try:
        output = snapshot['output']
        spec = ChartSpec.model_validate(output['chartState'])
        # Display transformations and aliases must not alter statistical evidence.
        visible = {s['label'] for s in agent.output_agent._display_series(snapshot['result'], spec)}
        series = agent.output_agent._series(snapshot['result'].get('execution', {}).get('rows', []))
        plans = snapshot['result'].get('api_plans') or [snapshot['result'].get('api_plan')]
        for item in series:
            matching = [p for p in plans if p and str(p.get('table_id')) == item.get('sourceId')]
            if len(matching) == 1:
                item['tableName'] = matching[0].get('table_name', '')
            if not item.get('frequency') and len(matching) == 1:
                item['frequency'] = matching[0].get('frequency', '')
        raw_spec = spec.model_copy(update={'transform': 'raw', 'top_n': None})
        series = [s for s in agent.output_agent._apply_data_edits(series, raw_spec) if s['label'] in visible]
        if len(series) > 40:
            raise ValueError('too many series')
        from visible_chart_commentary import refers_to_visible_chart,describe_visible_chart
        if refers_to_visible_chart(question):
            displayed=agent.output_agent._display_series(snapshot['result'],spec)
            if re.search(r'표',question) and spec.report_table:
                selected=spec.report_table.labels
                displayed=[s for s in displayed if not selected or s['label'] in selected]
                if spec.report_table.recent_periods:
                    displayed=[{**s,'points':s['points'][-spec.report_table.recent_periods:]} for s in displayed]
            answer=describe_visible_chart(question,displayed)
        else:
            answer = answer_question(question, series, report_client)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail='현재 조회 자료와 비교 조건을 확인해 주세요.') from exc
    with EDIT_LOCK:
        live = EDIT_SESSIONS.get(payload.edit_session_id)
        if not live or payload.revision != live['output'].get('editVersion', 0):
            raise HTTPException(status_code=409, detail='답변 작성 중 그래프가 변경되었습니다. 최신 자료에서 다시 질문해 주세요.')
    from focused_question_answer import focused_answer
    if answer.get('method')!='displayed_data_server_calculated':answer=focused_answer(answer)
    return {**answer, 'revision': payload.revision, 'sources': copy.deepcopy(output.get('evidence', []))}


@app.post('/api/table/export')
@observed('export.table', 'span')
def export_table(payload: TableExportRequest):
    from fastapi.responses import Response
    from report_table import build_table, render_table_file
    with EDIT_LOCK:
        session = EDIT_SESSIONS.get(payload.edit_session_id)
        if not session: raise HTTPException(status_code=404, detail='분석 편집 세션이 없습니다.')
        if payload.revision != session['output'].get('editVersion', 0):
            raise HTTPException(status_code=409, detail='최신 분석에서 표를 다시 출력해 주세요.')
        result = copy.deepcopy(session['result'])
        state = copy.deepcopy(session['output']['chartState'])
        displayed_document = copy.deepcopy(session['output'].get('tableDocument'))
    try:
        spec = ChartSpec.model_validate(state)
        series = agent.output_agent._display_series(result, spec)
        doc = displayed_document or build_table(series, spec.report_table, title=spec.title, sources=agent.output_agent._evidence(result), display_names=spec.display_names)
        content = render_table_file(doc, payload.format)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    zipped = payload.format == 'png' and content[:2] == b'PK'
    extension = 'zip' if zipped else payload.format
    mime = {'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'png': 'image/png', 'pdf': 'application/pdf', 'zip': 'application/zip'}[extension]
    return Response(content, media_type=mime, headers={'Content-Disposition': f'attachment; filename="statbridge-table.{extension}"', 'X-Chart-Version': str(payload.revision)})


@app.post('/api/report/recommend')
def recommend_current_report(payload:ReportExportRequest):
    from advanced_chart_spec import recommend_report
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(payload.edit_session_id)
        if not session:raise HTTPException(status_code=404,detail='그래프 편집 세션이 없습니다.')
        if payload.revision is not None and payload.revision!=session['output'].get('editVersion',0):
            raise HTTPException(status_code=409,detail='최신 그래프에서 추천을 요청해 주세요.')
        series=copy.deepcopy(session['output']['visualization']['series'])
        result=copy.deepcopy(session['result'])
    plans=result.get('api_plans') or ([result['api_plan']] if result.get('api_plan') else [])
    return recommend_report(series,plans)


@app.post('/api/report/export')
@observed('export.report', 'span')
def export_report(payload: ReportExportRequest):
    from fastapi.responses import Response
    from report_export import document,render_file
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(payload.edit_session_id)
        if session is None:raise HTTPException(status_code=404,detail='그래프 편집 세션이 없습니다.')
        version=session['output'].get('editVersion',0)
        if payload.revision is not None and payload.revision!=version:
            raise HTTPException(status_code=409,detail='최신 그래프 버전으로 다시 출력해 주세요.')
        result=copy.deepcopy(session['result'])
        state=copy.deepcopy(session['output']['chartState'])
    try:
        spec=ChartSpec.model_validate(state)
        if payload.format=='json':
            content=json.dumps(document(result,spec),ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')
            if len(content)>8_000_000:raise ValueError('복원 가능한 JSON 크기 제한 8MB를 초과했습니다. 표시 기간·계열 수를 줄여 주세요.')
        else:
            series=agent.output_agent._display_series(result,spec)
            content=render_file(series,spec,result,payload.format)
    except (ValueError,TypeError) as error:
        raise HTTPException(status_code=422,detail=str(error)) from error
    mime={'json':'application/json','png':'image/png','svg':'image/svg+xml','pdf':'application/pdf'}[payload.format]
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="statbridge-report.{payload.format}"','X-Chart-Version':str(version)})


OUTPUT_SESSIONS: dict[str, dict[str, Any]] = {}
EDIT_SESSIONS: dict[str, dict[str, Any]] = {}
MAX_EDIT_SESSIONS = 100
import account_auth
from observability_middleware import ObservabilityMiddleware, get_trace as get_observation_trace, install_shutdown
from statbridge_mcp.observability import shutdown_exporter
app.add_middleware(ObservabilityMiddleware,owner=lambda:account_auth.USER.get(),sessions=EDIT_SESSIONS,session_lock=lambda:EDIT_LOCK)
install_shutdown(app,shutdown_exporter)
account_auth.install(app,ROOT,OUTPUT_SESSIONS,EDIT_SESSIONS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(account_auth.ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-StatBridge-Trace-Id"],
)


EDIT_LOCK = RLock()


def _table_card(table_id: str) -> dict[str, str]:
    if table_id.startswith('USER_FILE:'):
        return {'tableId':table_id,'name':'사용자 업로드 · '+table_id.split(':',1)[1],'source':'사용자 제공 자료 (KOSIS 자료 아님)','item':'파일 관측값','unit':'계산 근거 참조'}
    try:
        meta = service.get_table_metadata(table_id, live_period_fallback=False)
        items = meta.get("items") or []
        item = str(items[0].get("item_name") or "통계표 후보") if items else "통계표 후보"
        units = (agent.tables_by_id.get(table_id) or {}).get("units") or []
        return {
            "tableId": table_id,
            "name": str(meta.get("table_name") or table_id),
            "source": "한국은행 · KOSIS",
            "item": item,
            "unit": " / ".join(str(unit) for unit in units) or "-",
        }
    except Exception:
        return {"tableId": table_id, "name": table_id, "source": "한국은행 · KOSIS", "item": "통계표", "unit": "-"}


DOMAIN_LABELS = {
    "interest_rates": "금리", "money_liquidity": "통화·유동성", "deposits_loans": "예금·대출",
    "payments": "지급결제", "trade_currency": "결제통화", "national_accounts": "국민계정",
    "prices_trade": "물가·무역", "producer_prices": "생산자물가", "corporate_finance": "기업금융",
    "business_sentiment": "기업경기", "consumer_sentiment": "소비자동향", "economic_sentiment": "경제심리",
    "lending_survey": "대출행태", "balance_payments": "국제수지", "flow_of_funds": "상세자금순환", "other": "기타"
}
MAJOR_DOMAINS = {
    "금융·통화": {"interest_rates", "money_liquidity", "deposits_loans", "payments", "trade_currency", "flow_of_funds"},
    "국민경제·물가": {"national_accounts", "prices_trade", "producer_prices"},
    "기업·가계 경기": {"corporate_finance", "business_sentiment", "consumer_sentiment", "economic_sentiment", "lending_survey"},
    "대외경제": {"balance_payments"},
}


def _major_domain(domain: str) -> str:
    return next((major for major, domains in MAJOR_DOMAINS.items() if domain in domains), "기타")


def _friendly_dimension_name(name: str, examples: list[str], index: int) -> str:
    """Return a label meant for people, never an API parameter such as objL1."""
    cleaned = str(name or "").strip()
    joined = " ".join(examples)
    if any(token in joined for token in ("금액", "비중(%)", "비중")):
        return "표시 기준"
    if any(token in joined for token in ("달러", "유로", "엔화", "원화", "파운드", "위안")) and "(" in joined:
        return "국가·결제통화"
    if cleaned and cleaned not in {"계정코드별", "항목별", "분류별", "선택항목"}:
        return cleaned.removesuffix("별") or cleaned
    return f"분류 {index}"


def _catalog_card(table: dict[str, Any], metadata: Any | None = None) -> dict[str, Any]:
    dimensions = table.get("dimensions") or []
    frequency = str(table.get("prd_se") or "-")
    frequency_labels = {"D": "일", "M": "월", "Q": "분기", "H": "반기", "S": "반기", "Y": "년", "A": "년"}
    # Return complete metadata. Compact previews belong in the UI so the
    # lineage card can still expose every source value on demand.
    units = [str(x) for x in (table.get("units") or []) if str(x)]
    unit_scale = next((x for x in units if any(token in x for token in ("조", "억", "백만", "천", "원", "%", "지수"))), units[0] if units else "-")
    metadata_dimensions = list(getattr(metadata, "dimensions", None) or [])
    public_dimensions: list[dict[str, Any]] = []
    for index, dimension in enumerate(dimensions, start=1):
        values = dimension.get("values") or []
        dimension_values = [str(v.get("normalized") or v.get("value_name") or "") for v in values]
        meta_dimension = metadata_dimensions[index - 1] if index <= len(metadata_dimensions) else {}
        # Only selectable dictionary codes are presented as query choices.
        # Metadata also contains hierarchy nodes without local series.
        dimension_values = [value for value in dimension_values if value]
        raw_name = str(meta_dimension.get("obj_name") or dimension.get("dimension_name") or "")
        public_dimensions.append({
            "name": _friendly_dimension_name(raw_name, dimension_values, index),
            "count": len(values),
            "values": [str(v.get("value_name") or v["value_id"]) for v in values],
            "apiParam": str(dimension.get("api_param") or ""),
            "valueOptions": [{"id": str(v["value_id"]), "name": str(v.get("value_name") or v["value_id"])} for v in values],
            "defaultValueId": str(dimension.get("representative_value_id") or (values[0]["value_id"] if values else "")),
        })

    return {
        "tableId": str(table.get("table_id") or ""), "name": str(table.get("table_name") or ""),
        "organization": "한국은행 · KOSIS", "frequency": frequency,
        "frequencyLabel": frequency_labels.get(frequency, frequency), "unitScale": unit_scale,
        "periodStart": str(table.get("period_start_observed") or "-"),
        "periodEnd": str(table.get("period_end_observed") or "-"),
        "items": [str(x) for x in (table.get("item_names") or []) if str(x)],
        "units": units,
        "dimensions": public_dimensions,
    }


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    metadata_by_id = {str(meta.table_id): meta for meta in service.store.available_supported_tables()}
    supported = set(metadata_by_id)
    for table in agent.resolver.tables:
        table_id = str(table.get("table_id") or "")
        if table_id not in supported:
            continue
        domain = str((table.get("domains") or ["other"])[0])
        major = _major_domain(domain)
        middle = DOMAIN_LABELS.get(domain, domain)
        grouped.setdefault(major, {}).setdefault(middle, []).append(_catalog_card(table, metadata_by_id.get(table_id)))
    categories = []
    for major in ("금융·통화", "국민경제·물가", "기업·가계 경기", "대외경제", "기타"):
        middles = grouped.get(major) or {}
        if not middles:
            continue
        categories.append({"name": major, "count": sum(len(v) for v in middles.values()),
                           "children": [{"name": name, "count": len(cards),
                                         "children": sorted(cards, key=lambda x: (x["name"], x["tableId"]))}
                                        for name, cards in sorted(middles.items())]})
    return {"source": "StatBridge 검증 통계사전", "total": sum(x["count"] for x in categories), "categories": categories}


def _rows_preview(execution: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    rows = execution.get("rows") or []
    return [dict(x) for x in rows[:limit] if isinstance(x, dict)]


def _api_period(value: str | None, frequency: str, *, end: bool = False) -> str | None:
    """Convert an exact UI date (YYYY-MM-DD) to the KOSIS period format."""
    if not value:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not match:
        raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
    year, month = int(match.group(1)), int(match.group(2))
    if frequency == "D":
        return value.replace("-", "")
    if frequency == "M":
        return f"{year:04d}{month:02d}"
    if frequency == "Q":
        return f"{year:04d}{((month - 1) // 3) + 1:02d}"
    if frequency in {"H", "S"}:
        return f"{year:04d}{1 if month <= 6 else 2:02d}"
    return f"{year:04d}"


def _ui_period(value: str | None, frequency: str, *, end: bool = False) -> str | None:
    """Convert a KOSIS period into an inclusive HTML date boundary."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 4:
        return None
    year = int(digits[:4])
    if frequency == 'D':
        from expression_tree_validator import period_index
        period_index(digits, 'D')
        return f'{digits[:4]}-{digits[4:6]}-{digits[6:]}'
    if frequency == "M":
        month = max(1, min(12, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    if frequency == "Q":
        quarter = max(1, min(4, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        month = quarter * 3 if end else (quarter - 1) * 3 + 1
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    if frequency in {"H", "S"}:
        half = max(1, min(2, int(digits[4:6] or "1") if len(digits) >= 6 else 1))
        month = half * 6 if end else (half - 1) * 6 + 1
        day = calendar.monthrange(year, month)[1] if end else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{year:04d}-12-31" if end else f"{year:04d}-01-01"


def _availability_from_plans(plans: list[dict[str, Any]]) -> dict[str, str]:
    starts: list[str | None] = []
    ends: list[str | None] = []
    for plan in plans:
        table = agent.resolver.tables_by_id.get(str(plan.get("table_id") or "")) or {}
        frequency = str(plan.get("frequency") or table.get("prd_se") or "")
        starts.append(_ui_period(str(table.get("period_start_observed") or plan.get("start_period") or ""), frequency))
        ends.append(_ui_period(str(table.get("period_end_observed") or plan.get("end_period") or ""), frequency, end=True))
    valid_starts = [x for x in starts if x]
    valid_ends = [x for x in ends if x]
    return {"min": max(valid_starts) if valid_starts else "", "max": min(valid_ends) if valid_ends else ""}


def _availability_from_table_ids(table_ids: list[str]) -> dict[str, str]:
    plans: list[dict[str, Any]] = []
    for table_id in table_ids:
        table = agent.resolver.tables_by_id.get(str(table_id))
        if not table:
            continue
        plans.append({
            "frequency": str(table.get("prd_se") or ""),
            "start_period": str(table.get("period_start_observed") or ""),
            "end_period": str(table.get("period_end_observed") or ""),
        })
    return _availability_from_plans(plans)


def _resolve_selected_options(query_text: str, state: dict[str, Any], selections: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve button selections deterministically, without a second HCX/vector round-trip."""
    groups = [(str(group.get("clarification_id") or ""), [str(v) for v in (group.get("values") or []) if str(v)])
              for group in selections]
    groups = [(group_id, values) for group_id, values in groups if group_id and values]
    count=1
    for _,values in groups:
        count*=len(values)
    if count>5:
        raise HTTPException(status_code=422,detail='선택 조합은 최대 5개입니다. 조건을 줄여 주세요. 일부 조건만 조회하지 않습니다.')
    combinations = list(product(*[[(group_id, value) for value in values] for group_id, values in groups]))
    resolved_parts: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    for combination in combinations:
        next_state = dict(state)
        confirmed = dict(next_state.get("confirmed") or {})
        confirmed.update(dict(combination))
        next_state["confirmed"] = confirmed
        part = agent.run(query=query_text, state=next_state, execute=False, generate_answer=False)
        if part.get("status") != "resolved":
            # Ask the next unresolved question; never drop that branch and show
            # successful siblings as if they represented the entire request.
            return part
        part_plans=part.get('api_plans') or ([part['api_plan']] if part.get('api_plan') else [])
        if not part_plans:
            return {'status':'no_match','missing_series':[query_text]}
        for raw_plan in part_plans:
            plan=dict(raw_plan)
            labels=[value for gid,value in combination if not gid.startswith('canonical_table:')]
            if labels:
                plan['series_label']=plan.get('series_label') or plan['table_name']+' · '+' · '.join(labels)
            key = (str(plan.get("table_id")), str(plan.get("item_id")), tuple(sorted((plan.get("classifications") or {}).items())))
            if key not in seen:
                seen.add(key)
                plans.append(plan)
                resolved_parts.append(part)
    if not plans:
        return {"status": "no_match", "missing_series": [value for _, values in groups for value in values]}
    base = resolved_parts[0]
    base["api_plan"] = plans[0]
    base["api_plans"] = plans
    base["selected_table"] = base.get("selected_table") or {}
    return agent.validate_resolution(query_text,base)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "ui-agent-dictionary-mcp",
        "agent": "StatBridgeAgent-v6-ncp",
        "orchestration_engine": "langgraph",
        "orchestration_nodes": [
            "resolve_request", "await_clarification", "finish_unresolved", "execute_statistics",
            "await_output_selection", "prepare_output",
        ],
        "ncp_api_key_configured": agent.ncp.configured,
        "jev_enabled": agent.jev.settings.enabled,
        "jev_api_key_configured": bool(agent.jev.settings.api_key),
        "jev_model": agent.jev.settings.model,
        "classifier_model": agent.ncp.settings.classifier_model,
        "main_model": agent.ncp.settings.main_model,
        "dictionary_tables": len(agent.resolver.tables),
        "clarification_groups": len(agent.resolver.groups),
        "supported_tables": len(service.store.available_supported_tables()),
        "kosis_api_key_configured": bool(os.getenv("KOSIS_API_KEY", "").strip()),
        "data_dir": str(service.store.data_dir),
    }


def _query_derived(payload):
    q = payload.query.strip()
    result = agent.workflow.invoke(query=q, conversation_state=payload.state, execute=payload.execute,
        start_period=payload.period_start, end_period=payload.period_end,
        generate_answer=False, output_request=None)
    # A fetch/response validation error is not proof of unavailable data. Keep
    # its precise diagnosis and the user's context; replanning the same source
    # cannot repair an API outage, missing unit, or invalid response identity.
    if (result.get('failure_stage') == 'execute_derived_sources'
            and result.get('failure_kind') != 'source_data_absent'):
        return _derived_query_response(payload, result)
    from derived_support_policy import assessment_scope
    assessment=result.get('availability') or {}
    if (not is_availability_check(q) and assessment_scope(assessment)=='mixed'
            and not assessment.get('questions')):
        outcome=derived_supplements.from_assessment(account_auth.current_user()['id'],result.get('query') or q,assessment)
        if outcome.get('supplement'):return outcome
        # Keep original source checks and clarification when file preparation is not ready.
    if not is_availability_check(q) and not result.get('derivedEvidence') and (result.get('missing') or result.get('failure_stage')=='execute_derived_sources') and agent.workflow.derived_runtime.planner.client.configured:
        try:
            outcome=derived_supplements.start(account_auth.current_user()['id'],result.get('query') or q,payload.period_start,payload.period_end,
                planning=result if result.get('status')=='inputs_required' else None)
            if outcome.get('status')!='calculated':return outcome
            result=outcome['result']
        except (ValueError,TypeError,KeyError) as exc:
            result['question']=(result.get('question') or '')+' 파일 보완 준비를 완료하지 못했습니다: '+str(exc)
    return _derived_query_response(payload,result)


def _runtime_for_derived(result):
    identifier=(result.get('_derived') or {}).get('supplemental_workspace_id')
    if not identifier:return agent.workflow.derived_runtime
    w=derived_supplements.load(account_auth.current_user()['id'],identifier)
    # Pin this graph to the immutable file content used in its own receipt.
    # A later corrected upload in the same workspace must not alter old graphs.
    w=copy.deepcopy(w)
    for source in result['derivedEvidence']['plan']['sources']:
        if source['table_id'].startswith('USER_FILE:'):
            stored=result['_derived']['responses'][source['alias']]
            w['files'][source['table_id'].split(':',1)[1]]={**stored,'required_data':source['required_data']}
    return derived_supplements.runtime(w)


def _derived_query_response(payload,result):
    q=payload.query.strip()
    common = {'query': q, 'interpretedQuery': q, 'period': {'start': payload.period_start or '', 'end': payload.period_end or ''},
              'frequency': '', 'chart': [], 'tables': [], 'insights': [], 'lineage': [], 'warnings': [],
              'analysisKind': 'derived', 'state': result.get('state') or {}}
    from derived_support_policy import assessment_scope, unsupported
    if result.get('availability') and assessment_scope(result['availability'])=='unsupported':
        result={**result,**unsupported(q),'availability':result['availability']}
    if result.get('unsupported'):
        question = result['question']
        return {**common, 'status':'need_details', 'state':{}, 'summary':question,
                **({'availability':result['availability']} if result.get('availability') else {}),
                'recovery':{'issue':'unsupported','question':question,
                            'choices':[{'id':'revise','label':'질문 다시 작성'}], 'preserveState':True}}
    if not result.get('derivedEvidence'):
        question = result.get('question') or '파생지표 계산에 필요한 자료와 조건을 확인해 주세요.'
        if result.get('availability'):
            assessment=result['availability']
            user=account_auth.USER.get()
            if user:
                check_id=uuid4().hex
                if len(DERIVED_SOURCE_CHECKS)>=100:DERIVED_SOURCE_CHECKS.pop(next(iter(DERIVED_SOURCE_CHECKS)))
                DERIVED_SOURCE_CHECKS[check_id]={'owner':user['id'],'query':q,'assessment':copy.deepcopy(assessment),'created':time.time()}
                assessment={**assessment,'checkId':check_id}
            return {**common,'status':'need_details','summary':question,'availability':assessment,
                'recovery':{'issue':'source_verification' if assessment['status']!='checked' else 'clarification',
                    'question':'확인 중인 자료는 부족 자료로 단정하지 않습니다.' if assessment['status']!='checked' else '계산 기준을 답해 주시면 같은 요청을 이어갑니다.',
                    'choices':[{'id':'retry','label':'자료 확인 다시 시도'}] if assessment['status']!='checked' else [{'id':'revise','label':'계산 조건 답하기'}],
                    'preserveState':True}}
        if result.get('failure_kind') in {'model_provider_error','model_response_error'}:
            return {**common,'status':'request_failed','summary':question,
                'failure_kind':result['failure_kind'],'failure_stage':result.get('failure_stage'),
                'recovery':{'issue':'model','question':'원자료가 없다는 판정이 아니라 분석 모델의 응답 처리 문제입니다. 입력과 현재 작업을 유지했으니 같은 요청을 다시 시도해 주세요.',
                            'choices':[{'id':'retry','label':'같은 요청 다시 시도'},{'id':'revise','label':'질문 수정'}],'preserveState':True}}
        if result.get('failure_stage') == 'execute_derived_sources':
            return {**common, 'status':'data_unavailable', 'summary':question,
                'failure_kind':result.get('failure_kind'), 'failure_stage':result['failure_stage'],
                'recovery':{'issue':'source_metadata' if result.get('failure_kind') == 'source_metadata_incomplete' else 'source_verification',
                    'question':question, 'choices':[{'id':'retry','label':'자료 확인 다시 시도'},
                                                   {'id':'revise','label':'계산 조건 입력'}],
                    'preserveState':True}}
        return {**common, 'status': 'data_unavailable' if result.get('status') == 'data_unavailable' else 'need_details',
                'summary': question, 'missingData': result.get('missing', []), 'catalogSearches': result.get('searches', []),
                'recovery': {'issue': 'derived_data', 'question': question,
                             'requirements': [x['required_data'] for x in result.get('missing', [])],
                             'choices': [{'id': 'revise', 'label': '계산 조건 입력'}], 'preserveState': True}}
    evidence = result['derivedEvidence']
    plan = evidence['plan']
    frequency = plan['sources'][0]['frequency']
    period = {'start': _ui_period(plan['start_period'], frequency), 'end': _ui_period(plan['end_period'], frequency, end=True)}
    session_id = uuid4().hex
    if len(OUTPUT_SESSIONS) >= MAX_EDIT_SESSIONS:
        OUTPUT_SESSIONS.pop(next(iter(OUTPUT_SESSIONS)))
    OUTPUT_SESSIONS[session_id] = {'created': time.time(), 'result': result, 'query': q,
        'account_id': (account_auth.USER.get() or {}).get('id'), 'table_name': plan['indicator_name'],
        'frequency': plan['sources'][0]['frequency'], 'period': period,
        'available_period': {'min': period['start'], 'max': period['end']}, 'adjusted_period': False,
        'resolve_ms': 0, 'execute_ms': 0}
    inspection = agent.output_agent.inspect(result)
    has_file=any(s.get('origin')=='user_file' for s in evidence['sourceSelections'])
    has_api=any(s.get('origin')=='kosis_api' for s in evidence['sourceSelections'])
    source_label='API·사용자 파일로' if has_file and has_api else '사용자 제공 파일로' if has_file else 'KOSIS 원자료로'
    return {**common, 'status': 'need_output_config', 'summary': source_label+' 파생지표 계산을 완료했습니다. 그래프 종류를 선택해 주세요.',
            'period': period, 'frequency': plan['sources'][0]['frequency'], 'outputSessionId': session_id,
            'outputOptions': inspection, 'seriesCount': inspection['seriesCount'], 'derivedEvidence': evidence,
            'tables': [_table_card(s['table_id']) for s in plan['sources']],
            'warnings': [f"결측·계산 불가 {len(evidence['issues'])}건의 시점별 근거가 기록되었습니다."] if evidence['issues'] else []}


class SupplementStartRequest(BaseModel):
    query:str=Field(min_length=1,max_length=8000)
    period_start:str|None=None
    period_end:str|None=None


DERIVED_SOURCE_CHECKS={}


class SupplementCheckRequest(BaseModel):
    check_id:str=Field(pattern=r'^[a-f0-9]{32}$')


class ComparisonSeriesDeclaration(BaseModel):
    model_config={'extra':'forbid'}
    alias:str=Field(pattern=r'^[a-z][a-z0-9_]{0,47}$')
    label:str=Field(min_length=1,max_length=100)
    direction:Literal['direct','inverse']


class SupplementComparisonRequest(BaseModel):
    model_config={'extra':'forbid'}
    periods:list[dict[str,str]]=Field(min_length=2,max_length=4)
    series:list[ComparisonSeriesDeclaration]=Field(min_length=1,max_length=12)
    average_aliases:list[str]=Field(min_length=1,max_length=12)
    complete_observations_confirmed:bool


class SupplementUploadRequest(BaseModel):
    alias:str=Field(pattern=r'^[a-z][a-z0-9_]{0,47}$')
    filename:str=Field(min_length=1,max_length=120)
    content:str=Field(max_length=2_700_000)
    encoding:Literal['utf8','base64']='utf8'


def _supplement_call(action):
    try:
        outcome=action()
        if outcome.get('status')=='calculated':
            return _derived_query_response(QueryRequest(query=outcome['result']['query']),outcome['result'])
        return outcome
    except KeyError:
        raise HTTPException(404,'자료 보완 작업을 찾을 수 없습니다.') from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


@app.post('/api/derived/supplement')
def start_derived_supplement(payload:SupplementStartRequest):
    return _supplement_call(lambda:derived_supplements.start(account_auth.current_user()['id'],payload.query,payload.period_start,payload.period_end))


@app.get('/api/derived/supplement')
def list_derived_upload_workspaces():
    # Pending uploads are session-only and never advertised on the home screen.
    account_auth.current_user()
    return {'items':[]}


@app.get('/api/derived/supplement/{identifier}')
def restore_derived_upload_workspace(identifier:str):
    return _supplement_call(lambda:derived_supplements.public(derived_supplements.load(account_auth.current_user()['id'],identifier)))


@app.post('/api/derived/supplement/from-check')
def supplement_from_check(payload:SupplementCheckRequest):
    owner=account_auth.current_user()['id'];checked=DERIVED_SOURCE_CHECKS.get(payload.check_id)
    if not checked or checked['owner']!=owner or time.time()-checked['created']>3600:
        raise HTTPException(404,'자료 확인 세션이 만료됐습니다. 같은 질문으로 다시 확인해 주세요.')
    if checked.get('workspace_id'):
        return derived_supplements.public(derived_supplements.load(owner,checked['workspace_id']))
    outcome=_supplement_call(lambda:derived_supplements.from_assessment(owner,checked['query'],checked['assessment']))
    if outcome.get('supplement'):checked['workspace_id']=outcome['supplement']['workspaceId']
    return outcome


@app.post('/api/derived/supplement/{identifier}/comparison')
def configure_supplement_comparison(identifier:str,payload:SupplementComparisonRequest):
    return _supplement_call(lambda:derived_supplements.configure_comparison(account_auth.current_user()['id'],identifier,payload.model_dump()))


@app.get('/api/derived/supplement/{identifier}/template/{alias}')
def derived_supplement_template(identifier:str,alias:str,format:Literal['csv','json','tsv']='csv'):
    try:
        text=derived_supplements.template(account_auth.current_user()['id'],identifier,alias,format)
        media={'csv':'text/csv; charset=utf-8','json':'application/json','tsv':'text/tab-separated-values; charset=utf-8'}[format]
        return Response(text,media_type=media,headers={'Content-Disposition':f'attachment; filename="{alias}.{format}"','Cache-Control':'no-store'})
    except KeyError:raise HTTPException(404,'자료 보완 작업을 찾을 수 없습니다.') from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@app.post('/api/derived/supplement/{identifier}/upload')
def upload_derived_supplement(identifier:str,payload:SupplementUploadRequest):
    return _supplement_call(lambda:derived_supplements.upload(account_auth.current_user()['id'],identifier,payload.alias,payload.filename,payload.content,payload.encoding))


@app.post('/api/derived/supplement/{identifier}/resume')
def resume_derived_supplement(identifier:str):
    return _supplement_call(lambda:derived_supplements.resume(account_auth.current_user()['id'],identifier))


def _derived_output_spec(original, revised, previous):
    spec = ChartSpec.model_validate(previous['chartState'])
    old_plan, new_plan = original['derivedEvidence']['plan'], revised['derivedEvidence']['plan']
    old_outputs, new_outputs = old_plan['outputs'], new_plan['outputs']
    by_node = {out['node']: out['label'] for out in new_outputs}
    mapping = {out['label']: by_node[out['node']] for out in old_outputs if out['node'] in by_node}
    if len(old_outputs) == len(new_outputs):
        for old, new in zip(old_outputs, new_outputs):
            mapping.setdefault(old['label'], new['label'])
    labels = {out['label'] for out in new_outputs}
    changes = {}
    if spec.title == old_plan['indicator_name']:
        changes['title'] = new_plan['indicator_name']
    for field in ('series_colors', 'series_dashes', 'series_styles', 'display_names', 'series_chart_types'):
        changes[field] = {mapping.get(key, key): value for key, value in getattr(spec, field).items()
                          if mapping.get(key, key) in labels}
    for field in ('hidden_series', 'series_order', 'secondary_axis_series'):
        changes[field] = [mapping.get(key, key) for key in getattr(spec, field) if mapping.get(key, key) in labels]
    changes['range_styles'] = [style.model_copy(update={'label': mapping.get(style.label, style.label)})
                              for style in spec.range_styles if mapping.get(style.label, style.label) in labels]
    return spec.model_copy(update=changes)


@app.post("/api/query")
@observed('query.handle', 'span')
def query(payload: QueryRequest) -> dict[str, Any]:
    request_started = time.perf_counter()
    q = payload.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")
    from query_conditions import clarification_question
    condition_question = clarification_question(q, agent.resolver.tables) if not (payload.state or payload.selections or payload.clarification) else None
    if condition_question:
        return {'status':'need_details','query':q,'interpretedQuery':q,'summary':condition_question,
                'period':{},'frequency':'확인 필요','chart':[],'tables':[],'insights':[],'lineage':[],'warnings':[],
                'state':{},'recovery':{'issue':'clarification','question':condition_question,
                'choices':[{'id':'revise','label':'조회 조건 입력'}],'preserveState':True}}
    if is_derived_request(q) or (payload.state or {}).get('_derived_query'):
        return _query_derived(payload)

    # Resolve the table first. Numeric execution waits until the user has
    # explicitly chosen the chart period.
    selected_values = [str(value).strip() for group in (payload.selections or [])
                       for value in (group.get("values") or []) if str(value).strip()]
    agent_query = q
    agent_state = payload.state
    agent_clarification = payload.clarification.model_dump() if payload.clarification else None
    if selected_values:
        # Selection values are constraints in state, not additions to the user's
        # text. Appending one selected ID would prohibit other requested tables.
        result = _resolve_selected_options(q, dict(payload.state or {}), payload.selections or [])
    else:
        result = agent.run(
            query=agent_query,
            state=agent_state,
            clarification=agent_clarification,
            execute=False,
        )
    resolve_ms = round((time.perf_counter() - request_started) * 1000)
    if result.get("status") == "resolved":
        result = agent.validate_resolution(q, result)

    if result.get("status") == "need_clarification":
        candidate_ids = [str(x.get("table_id") or "") for x in (result.get("state", {}).get("candidate_tables") or [])]
        available_period = _availability_from_table_ids(candidate_ids)
        raw_groups = result.get("clarifications") or [result]
        clarification_groups = [{
            "id": group["clarification_id"], "question": group["question"],
            "ui": "multi_select_buttons",
            "options": [{"label": o["label"], "value": o["value"]} for o in group.get("options", [])],
        } for group in raw_groups]
        return {
            "status": "need_clarification",
            "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "clarification": {
                "id": result["clarification_id"],
                "question": result["question"],
                "ui": result.get("ui", "single_select_buttons"),
                "options": [
                    {"label": o["label"], "value": o["value"]}
                    for o in result.get("options", [])
                ],
            },
            "clarifications": clarification_groups,
            "state": result["state"],
            "summary": "HCX-003가 자연어를 통계언어로 정리한 뒤 사전에서 여러 후보가 남았습니다. HCX-007이 사전 선택지를 바탕으로 한 번에 하나의 역질문을 합니다.",
            "period": {"start": "-", "end": "-"},
            "availablePeriod": available_period,
            "frequency": "확인 필요",
            "chart": [],
            "tables": [
                _table_card(x["table_id"])
                for x in (result.get("state", {}).get("candidate_tables") or [])[:4]
            ],
            "insights": ["Agent가 임의로 통계를 선택하지 않고 사용자 확인을 요청했습니다."],
            "lineage": [
                {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
                {"id": "hcx003", "title": "HCX-003 통계언어 분류", "description": str((result.get("classification") or {}).get("normalized_query") or q), "status": "complete"},
                {"id": "dictionary", "title": "통계언어 사전 검색", "description": "v5 사전에서 후보 생성", "status": "complete"},
                {"id": "hcx007", "title": "HCX-007 메인 Agent", "description": "사전 후보의 차이를 사용자에게 역질문", "status": "complete"},
                {"id": "clarify", "title": "역질문", "description": result["question"], "status": "active"},
            ],
            "warnings": [],
            "debug": {"timingMs": {"resolve": resolve_ms, "total": resolve_ms}},
        }

    if result.get("status") == "catalog_only":
        table = result.get("selected_table") or {}
        return {
            "status": "catalog_only", "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "summary": "통계표는 카탈로그에서 확인되지만 현재 로컬 조회 데이터가 없어 수치 조회를 중단했습니다.",
            "period": {"start": "-", "end": "-"}, "frequency": "-", "chart": [],
            "tables": [{**_table_card(str(table.get("table_id") or "")), "name": str(table.get("table_name") or table.get("table_id") or "통계표"), "source": "한국은행 KOSIS 카탈로그"}],
            "insights": [], "warnings": ["카탈로그 전용 표입니다. 로컬 메타데이터와 조회 파라미터가 확보되기 전에는 KOSIS 수치를 호출하지 않습니다."],
        }

    if result.get("status") != "resolved":
        missing = [str(x) for x in result.get("missing_series") or []]
        missing_text = f" 지원 데이터에서 찾지 못한 지표: {', '.join(missing)}." if missing else ""
        try:
            requested_period = explicit_period(q) or {}
        except ValueError:
            requested_period = {}
        return {
            "status": "no_match",
            "query": q,
            "interpretedQuery": result.get("dictionary_query") or q,
            "summary": "요청한 모든 지표에 대응하는 통계표를 찾지 못했습니다." + missing_text,
            "requiresQueryRefinement": True,
            "refinementQuestion": ("지원 목록에서 「" + ', '.join(missing) + "」에 대응하는 표를 확인하지 못했습니다. 정확한 지표명이나 통계표 이름을 알려 주시겠어요?") if missing else "요청과 일치하는 표를 확정하지 못했습니다. 조회할 지표와 기준을 구체적으로 알려 주시겠어요?",
            "period": {"start": payload.period_start or requested_period.get('start',''), "end": payload.period_end or requested_period.get('end','')},
            "state": result.get('state') or payload.state,
            "recovery": recovery_question('지원 목록에서 확인되지 않은 지표: ' + ', '.join(missing) if missing else '요청 조건에 대응하는 통계표를 확인하지 못했습니다.', issue='no_match'),
            "frequency": "-",
            "chart": [], "tables": [], "insights": [],
            "lineage": [
                {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
                {"id": "agent", "title": "Agent 사전 검색", "description": "후보 없음", "status": "active"},
            ],
            "warnings": (result.get("request_validation") or {}).get("errors") or ["일부 지표를 다른 표현으로 바꾸거나 지원되는 지표만 선택해 주세요."],
            "requestValidation": result.get("request_validation"),
        }

    dimension_selection = payload.dimension_values if payload.dimension_values is not None else (payload.state or {}).get("_dimension_values", {})
    if dimension_selection:
        if len(result.get("api_plans") or [result["api_plan"]]) != 1:
            raise HTTPException(status_code=400, detail="분류 직접 선택은 단일 통계표 조회에만 사용할 수 있습니다.")
        table = agent.tables_by_id[result["api_plan"]["table_id"]]
        valid = {d["api_param"]: {v["value_id"]: v for v in d["values"]} for d in table["dimensions"]}
        selected = dict(result["selected_table"])
        # Explicit catalog selections outrank the conversation comparison default.
        selected.pop("_comparison_default", None)
        hits = {h["api_param"]: h for h in selected.get("dimension_hits", [])}
        for param, value_id in dimension_selection.items():
            if param not in valid or value_id not in valid[param]:
                raise HTTPException(status_code=400, detail="선택한 통계표에 존재하지 않는 분류 코드입니다.")
            value = valid[param][value_id]
            hits[param] = {"api_param": param, "value_id": value_id, "value_name": value["value_name"]}
        selected["dimension_hits"] = list(hits.values())
        result["selected_table"] = selected
        result["api_plan"] = agent.build_api_plan(q, selected).as_dict()
        result["api_plans"] = [result["api_plan"]]
        result.setdefault("state", {})["_dimension_values"] = dimension_selection
    missing_dimensions = list(dict.fromkeys(label for item in result.get('selected_tables', []) for label in item.get('_missing_dimensions', [])))
    if missing_dimensions:
        plans = result.get('api_plans') or [result['api_plan']]
        requested_period = explicit_period(q) or {}
        detail = f"요청한 통계표 {len(plans)}개와 조회 기간을 확인했습니다. 조회할 분류가 지정되지 않아 데이터 호출을 기다리고 있습니다."
        return {'status': 'need_details', 'query': q, 'interpretedQuery': q,
                'summary': detail, 'period': {'start': payload.period_start or requested_period.get('start', ''), 'end': payload.period_end or requested_period.get('end', '')},
                'frequency': '/'.join(dict.fromkeys(p['frequency'] for p in plans)), 'chart': [], 'tables': [_table_card(p['table_id']) for p in plans],
                'insights': [], 'warnings': [], 'state': result.get('state') or {},
                'recovery': {'issue': 'dimensions', 'requirements': missing_dimensions, 'question': '아래에서 필요한 분류를 확인해 질문에 넣어 주세요. 입력하신 통계표와 기간은 유지됩니다.', 'choices': [{'id': 'revise', 'label': '조회 조건 입력'}, {'id': 'table', 'label': '통계표 분류 확인'}], 'preserveState': True}}
    plan = result["api_plan"]
    plans = result.get("api_plans") or [plan]
    table_name = " · ".join(str(x.get("series_label") or x["table_name"]) for x in plans)
    frequencies = list(dict.fromkeys(str(x["frequency"]) for x in plans))
    frequency_label = "/".join(frequencies)
    available_period = _availability_from_plans(plans)
    if re.search(r'최신|가장\s*최근|관측값.*마지막|발표된\s*마지막', q) and re.search(r'부터|이후', q):
        # Catalog snapshots lag releases. Resolve an explicit latest-observation
        # endpoint from this exact source, not today's calendar or another series.
        from datetime import date
        latest_ends=[]
        for selected_plan in plans:
            frequency=selected_plan['frequency']
            end=_api_period(date.today().isoformat(),frequency,end=True)
            data=service.client.get_statistics(selected_plan['org_id'],selected_plan['table_id'],
                selected_plan['item_id'],frequency,selected_plan['start_period'],end,
                selected_plan['classifications'])
            if data:
                latest_ends.append(_ui_period(max(str(row['PRD_DE']) for row in data),frequency,end=True))
        if latest_ends:available_period['max']=min(latest_ends)
    text_period = None
    if not payload.period_start and not payload.period_end:
        try:
            period_text=instruction_text(q, agent.resolver.tables)
            text_period = explicit_period(period_text, latest=available_period["max"])
            if not text_period and all(plan['frequency'] in {'A','Y'} for plan in plans):
                text_period=explicit_period(period_text+' 연간',latest=available_period['max'])
        except (ValueError, OverflowError) as exc:
            raise HTTPException(status_code=400, detail=f"날짜를 확인해 주세요: {exc}") from exc
    period_start = payload.period_start or (text_period or {}).get("start")
    period_end = payload.period_end or (text_period or {}).get("end")
    inherited_period = None
    if not period_start and not period_end:
        prior_context = agent._validated_followup_plans(payload.state)
        if prior_context:
            prior_plans = prior_context[1]
            frequencies = {str(plan.get("frequency") or "") for plan in prior_plans}
            starts = {str(plan.get("start_period") or "") for plan in prior_plans}
            ends = {str(plan.get("end_period") or "") for plan in prior_plans}
            if len(frequencies) == len(starts) == len(ends) == 1 and next(iter(starts)) and next(iter(ends)):
                frequency = next(iter(frequencies))
                inherited_period = {
                    "start": _ui_period(next(iter(starts)), frequency),
                    "end": _ui_period(next(iter(ends)), frequency, end=True),
                }
                if inherited_period["start"] and inherited_period["end"]:
                    period_start, period_end = inherited_period["start"], inherited_period["end"]
    outside = bool(text_period and ((available_period["min"] and period_start < available_period["min"]) or (available_period["max"] and period_end > available_period["max"])))
    if not period_start or not period_end or outside or (text_period and not payload.execute):
        return {
            "status": "need_period",
            "query": q,
            "interpretedQuery": table_name,
            "summary": "텍스트의 기간이 제공 범위를 벗어났습니다. 사용 가능한 기간을 확인해 주세요." if outside else ("텍스트의 조회 기간을 확인했습니다. 날짜를 다시 선택할 필요가 없습니다." if text_period else "통계표를 찾았습니다. 그래프로 볼 정확한 시작일과 종료일을 입력해 주세요."),
            "period": {"start": available_period["min"], "end": available_period["max"]},
            "availablePeriod": available_period,
            "frequency": frequency_label,
            "chart": [],
            "chartMode": payload.chart_mode or "combined",
            "tables": [_table_card(x["table_id"]) for x in plans],
            "insights": [],
            "lineage": [
                {"id": "table", "title": "통계표 확정", "description": table_name, "status": "complete"},
                {"id": "period", "title": "조회 기간", "description": "사용자 입력 대기", "status": "active"},
            ],
            "warnings": [f"요청 기간: {period_start}–{period_end}; 제공 기간: {available_period['min']}–{available_period['max']}"] if outside else [],
            "periodSelection": ({"source":text_period.get("source","text"),"basis":text_period.get("basis"),"requested":{"start":period_start,"end":period_end},"applied":{"start":period_start,"end":period_end}} if text_period and not outside else
                                {"source":"context","requested":inherited_period,"applied":inherited_period} if inherited_period else text_period),
            "state": result.get("state") or {},
            "debug": {"timingMs": {"resolve": resolve_ms, "total": resolve_ms}},
        }

    # Keep the live endpoint already resolved above for latest-observation requests.
    selected_start = period_start
    selected_end = period_end
    period_selection = {
        "source": (text_period or {}).get("source", "text") if text_period else "context" if inherited_period else "ui",
        "basis": (text_period or {}).get("basis"),
        "requested": {"start": period_start, "end": period_end},
        "applied": {"start": selected_start, "end": selected_end},
    }
    adjusted_period = False
    if available_period["min"] and selected_start < available_period["min"]:
        selected_start = available_period["min"]
        adjusted_period = True
    if available_period["max"] and selected_end > available_period["max"]:
        selected_end = available_period["max"]
        adjusted_period = True
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="선택한 통계표들이 함께 제공되는 기간이 없습니다.")
    period_selection["applied"] = {"start":selected_start,"end":selected_end}

    period_overrides = {
        str(x["table_id"]): (
            str(_api_period(selected_start, str(x["frequency"])) or ""),
            str(_api_period(selected_end, str(x["frequency"]), end=True) or ""),
        ) for x in plans
    }
    if any(start > end for start, end in period_overrides.values()):
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 늦을 수 없습니다.")
    execution_started = time.perf_counter()
    result = agent.run_resolution(
        query=agent_query,
        resolution=result,
        execute=payload.execute,
        period_overrides=period_overrides,
        generate_answer=False,
        output_request={
            **(payload.chart_options or {}),
            "chart_type": payload.chart_type,
            "layout": payload.chart_mode or "combined",
        },
    )
    execute_ms = round((time.perf_counter() - execution_started) * 1000)

    if result.get("status") == "no_match":
        return {"status":"no_match", "query":q, "interpretedQuery":q, "summary":"입력한 통계와 조회 대상이 일치하지 않아 출력을 중단했습니다.",
                "period":{"start":selected_start,"end":selected_end}, "frequency":frequency_label,
                "recovery":recovery_question("조회 조건과 실제 조회 계획이 일치하지 않습니다.",issue='no_match'),
                "state":result.get("state"), "warnings":result.get("request_validation",{}).get("errors",[]),
                "requestValidation":result.get("request_validation"), "tables":[], "insights":[], "chart":[], "rows":[]}

    plan = result["api_plan"]
    plans = result.get("api_plans") or [plan]
    table_name = " · ".join(str(x.get("series_label") or x["table_name"]) for x in plans)
    frequencies = list(dict.fromkeys(str(x["frequency"]) for x in plans))
    frequency_label = "/".join(frequencies)
    execution = result.get("execution") or {}
    selected = result["selected_table"]
    candidates = result.get("candidates") or []
    execution_status = execution.get("status")

    output = result.get("output") or {}
    visualization = output.get("visualization") or {}
    chart = visualization.get("series") or []
    if execution_status == "success" and not output:
        session_id = uuid4().hex
        OUTPUT_SESSIONS[session_id] = {
            "created": time.time(), "result": result, "query": q, "account_id":(account_auth.USER.get() or {}).get("id"),
            "table_name": table_name, "frequency": frequency_label,
            "period": {"start": selected_start, "end": selected_end},
            "available_period": available_period, "adjusted_period": adjusted_period,
            "resolve_ms": resolve_ms, "execute_ms": execute_ms,
            "periodSelection": period_selection,
        }
        inspection = agent.output_agent.inspect(result)
        return {
            "status": "need_output_config",
            "periodSelection": period_selection,
            "state": result.get("state") or payload.state or {},
            "query": q,
            "interpretedQuery": table_name,
            "summary": "MCP 데이터 조회가 끝났습니다. 출력할 그래프 종류와 편집 옵션을 선택해 주세요.",
            "period": {"start": selected_start, "end": selected_end},
            "availablePeriod": available_period,
            "frequency": frequency_label,
            "chart": [],
            "chartMode": None,
            "chartType": None,
            "seriesCount": inspection["seriesCount"],
            "outputSessionId": session_id,
            "outputOptions": inspection,
            "tables": [_table_card(x["table_id"]) for x in plans],
            "insights": [f"MCP 조회 완료: {execution.get('row_count', 0)}개 행"],
            "lineage": [
                {"id": "mcp", "title": "MCP 통계 데이터", "description": f"{execution.get('row_count', 0)}개 행 수신", "status": "complete"},
                {"id": "output", "title": "출력 Agent", "description": "그래프 주문 대기", "status": "active"},
            ],
            "warnings": [],
        }
    if execution_status == "success":
        summary = str(output.get("summary") or "출력 에이전트가 그래프 명세를 생성했습니다.")
        warnings: list[str] = ([f"선택 기간을 원자료 제공 범위({available_period['min']}–{available_period['max']})에 맞춰 조정했습니다."] if adjusted_period else [])
    elif execution_status == "planned_only":
        summary = f"Agent가 '{plan['table_name']}'을 선택해 정확한 KOSIS API 호출 파라미터까지 만들었습니다. 실행은 요청에 따라 생략했습니다."
        warnings = []
    else:
        summary = f"Agent가 '{plan['table_name']}'과 실제 사전의 API ID를 확정했습니다. KOSIS 실행 단계에서 오류가 발생했지만 생성된 요청 파라미터는 확인할 수 있습니다."
        if 'KOSIS_API_KEY가 설정되지 않았습니다.' in str(execution.get('error') or ''):
            summary = (f"{plan['table_name']}의 조회 조건은 확인했습니다. 실제 수치 조회를 위해 "
                       "이 프로젝트 .env에 KOSIS_API_KEY를 입력하고 START_STATBRIDGE.cmd로 다시 실행해 주세요.")
        warnings = [str(execution.get("error") or "KOSIS API 실행 오류")]

    return {
        "status": "resolved" if execution_status in {"success", "planned_only"} else "data_unavailable",
        **({"recovery": recovery_question(execution.get("error") or "통계 조회 결과를 확인하지 못했습니다.")} if execution_status not in {"success", "planned_only"} else {}),
        "periodSelection": period_selection,
        "query": q,
        "interpretedQuery": table_name,
        "summary": summary,
        "period": {"start": selected_start, "end": selected_end},
        "availablePeriod": available_period,
        "frequency": frequency_label,
        "chart": chart,
        "chartMode": visualization.get("layout") or payload.chart_mode or "combined",
        "chartType": visualization.get("chartType") or payload.chart_type or "line",
        "outputSpec": output,
        "tables": [_table_card(x["table_id"]) for x in plans],
        "insights": [
            f"선택 통계표: {len(plans)}개",
            *[f"{x.get('series_label') or x['table_name']}: {x['table_name']} ({x['table_id']})" for x in plans],
        ],
        "lineage": [
            {"id": "ui", "title": "UI 자연어 수신", "description": q, "status": "complete"},
            {"id": "hcx003", "title": "HCX-003 자연어 → 통계언어", "description": str((result.get("classification") or {}).get("normalized_query") or result.get("dictionary_query") or q), "status": "complete"},
            {"id": "dictionary", "title": "통계언어 사전 검색", "description": f"{len(agent.resolver.tables)}개 통계표 사전에서 {len(candidates)}개 후보 평가", "status": "complete"},
            {"id": "hcx007", "title": "HCX-007 메인 Agent", "description": f"{len(plans)}개 비교 계열 확정 및 답변 처리", "status": "complete"},
            {"id": "mcp", "title": "MCP 통계 서비스", "description": "검증된 table/item/objL/기간 파라미터 전달", "status": "complete" if execution_status in {"success", "planned_only"} else "active"},
            {"id": "kosis", "title": "KOSIS API", "description": execution_status or "unknown", "status": "complete" if execution_status == "success" else "active"},
            {"id": "output", "title": "출력 Agent", "description": f"{visualization.get('chartType') or 'chart'} 그래프 명세와 편집 옵션 생성", "status": "complete" if output.get("status") == "ready" else "active"},
        ],
        "warnings": warnings,
        "state": result.get("state") or payload.state or {},
        "debug": {
            "selectedTable": selected,
            "apiPlan": plan,
            "executionStatus": execution_status,
            "rowsPreview": _rows_preview(execution),
            "classification": result.get("classification"),
            "dictionaryQuery": result.get("dictionary_query"),
            "ncpModels": {"classifier": agent.ncp.settings.classifier_model, "main": agent.ncp.settings.main_model},
            "timingMs": {"resolve": resolve_ms, "execute": execute_ms, "total": round((time.perf_counter() - request_started) * 1000)},
        },
    }


@app.post("/api/output/options")
def inspect_output_options(payload: OutputOptionsRequest) -> dict[str, Any]:
    sessions=[OUTPUT_SESSIONS.get(identifier) for identifier in payload.session_ids]
    if any(session is None for session in sessions):
        raise HTTPException(status_code=404,detail="출력 데이터가 만료되었습니다. 다시 조회해 주세요.")
    results=[session["result"] for session in sessions]
    merged={"execution":{"rows":[row for result in results for row in (result.get("execution") or {}).get("rows",[])]}}
    try:
        return agent.output_agent.inspect(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/api/output")
def render_output(payload: OutputRenderRequest) -> dict[str, Any]:
    sessions = []
    for session_id in payload.session_ids:
        session = OUTPUT_SESSIONS.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="output session not found or expired")
        sessions.append(session)

    base = sessions[0]
    results = [session["result"] for session in sessions]
    derived = any(result.get('derivedEvidence') for result in results)
    if derived and len(results) != 1:
        raise HTTPException(status_code=422, detail='파생지표는 한 계산 계획씩 출력해 주세요. 비교는 같은 질문에서 요청할 수 있습니다.')
    output_periods = {}
    for session in sessions:
        plans = session["result"].get("api_plans") or [session["result"].get("api_plan")]
        confirmed_periods = {str(plan["table_id"]): (
            _api_period(session["period"]["start"], str(plan["frequency"])),
            _api_period(session["period"]["end"], str(plan["frequency"]), end=True),
        ) for plan in plans if plan and session.get("period")}
        output_periods.update(confirmed_periods)
        if session['result'].get('derivedEvidence'):
            from expression_tree_schema import ExpressionPlan
            _runtime_for_derived(session['result']).validate(ExpressionPlan.model_validate(session['result']['derivedEvidence']['plan']))
            continue
        checked = agent.validate_resolution(str(session["query"]), session["result"], confirmed_periods=confirmed_periods)
        if checked.get("status") != "resolved":
            raise HTTPException(status_code=422, detail="요청과 통계표가 일치하지 않습니다. 다시 검색해 주세요.")
    output_intent=structure_request(payload.natural_language or '')
    if not derived and payload.natural_language and (output_intent['metrics'] or output_intent['explicit_ids']
            or catalog_mentions(payload.natural_language,agent.tables_by_id.values())
            or unsupported_quantity_terms(payload.natural_language,agent.tables_by_id.values())):
        # Each session's clarification constraints were checked above. They belong
        # to that session, not to unrelated tables merged from another request.
        checked = agent.validate_resolution(payload.natural_language, {**results[0],"state":{},"api_plans":[plan for result in results for plan in (result.get("api_plans") or [result.get("api_plan")]) if plan]}, confirmed_periods=output_periods)
        if checked.get("status") != "resolved":
            reasons=' / '.join(checked.get('request_validation',{}).get('errors',[]))
            raise HTTPException(status_code=422, detail="출력 요청과 조회된 통계표를 확인해 주세요. "+reasons)
    merged = dict(results[0])
    merged["query"] = payload.natural_language or " · ".join(s["query"] for s in sessions)
    merged_execution = {
        "status": "success",
        "rows": [row for result in results for row in ((result.get("execution") or {}).get("rows") or [])],
        "row_count": sum(int((result.get("execution") or {}).get("row_count") or 0) for result in results),
        "sources": [source for result in results for source in ((result.get("execution") or {}).get("sources") or [])],
    }
    merged_execution["calculations"]=[c for result in results for c in (result.get("execution") or {}).get("calculations",[])]
    merged["execution"] = merged_execution
    merged["api_plans"] = [plan for result in results for plan in (result.get("api_plans") or [result.get("api_plan")]) if plan]
    merged["request_validation"] = {"valid":True,"table_ids":[p["table_id"] for p in merged["api_plans"]],"requests":[s["query"] for s in sessions]}
    merged["candidates"] = [candidate for result in results for candidate in (result.get("candidates") or [])]
    output_request = {
        "quick_output": payload.quick_output,
        "chart_type": payload.chart_type,
        "layout": payload.chart_mode,
        "title": payload.title,
        "show_legend": payload.show_legend,
        "x_axis_label": payload.x_axis_label,
        "y_axis_label": payload.y_axis_label,
        "natural_language": payload.natural_language or " · ".join(str(session["query"]) for session in sessions),
    }
    if derived:
        output_request.update(quick_output=True,
                              title=payload.title or results[0]['derivedEvidence']['plan']['indicator_name'])
    try:
        rendered = agent.render_output(merged, output_request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    output = rendered.get("output") or {}
    output["editCapabilities"] = {"model": edit_agent.model.model_name, "supportsImages": edit_agent.model.supports_images}
    edit_session_id = uuid4().hex
    if len(EDIT_SESSIONS) >= MAX_EDIT_SESSIONS:
        EDIT_SESSIONS.pop(next(iter(EDIT_SESSIONS)))
    output.update(editVersion=0, canUndo=False, canRedo=False)
    execution_runs=initial_traces(results,rendered,merged)
    EDIT_SESSIONS[edit_session_id] = {"account_id":(account_auth.USER.get() or {}).get("id"), "result": merged, "output": output, "sessions": sessions, "undo": [], "redo": [],"executionRuns":execution_runs,'initial':copy.deepcopy({'result':merged,'sessions':sessions,'chartState':output['chartState']})}
    visualization = output.get("visualization") or {}
    chart = visualization.get("series") or []
    plans = rendered.get("api_plans") or [rendered.get("api_plan")]
    plans = [plan for plan in plans if plan]
    table_name = " · ".join(str(session["table_name"]) for session in sessions)
    starts = sorted(str(session["period"]["start"]) for session in sessions)
    ends = sorted(str(session["period"]["end"]) for session in sessions)
    frequencies = list(dict.fromkeys(str(session["frequency"]) for session in sessions))
    candidates = rendered.get("candidates") or []
    adjusted = any(bool(session.get("adjusted_period")) for session in sessions)
    warnings = (["선택 기간 일부를 원자료 제공 범위에 맞춰 조정했습니다."] if adjusted else [])
    warnings.extend(output.get('warnings') or [])
    response = {
        "status": "resolved",
        **({'analysisKind': 'derived', 'derivedEvidence': merged['derivedEvidence']} if derived else {}),
        "periodSelection": base.get("periodSelection"),
        "query": " · ".join(str(session["query"]) for session in sessions),
        "interpretedQuery": table_name,
        "summary": str(output.get("summary") or "출력 에이전트가 그래프 명세를 생성했습니다."),
        "period": {"start": starts[0], "end": ends[-1]},
        "frequency": " / ".join(frequencies),
        "chart": chart,
        "chartMode": visualization.get("layout") or payload.chart_mode,
        "chartType": visualization.get("chartType") or payload.chart_type,
        "outputSpec": output,
        "editSessionId": edit_session_id,
        "executionRuns":execution_runs,
        "seriesCount": len(chart),
        "tables": [_table_card(str(plan["table_id"])) for plan in plans],
        "insights": [
            f"출력 Agent가 MCP {merged_execution['row_count']}개 행을 {len(chart)}개 계열로 변환",
            *[f"{plan.get('series_label') or plan['table_name']}: {plan['table_id']}" for plan in plans],
        ],
        "lineage": [
            {"id": "input", "title": "입력 Agent", "description": "통계표·기간 확정", "status": "complete"},
            {"id": "mcp", "title": "MCP 통계 서비스", "description": f"{merged_execution['row_count']}개 행 전달", "status": "complete"},
            {"id": "output", "title": "출력 Agent", "description": f"{visualization.get('chartType')} 그래프와 편집 명세 생성", "status": "complete"},
        ],
        "warnings": warnings,
        "debug": {
            "executionStatus": "success",
            "orchestration": rendered.get("orchestration"),
            "candidateCount": len(candidates),
        },
    }
    for session_id in payload.session_ids:
        OUTPUT_SESSIONS.pop(session_id, None)
    return response


@app.post("/api/output/edit")
@observed('edit.validate_apply', 'span')
def edit_output(payload: OutputEditRequest) -> dict[str, Any]:
    session = EDIT_SESSIONS.get(payload.edit_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="output edit session not found or expired")
    previous = session["output"]
    revision = previous.get("editVersion", 0)
    if payload.revision is not None and payload.revision != revision:
        raise HTTPException(status_code=409, detail="그래프가 변경되었습니다. 최신 그래프에서 다시 수정해 주세요.")
    used_edit_graph=False
    reference_data_update=False
    candidate_result=session["result"]
    candidate_sessions=session["sessions"]
    if payload.visual and payload.visual.reference_files:
        if payload.reference_files:
            raise HTTPException(status_code=422, detail='참고 파일은 한 곳에만 첨부해 주세요.')
        payload.reference_files = payload.visual.reference_files
    if payload.reference_files:
        if not edit_agent.model.supports_images:
            raise HTTPException(status_code=422, detail='현재 모델은 참고 파일을 읽을 수 없습니다. 이미지 입력 모델을 설정해 주세요.')
        if payload.action != 'edit' or payload.commands or payload.document or payload.restore_initial or payload.append_session_ids or payload.replace_session_ids:
            raise HTTPException(status_code=422, detail='참고 파일은 자연어 수정 내용과 함께 별도로 적용해 주세요.')
        payload.visual = (payload.visual or VisualEditContext()).model_copy(update={'reference_files':payload.reference_files})
    period_update=None
    try:
        if payload.derived_node_patch and (not candidate_result.get('_derived') or payload.instruction or payload.commands or payload.document or payload.action != 'edit' or payload.restore_initial):
            raise ValueError('계산 노드 수정은 파생지표에서 다른 수정과 분리해 요청해 주세요.')
        if candidate_result.get('_derived') and any(c.operation in {'set_transform'} for c in payload.commands):
            raise ValueError('파생지표 계산 조건은 자연어 또는 계산 노드 수정으로 변경해 주세요.')
        data_change=bool(payload.append_session_ids or payload.replace_session_ids)
        if candidate_result.get('_derived') and data_change:
            raise ValueError('파생지표 비교 항목은 자연어로 요청해 원자료와 계산식을 함께 검증해 주세요.')
        if sum([data_change,payload.restore_initial,bool(payload.undo_property)])>1:raise ValueError('데이터 변경·복원·개별 취소를 따로 실행해 주세요.')
        if (data_change or payload.restore_initial or payload.undo_property) and (payload.action!='edit' or payload.instruction or payload.commands or payload.report or payload.clear_report or payload.document or payload.visual or payload.stickers is not None):
            raise ValueError('데이터 변경·원본 복원은 다른 수정과 분리해 실행하세요.')
        if payload.append_session_ids and payload.replace_session_ids:raise ValueError('추가와 교체를 동시에 요청할 수 없습니다.')
        direct=list(payload.commands)
        if payload.report is not None:
            direct.append(ChartEditCommand(operation='set_report',kind='STYLE_EDIT',params={'patch':payload.report.model_dump()}))
        if payload.clear_report:
            if payload.report is not None:raise ValueError('보고서 적용과 해제를 함께 요청할 수 없습니다.')
            direct.append(ChartEditCommand(operation='set_report',kind='STYLE_EDIT',params={'clear':True}))
        if direct and (payload.action!='edit' or payload.instruction.strip() or payload.visual is not None):
            raise ValueError('직접 설정 명령은 텍스트·그림 지시 또는 되돌리기와 분리해 적용하세요.')
        if payload.stickers is not None and payload.action != "edit":
            raise ValueError("이모티콘 배치는 편집 요청에서만 적용할 수 있습니다.")
        if payload.undo_property:
            state=copy.deepcopy(previous['chartState']);field='series_colors' if payload.undo_property=='series_color' else payload.undo_property
            if field=='series_colors':
                labels={s['label'] for s in agent.output_agent._series(candidate_result.get('execution',{}).get('rows',[]))}
                if payload.property_label not in labels:raise ValueError('되돌릴 데이터 항목을 선택해 주세요.')
                def value(item):
                    from chart_designs import palette
                    from advanced_chart_spec import PALETTE
                    chosen=ChartSpec.model_validate(item['chartState'])
                    if payload.property_label in chosen.series_styles and chosen.series_styles[payload.property_label].color:
                        return chosen.series_styles[payload.property_label].color
                    if payload.property_label in chosen.series_colors:return chosen.series_colors[payload.property_label]
                    raw=agent.output_agent._series(item.get('result',candidate_result).get('execution',{}).get('rows',[]))
                    shown=agent.output_agent._apply_data_edits(raw,chosen)
                    colors=palette(chosen) or (PALETTE if chosen.report else None)
                    for i,s in enumerate(shown):
                        if s['label']==payload.property_label:return colors[i%len(colors)] if colors else s['color']
                    return None
                current=value({'chartState':state,'result':candidate_result})
            else:
                current=state[field];value=lambda item:item['chartState'][field]
            old=next((item for item in reversed(session.get('undo',[])) if value(item)!=current),None)
            if not old:raise ValueError('선택한 속성의 이전 수정이 없습니다.')
            if field=='series_colors':
                if value(old) is None:state[field].pop(payload.property_label,None)
                else:state[field][payload.property_label]=value(old)
                if payload.property_label in state['series_styles']:state['series_styles'][payload.property_label]['color']=None
            else:state[field]=value(old)
            output=agent.output_agent._build(candidate_result,ChartSpec.model_validate(state),generate_explanation=False)
            output['editCapabilities']=previous.get('editCapabilities',{})
        elif data_change:
            candidate_result,candidate_sessions,output=workspace_data(session,payload.replace_session_ids or payload.append_session_ids,bool(payload.replace_session_ids))
            output['editCapabilities']=previous.get('editCapabilities',{})
        elif payload.restore_initial:
            original=session.get('initial') or {'chartState':previous['chartState'],'result':session['result'],'sessions':session['sessions']}
            candidate_result=original['result'];candidate_sessions=original['sessions']
            output=agent.output_agent._build(candidate_result,ChartSpec.model_validate(original['chartState']),generate_explanation=False)
            output['editCapabilities']=previous.get('editCapabilities',{})
        elif payload.document is not None:
            from report_export import restore_document
            if payload.action!='edit' or payload.instruction or payload.visual or payload.stickers is not None or direct:
                raise ValueError('JSON 복원은 별도 요청으로 실행해 주세요.')
            if len(json.dumps(payload.document,ensure_ascii=False))>8_000_000:
                raise ValueError('보고서 JSON은 최대 8MB입니다.')
            restored=restore_document(candidate_result,payload.document)
            output=agent.output_agent._build(candidate_result,restored,generate_explanation=False)
            output['editCapabilities']=previous.get('editCapabilities',{})
        elif direct:
            if any(c.operation == 'set_transform' and c.value == 'year_over_year' for c in direct):
                from chart_comparison_history import ensure_comparison_history
                candidate_result = ensure_comparison_history(candidate_result, agent.service)
            if payload.stickers is not None:
                direct.append(ChartEditCommand(operation='set_stickers',kind='STYLE_EDIT',params={'items':[s.model_dump() for s in payload.stickers]}))
            output=agent.output_agent.apply_edits(candidate_result,previous,direct,generate_explanation=False)
            output['editCapabilities']=previous.get('editCapabilities',{})
        elif payload.stickers is not None and not payload.instruction.strip() and payload.visual is None:
            output = agent.output_agent.apply_edits(candidate_result, previous,
                [ChartEditCommand(operation="set_stickers", kind="STYLE_EDIT",
                    params={"items": [s.model_dump() for s in payload.stickers]})], generate_explanation=False)
            output["editCapabilities"] = previous.get("editCapabilities", {})
        elif payload.action == 'edit' and payload.edit_target=='table' and payload.reference_files:
            from table_reference_edit import edit_table_reference
            output=edit_table_reference(edit_agent,candidate_result,previous,payload.instruction,payload.visual.model_dump(),[m.model_dump() for m in payload.conversation])
        elif payload.action == "edit" and candidate_result.get('_derived') and (
                payload.derived_node_patch or is_calculation_edit(payload.instruction)):
            if payload.reference_files or (payload.visual and (payload.visual.marks or payload.visual.reference_files or
                                                                payload.visual.graph_image or payload.visual.marked_image)):
                raise ValueError('계산 조건 수정과 그림·첨부 수정을 나누어 요청해 주세요.')
            period = explicit_period(payload.instruction) or {}
            period_only = period_redraw(payload.instruction)
            revised = _runtime_for_derived(candidate_result).revise(candidate_result,
                instruction=payload.instruction if not payload.derived_node_patch and not (period_only and period_only.get('only_period')) else None,
                node_patch=payload.derived_node_patch, start=period.get('start'), end=period.get('end'))
            output = agent.output_agent._build(revised, _derived_output_spec(candidate_result, revised, previous), generate_explanation=False)
            candidate_result = revised
            plan = revised['derivedEvidence']['plan']
            candidate_sessions = [{**s, 'result': revised, 'period': {'start': _ui_period(plan['start_period'], plan['sources'][0]['frequency']), 'end': _ui_period(plan['end_period'], plan['sources'][0]['frequency'], end=True)},
                                   'table_name': plan['indicator_name'], 'frequency': plan['sources'][0]['frequency']} for s in session['sessions']]
        elif payload.action == "edit":
            period_update=revise_period(session,payload.instruction,agent.output_agent,agent.execute_resolution,_availability_from_plans,_api_period,agent.service)
            if period_update:
                candidate_result=period_update["result"]
                candidate_sessions=period_update["sessions"]
            baseline=period_update["output"] if period_update else previous
            if payload.stickers is not None:
                baseline=agent.output_agent.apply_edits(candidate_result, baseline,
                    [ChartEditCommand(operation="set_stickers", kind="STYLE_EDIT",
                        params={"items": [s.model_dump() for s in payload.stickers]})], generate_explanation=False)
                baseline["editCapabilities"] = previous.get("editCapabilities", {})
            if period_update and period_update["only_period"] and not payload.reference_files and not (payload.visual and any(m.text.strip() for m in payload.visual.marks)):
                output=baseline
            else:
                try:
                    used_edit_graph=True
                    def prepare_comparison(result, commands):
                        nonlocal candidate_result
                        if any(c.operation=='set_transform' and c.value=='year_over_year' for c in commands):
                            from chart_comparison_history import ensure_comparison_history
                            result=ensure_comparison_history(result,agent.service)
                        candidate_result=result
                        return result
                    edit_options={}
                    if payload.reference_files:
                        from chart_reference_data import reference_catalog, prepare_reference_data
                        catalog=reference_catalog(candidate_result,agent)
                        def prepare_reference(result,current,appearance):
                            nonlocal candidate_result,candidate_sessions,reference_data_update
                            prepared=prepare_reference_data(result,current,appearance,catalog,agent)
                            if prepared:
                                candidate_result,baseline,candidate_sessions=prepared
                                reference_data_update=True
                                return candidate_result,baseline
                            return None
                        edit_options={'prepare_result':prepare_comparison,'prepare_reference':prepare_reference,'reference_catalog':catalog}
                    output = edit_agent.edit(candidate_result, {**baseline, "editConversation": [m.model_dump() for m in payload.conversation[-40:]]}, payload.instruction,
                                         payload.visual.model_dump() if payload.visual else None, **edit_options)
                    if reference_data_update:
                        output['editChanges']=[*[f'참고 범례 {b["reference"]} → 실제 조회: {b["table"]} · {b["category"]}'
                            for b in candidate_result['execution']['referenceDataBinding']],*output.get('editChanges',[])]
                except ValueError as error:
                    if period_update and "변경된 내용이 없습니다" in str(error):
                        output=baseline;used_edit_graph=False
                    else: raise
        else:
            history = session.get(payload.action, [])
            if not history:
                raise ValueError("취소/다시 적용할 편집이 없습니다.")
            candidate_result=history[-1].get("result",session["result"])
            candidate_sessions=history[-1].get("sessions",session["sessions"])
            output = agent.output_agent._build(candidate_result, ChartSpec.model_validate(history[-1]["chartState"]), generate_explanation=False)
            output["editHistory"] = history[-1].get("editHistory", [])
            output["editCapabilities"] = previous.get("editCapabilities", {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with EDIT_LOCK:
        if session["output"] is not previous:
            raise HTTPException(status_code=409, detail="다른 수정이 먼저 적용되었습니다. 최신 그래프에서 다시 시도해 주세요.")
        snapshot = {"chartState": previous["chartState"], "editHistory": previous.get("editHistory", []), "result":session["result"], "sessions":session["sessions"]}
        if 'initial' not in session:session['initial']=copy.deepcopy(snapshot)
        if payload.action == "edit":
            session["undo"] = [*session.get("undo", []), snapshot][-20:]
            session["redo"] = []
        else:
            session[payload.action] = session[payload.action][:-1]
            opposite = "redo" if payload.action == "undo" else "undo"
            session[opposite] = [*session.get(opposite, []), snapshot][-20:]
        output.update(editVersion=revision+1, canUndo=bool(session.get("undo")), canRedo=bool(session.get("redo")))
        session["output"] = output
        session["result"] = candidate_result
        session["sessions"] = candidate_sessions
        session["executionRuns"]=[*session.get("executionRuns",[]),edit_trace(payload,output,used_edit_graph=used_edit_graph,period_update=bool(period_update))][-200:]
    return workspace_response(session,payload,period_update,reference_data_update)


def workspace_response(session,payload,period_update=None,reference_data_update=False):
    output={**session["output"], 'editCapabilities':{
        'model':getattr(getattr(edit_agent,'model',None),'model_name','configured-editor'),
        'supportsImages':getattr(getattr(edit_agent,'model',None),'supports_images',False)}}
    visualization = output.get("visualization") or {}
    chart = visualization.get("series") or []
    sessions = session["sessions"]
    merged = session["result"]
    plans = [plan for plan in (merged.get("api_plans") or [merged.get("api_plan")]) if plan]
    starts = sorted(str(item["period"]["start"]) for item in sessions)
    ends = sorted(str(item["period"]["end"]) for item in sessions)
    return {
        "status": "resolved",
        **({'analysisKind': 'derived', 'derivedEvidence': merged['derivedEvidence']} if merged.get('derivedEvidence') else {}),
        "query": " · ".join(str(item["query"]) for item in sessions),
        "interpretedQuery": " · ".join(str(item["table_name"]) for item in sessions),
        "summary": output.get("summary") or "",
        "periodSelection":sessions[0].get("periodSelection"),
        "period": {"start": starts[0], "end": ends[-1]},
        "frequency": " / ".join(dict.fromkeys(str(item["frequency"]) for item in sessions)),
        "chart": chart,
        "chartMode": visualization.get("layout"),
        "chartType": visualization.get("chartType"),
        "outputSpec": output,
        "editSessionId": payload.edit_session_id,
        "executionRuns":session.get("executionRuns",[]),
        "seriesCount": len(chart),
        "tables": [_table_card(str(plan["table_id"])) for plan in plans],
        "insights": output.get("editChanges") or ["편집 취소" if payload.action == "undo" else "편집 다시 적용"],
        "lineage": [
            {"id": "mcp", "title": "MCP 통계 서비스", "description": "참고 범례의 실제 통계 조회" if reference_data_update else "변경 기간의 원자료 재조회" if period_update else "기존 조회 데이터 사용", "status": "complete"},
            {"id": "edit", "title": "수정 Agent", "description": "텍스트·그림 표시를 검증 가능한 명령으로 해석", "status": "complete"},
            {"id": "output", "title": "출력 Agent", "description": "수정 명령 적용 및 그래프 렌더링", "status": "complete"},
        ],
        "warnings":[*(period_update["warnings"] if period_update else []),*output.get('warnings',[])],
    }


def _saved_workspace_path(identifier):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',identifier):raise HTTPException(status_code=422,detail='잘못된 저장 그래프 ID입니다.')
    return Path(__file__).resolve().parents[2]/'.venv'/'cache'/'chart_workspaces'/('user-'+str(account_auth.current_user()['id']))/(identifier+'.json.gz')


def _load_saved_workspace(identifier):
    import gzip
    from advanced_chart_spec import fingerprint
    path=_saved_workspace_path(identifier)
    if not path.exists():return None
    try:
        with gzip.open(path,'rb') as stream:raw=stream.read(64_000_001)
        if len(raw)>64_000_000:raise ValueError('저장 파일 크기 제한을 초과했습니다.')
        expected=account_auth.STORE.graph_content(account_auth.current_user()['id'],identifier)
        if expected and expected!=__import__('hashlib').sha256(raw).digest():raise ValueError('저장 파일 무결성 오류')
        saved=json.loads(raw)
        if saved.get('version')!=1:raise ValueError('저장 파일 버전을 확인해 주세요.')
        session=saved['session']
        if fingerprint(agent.output_agent._series(session['result'].get('execution',{}).get('rows',[])))!=saved['dataset_version']:raise ValueError('저장 자료의 무결성 검증에 실패했습니다.')
        old=session['output'];output=agent.output_agent._build(session['result'],ChartSpec.model_validate(old['chartState']),generate_explanation=False)
        output.update(editVersion=old.get('editVersion',0),canUndo=bool(session.get('undo')),canRedo=bool(session.get('redo')),editCapabilities=old.get('editCapabilities',{}),savedAt=saved.get('saved_at'),cachedSnapshot=True)
        session['output']=output
        if len(EDIT_SESSIONS)>=MAX_EDIT_SESSIONS:EDIT_SESSIONS.pop(next(iter(EDIT_SESSIONS)))
        EDIT_SESSIONS[identifier]=session
        return session
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(status_code=422,detail='저장 그래프를 불러오지 못했습니다. 파일 손상·버전·글꼴을 확인해 주세요.') from exc


@app.post('/api/workspace/{edit_session_id}/save')
@observed('analysis.save', 'span')
def save_workspace(edit_session_id: str,payload:ReportExportRequest):
    import gzip
    import re
    from datetime import datetime,timezone
    from advanced_chart_spec import fingerprint
    if payload.edit_session_id!=edit_session_id:raise HTTPException(status_code=422,detail='저장 대상이 일치하지 않습니다.')
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(edit_session_id)
        if not session:raise HTTPException(status_code=404,detail='편집 세션이 없습니다.')
        if payload.revision is not None and payload.revision!=session['output'].get('editVersion',0):raise HTTPException(status_code=409,detail='최신 그래프에서 다시 저장해 주세요.')
        snapshot=copy.deepcopy(session)
    def sanitize(value):
        if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items() if not re.search(r'api.?key|authorization|access.?token',str(k),re.I)}
        if isinstance(value,list):return [sanitize(v) for v in value]
        if isinstance(value,str):return re.sub(r'(?i)([?&](?:apiKey|api_key|access_token)=)[^&\s]+',r'\1[redacted]',value)
        return value
    saved={'version':1,'saved_at':datetime.now(timezone.utc).isoformat(),'dataset_version':fingerprint(agent.output_agent._series(snapshot['result'].get('execution',{}).get('rows',[]))),'session':sanitize(snapshot)}
    content=json.dumps(saved,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(content)>64_000_000:raise HTTPException(status_code=422,detail='저장 크기가 64MB를 초과합니다. 표시 데이터와 이력을 줄여 주세요.')
    path=_saved_workspace_path(edit_session_id);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with gzip.open(temp,'wb') as stream:stream.write(content)
    try:
        account_auth.STORE.save_graph(account_auth.current_user()['id'],edit_session_id,str(snapshot['output']['chartState'].get('title') or '통계 그래프'),__import__('hashlib').sha256(content).digest(),lambda:temp.replace(path))
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return {'status':'saved','saved_at':saved['saved_at'],'revision':snapshot['output'].get('editVersion',0)}


@app.get("/api/workspace/{edit_session_id}")
@observed('analysis.restore', 'span')
def read_workspace(edit_session_id: str):
    with EDIT_LOCK:
        session=EDIT_SESSIONS.get(edit_session_id) or _load_saved_workspace(edit_session_id)
        if not session:raise HTTPException(status_code=404,detail="편집 세션이 만료되었습니다. 같은 데이터를 다시 조회한 뒤 저장한 설정을 열어 주세요.")
        snapshot=copy.deepcopy(session)
    return workspace_response(snapshot,OutputEditRequest(edit_session_id=edit_session_id))


@app.get('/api/observability/{trace_id}')
def read_observation_trace(trace_id: str):
    value=get_observation_trace(trace_id,account_auth.current_user()['id'])
    if value is None:raise HTTPException(status_code=404,detail='실행 기록을 찾을 수 없습니다.')
    return value
