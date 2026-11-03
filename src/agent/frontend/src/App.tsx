import {WorkspaceLegendSwatch} from './WorkspaceLegendSwatch';
import RequestExecutionLog from './RequestExecutionLog';
import StatisticalQuestion from "./StatisticalQuestion";
import {publicationImageSize,reportChartSize} from './reportChartSize';
import {workspacePng,startFileDownload} from './workspaceDownload';
import DerivedEvidence from './DerivedEvidence';
import {derivedBudget} from './derivedDeadline';
import {periodTickUpdate} from './periodTicks';
import {withAnalysisDeadline,AnalysisTimeoutError} from './analysisDeadline';
import {analysisTimedOut} from './api/queryFailure';
import {tableCell,wantsReportTable} from './reportTable';
import {analysisEntries,type AccountAnalysis} from './analysisLibrary';
import {automaticOutput} from './automaticOutput';
import {displayedPeriod,emptyAvailablePeriod,groupSources,resultInsights,resultSourceLabel,titlePeriodMismatch} from './resultPresentation';
import QueryRefinementCard from './QueryRefinementCard';
import exploreExamples from './exploreExamples.json';
import ExecutionEvidence from './ExecutionEvidence';
import {panelGeometry,splitPlotAnnotations,resizeVisiblePlot} from './plotPanelLayout';
import {appendChat,readChat,writeChat,saveChat,allSavedChats as savedChats,removeSavedChat,editDescription,type SavedChat} from './graphChat';
import RecoveryPanel from './RecoveryPanel';
import DerivedSupplementPanel from './DerivedSupplementPanel';
import DerivedAvailabilityPanel from './DerivedAvailabilityPanel';
import {shouldShowDerivedSupplement} from './derivedAvailability';
import {accountKey,accountRequest} from './accountApi';
import {ownsHistory} from './accountScope';
import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import ChartSketchEditor from "./ChartSketchEditor";
import { EditConflictError, fetchCatalog, fetchWorkspace, submitOutput, submitOutputEdit, submitQuery } from "./api/client";
import {allowedCharts,chartLabels,chartDescriptions} from "./chartChoices";
import {initialQueryRequest} from "./querySubmission";
import type { CatalogResponse, CatalogTable, ChartEditInput, ChartSeries, ChartType, OutputRenderRequest, QueryRequest, QueryResponse } from "./api/types";

type IconName =
  | "logo" | "home" | "chat" | "chart" | "compare" | "lineage" | "search"
  | "send" | "menu" | "close" | "sparkle" | "database" | "download"
  | "share" | "check" | "chevron" | "bell" | "history" | "arrow";

const paths: Record<IconName, ReactNode> = {
  logo: <><path d="M4 18V11M10 18V6M16 18V9M22 18V3"/><path d="m3 13 6-4 6 2 7-7"/></>,
  home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v11h14V10M9 21v-7h6v7"/></>,
  chat: <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/>,
  chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19V2"/><path d="M2 19h21"/></>,
  compare: <><path d="M4 7h13M14 4l3 3-3 3M20 17H7M10 14l-3 3 3 3"/></>,
  lineage: <><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M7 5h10M6 7l5 10M18 7l-5 10"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/></>,
  send: <><path d="m3 3 19 9-19 9 4-9Z"/><path d="M7 12h15"/></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
  close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  sparkle: <><path d="m12 2 1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5Z"/><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7Z"/></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></>,
  download: <><path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 20h16"/></>,
  share: <><circle cx="18" cy="5" r="2"/><circle cx="6" cy="12" r="2"/><circle cx="18" cy="19" r="2"/><path d="m8 11 8-5M8 13l8 5"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  chevron: <path d="m9 6 6 6-6 6"/>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  arrow: <><path d="M5 12h14M14 7l5 5-5 5"/></>,
};

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function DateSelector({ value, min, max, onChange, label }: { value:string; min?:string; max?:string; onChange:(value:string)=>void; label:string }) {
  const today=new Date().toISOString().slice(0,10);
  const fallback=[max,today,min].find((item)=>item&&(!min||item>=min)&&(!max||item<=max))||min||max||today;
  const [open,setOpen]=useState(false);
  const [draft,setDraft]=useState(value||fallback);
  useEffect(()=>{if(value)setDraft(value)},[value]);
  const [year,month,day]=draft.split("-").map(Number);
  const minYear=Number((min||`${new Date().getFullYear()-30}-01-01`).slice(0,4));
  const maxYear=Number((max||`${new Date().getFullYear()+5}-12-31`).slice(0,4));
  const years=Array.from({length:Math.max(1,maxYear-minYear+1)},(_,index)=>minYear+index);
  const days=Array.from({length:new Date(year,month,0).getDate()},(_,index)=>index+1);
  const updateDraft=(nextYear:number,nextMonth:number,nextDay:number)=>{
    const safeDay=Math.min(nextDay,new Date(nextYear,nextMonth,0).getDate());
    setDraft(`${nextYear}-${String(nextMonth).padStart(2,"0")}-${String(safeDay).padStart(2,"0")}`);
  };
  const valid=(!min||draft>=min)&&(!max||draft<=max);
  return <div className="date-picker">
    <button type="button" className={`date-trigger ${value?"has-value":""}`} aria-expanded={open} aria-label={`${label} 선택`} onClick={()=>{setDraft(value||fallback);setOpen((current)=>!current)}}><Icon name="history" size={16}/><span>{value||"날짜 선택"}</span><Icon name="chevron" size={14}/></button>
    {open&&<div className="date-popover"><div className="date-popover-head"><div><small>{label}</small><strong>{draft.replaceAll("-",".")}</strong></div><button type="button" aria-label="날짜 선택 닫기" onClick={()=>setOpen(false)}><Icon name="close" size={15}/></button></div><div className="date-select-grid"><label>년<select value={year} onChange={(event)=>updateDraft(Number(event.target.value),month,day)}>{years.map((item)=><option key={item} value={item}>{item}년</option>)}</select></label><label>월<select value={month} onChange={(event)=>updateDraft(year,Number(event.target.value),day)}>{Array.from({length:12},(_,index)=>index+1).map((item)=><option key={item} value={item}>{item}월</option>)}</select></label><label>일<select value={day} onChange={(event)=>updateDraft(year,month,Number(event.target.value))}>{days.map((item)=><option key={item} value={item}>{item}일</option>)}</select></label></div>{min&&max&&<p>{min}부터 {max}까지 선택할 수 있습니다.</p>}<button type="button" className="date-confirm" disabled={!valid} onClick={()=>{onChange(draft);setOpen(false)}}>이 날짜로 선택</button></div>}
  </div>;
}

function ExpandableValues({ values, previewCount = 3 }: { values: string[]; previewCount?: number }) {
  const [expanded, setExpanded] = useState(false);
  const visibleValues = expanded ? values : values.slice(0, previewCount);
  if (!values.length) return <>-</>;
  return <div className="expandable-values">
    <span>{visibleValues.join(", ")}</span>
    {values.length > previewCount && <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      {expanded ? "접기" : `더 보기 (${values.length - previewCount}개)`}
    </button>}
  </div>;
}

function CatalogCard({ table, onQuery, loading }: { table: CatalogTable; onQuery: (query: string, dimensionValues: Record<string, string>) => void; loading: boolean }) {
  const [values, setValues] = useState<Record<number, string>>({});
  return <article className="catalog-card">
    <div><span>{table.organization}</span><span className="source-check"><Icon name="check" size={12}/></span></div>
    <h3>{table.name}</h3>
    <p className="catalog-table-id">통계표 ID {table.tableId}</p>
    <dl>
      <div><dt>수록 주기</dt><dd><strong>{table.frequencyLabel}</strong> <small>({table.frequency})</small></dd></div>
      <div><dt>제공 기간</dt><dd>{table.periodStart} – {table.periodEnd}</dd></div>
      <div><dt>대표 단위</dt><dd>{table.unitScale || "-"}</dd></div>
      <div><dt>수치 단위</dt><dd><ExpandableValues values={table.units}/></dd></div>
      <div><dt>통계 항목</dt><dd><ExpandableValues values={table.items}/></dd></div>
      <div><dt>분류 정보</dt><dd>{table.dimensions.length ? table.dimensions.map((dimension, index) =>
        <section className="dimension-detail" key={`${dimension.name}-${index}`}>
          <b>{dimension.name}</b><span> · {dimension.count}개</span>
          <ExpandableValues values={dimension.values}/>
        </section>) : "-"}</dd></div>
    </dl>
    <section className="catalog-query-controls">
      <h4>이 통계표 조회</h4>
      {table.dimensions.map((dimension, index) => <label key={index}>
        {dimension.name}
        <select aria-label={`${dimension.name} 선택`} value={values[index] || ""} onChange={(event) => setValues((prior) => ({...prior, [index]: event.target.value}))}>
          <option value="">대표값: {dimension.valueOptions.find((v) => v.id === dimension.defaultValueId)?.name || "기본 분류"}</option>
          {dimension.valueOptions.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
        </select>
      </label>)}
      <button type="button" disabled={loading} onClick={() => onQuery(table.tableId, Object.fromEntries(table.dimensions.map((dimension, index) => [dimension.apiParam, values[index] || dimension.defaultValueId])))}>이 표로 기간 선택 · 자료 조회</button>
      <p>기간을 지정하고 자료를 받은 뒤 그래프 종류를 선택합니다.</p>
    </section>
  </article>;
}

function DataCatalog({ onQuery, loading }: { onQuery: (query: string, dimensionValues: Record<string, string>) => void; loading: boolean }) {
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState("");
  const [major, setMajor] = useState(""); const [middle, setMiddle] = useState("");
  const [selected, setSelected] = useState<CatalogTable | null>(null);
  useEffect(() => { fetchCatalog().then((data) => { setCatalog(data); setMajor(data.categories[0]?.name || ""); }).catch((e) => setError(e instanceof Error ? e.message : "카탈로그를 불러오지 못했습니다.")); }, []);
  const majors = catalog?.categories || [];
  const majorNode = majors.find((x) => x.name === major) || majors[0];
  const middles = majorNode?.children || [];
  const middleNode = middles.find((x) => x.name === middle) || middles[0];
  useEffect(() => { setMiddle(""); setSelected(null); }, [major]);
  useEffect(() => { setSelected(null); }, [middle]);
  return <main className="catalog-page" id="data-catalog" tabIndex={-1}>
    <section className="catalog-hero"><span className="eyebrow">DATA LINEAGE CATALOG</span><h1>조회 가능한 데이터 계보</h1><p>StatBridge에서 조회할 수 있는 통계표를 대분류·중분류·소분류 순서로 탐색합니다.</p><div><strong>{catalog?.total ?? "-"}</strong><span>조회 가능 통계표</span><small>{catalog?.source || "불러오는 중"}</small></div></section>
    {error && <div className="error-banner">{error}</div>}
    {catalog && <section className="catalog-browser panel">
      <div className="catalog-column"><h2><span>1</span> 대분류</h2>{majors.map((x) => <button className={x.name === major ? "selected" : ""} key={x.name} onClick={() => setMajor(x.name)}><strong>{x.name}</strong><small>{x.count}개</small><Icon name="chevron" size={15}/></button>)}</div>
      <div className="catalog-column"><h2><span>2</span> 중분류</h2>{middles.map((x) => <button className={x.name === (middleNode?.name || "") ? "selected" : ""} key={x.name} onClick={() => setMiddle(x.name)}><strong>{x.name}</strong><small>{x.count}개</small><Icon name="chevron" size={15}/></button>)}</div>
      <div className="catalog-column catalog-small"><h2><span>3</span> 소분류</h2>{middleNode?.children.map((x) => <button className={x.tableId === selected?.tableId ? "selected" : ""} key={x.tableId} onClick={() => setSelected(x)}><strong>{x.name}</strong><Icon name="chevron" size={15}/></button>)}</div>
      <div className="catalog-detail"><h2>통계표 정보 카드</h2>{selected ? <CatalogCard key={selected.tableId} table={selected} onQuery={onQuery} loading={loading}/> : <div className="catalog-empty"><Icon name="database" size={35}/><strong>소분류 통계표를 선택하세요.</strong><p>주기·기간·수치 단위·통계 항목과 분류 내용을 카드로 보여드립니다.</p></div>}</div>
    </section>}
  </main>;
}

function Header({ view, goHome, selectView, busy }: {
  view: "home" | "lineage"; goHome: () => void; selectView: (view: "home" | "lineage") => void; busy?: boolean;
}) {
  const focusQuery = () => {
    selectView("home");
    requestAnimationFrame(() => {
      document.getElementById("statistics-query")?.focus();
      document.getElementById("statistics-query")?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  };
  const showGuide = () => {
    selectView("home");
    requestAnimationFrame(() => {
      document.getElementById("usage-guide")?.focus({ preventScroll: true });
      document.getElementById("usage-guide")?.scrollIntoView({ behavior: "smooth" });
    });
  };
  return <header className="institution-header">
    <a className="skip-link" href={view === "lineage" ? "#data-catalog" : "#main-content"}>본문 바로가기</a>
    <div className="utility-bar"><div className="utility-inner">
      <span>경제통계 분석 워크스페이스</span>
      <div><a href="https://www.bok.or.kr/portal/main/main.do" target="_blank" rel="noreferrer">한국은행 <span className="sr-only">(새 창)</span><span aria-hidden="true">↗</span></a><a href="https://kosis.kr" target="_blank" rel="noreferrer">KOSIS <span className="sr-only">(새 창)</span><span aria-hidden="true">↗</span></a><span className="workspace-label">STATBRIDGE LOCAL</span></div>
    </div></div>
    <div className="institution-nav">
      <button type="button" className="institution-brand" onClick={() => { goHome(); }} aria-label="StatBridge 홈으로 이동"><span className="institution-mark"><Icon name="logo" size={28}/></span><span><strong>StatBridge</strong><small>ECONOMIC DATA & INSIGHT</small></span></button>
      <nav id="primary-navigation" className="institution-links" aria-label="주 메뉴">
        <button type="button" aria-current={view === "home" ? "page" : undefined} onClick={() => { goHome(); }}>분석 홈</button>
        <button type="button" aria-current={view === "lineage" ? "page" : undefined} onClick={() => { selectView("lineage"); }}>통계 탐색 · 계보</button>
        <button type="button" onClick={showGuide}>이용 안내</button>
      </nav>
      <div className="institution-actions"><nav className="institution-chat-actions" aria-label="그래프 대화 관리"><button type="button" disabled={busy} aria-label="새 채팅 시작" onClick={()=>window.dispatchEvent(new Event('statbridge:new-chat'))}><Icon name="chat" size={16}/><span>새 채팅</span></button></nav><button type="button" className="institution-search" onClick={focusQuery} aria-label="통계 질문 입력으로 이동"><Icon name="search" size={24}/></button></div>
    </div>
  </header>;
}

function Hero({ query, setQuery, submit, loading }: { query: string; setQuery: (v: string) => void; submit: (e: FormEvent) => void; loading: boolean }) {
  return <section className="hero" aria-labelledby="hero-title">
    <div className="hero-content">
      <span className="hero-label"><span/> ECONOMIC STATISTICS WORKSPACE</span>
      <h1 id="hero-title">경제를 읽는 데이터,<br/><em>근거를 잇는 분석.</em></h1>
      <p>한국은행 통계를 자연어로 탐색하고 비교하세요.<br className="desktop-break"/> 통계표 선택부터 시각화까지, 수치와 출처를 함께 확인합니다.</p>
      <form className="ask-box" onSubmit={submit}>
        <Icon name="search" size={21}/>
        <input id="statistics-query" aria-label="통계 질문" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="예: 대출금리와 생산자물가를 비교해줘" />
        <button type="submit" disabled={loading || !query.trim()}>{loading ? <span className="spinner"/> : <Icon name="send" size={18}/>}<span>분석하기</span></button>
      </form>
    </div>
    <div className="economic-visual" aria-hidden="true">
      <svg viewBox="0 0 520 440" fill="none"><defs><linearGradient id="column-light" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#73c2e7" stopOpacity=".4"/><stop offset="1" stopColor="#73c2e7" stopOpacity=".02"/></linearGradient></defs>
        <circle cx="300" cy="220" r="182" stroke="#a4ccdf" strokeOpacity=".15"/><circle cx="300" cy="220" r="146" stroke="#a4ccdf" strokeOpacity=".12"/>
        <path d="M65 354H492M65 286H492M65 218H492M65 150H492" stroke="#a4ccdf" strokeOpacity=".12" strokeDasharray="3 8"/>
        {[112,185,258,331,404].map((x,index)=><g key={x}><path d={`M${x} ${260-index*30}h46v${94+index*30}h-46z`} fill="url(#column-light)" stroke="#8dd0ed" strokeOpacity=".22"/><path d={`M${x+9} ${273-index*30}v${67+index*30}m14 ${-67-index*30}v${67+index*30}m14 ${-67-index*30}v${67+index*30}`} stroke="#a4ccdf" strokeOpacity=".12"/></g>)}
        <path d="m90 278 72-38 72 17 73-83 72 21 75-83" stroke="#72c8ea" strokeWidth="2.5"/>
        {[[90,278],[162,240],[234,257],[307,174],[379,195],[454,112]].map(([x,y])=><circle key={x} cx={x} cy={y} r="4" fill="#b5e7fa"/>)}
        <path d="M88 374H476M109 390H457" stroke="#a4ccdf" strokeOpacity=".25"/>
      </svg>
      <div className="visual-caption"><span>FROM DATA TO EVIDENCE</span><strong>숫자 너머의 맥락을 연결합니다.</strong></div>
    </div>
    <div className="hero-bottom"><span>EXPLORE · COMPARE · TRACE</span><span>한국은행 통계 / KOSIS 자료 조회</span></div>
  </section>;
}

function UsageGuide() {
  return <section className="usage-guide" id="usage-guide" tabIndex={-1} aria-labelledby="guide-title"><div className="guide-heading"><span className="eyebrow">RESEARCH WORKFLOW</span><h2 id="guide-title">질문에서 분석까지</h2><p>필요한 통계를 찾고, 조건을 확인하고, 근거를 남깁니다.</p></div><ol>
    <li><span>01</span><h3>통계 질문하기</h3><p>알고 싶은 지표와 기간을 입력하거나 통계 탐색에서 표를 선택하세요.</p></li>
    <li><span>02</span><h3>조회 조건 확인하기</h3><p>통계표, 항목, 주기와 기간을 확인해 분석 범위를 정하세요.</p></li>
    <li><span>03</span><h3>결과와 출처 살펴보기</h3><p>그래프와 관측값을 비교하고, 출처를 확인한 뒤 그래프를 편집하세요.</p></li>
  </ol></section>;
}

function ChartRenderer({ series, chartType = "line", xAxisLabel, yAxisLabel }: { series: ChartSeries[]; chartType?: ChartType; xAxisLabel?:string|null; yAxisLabel?:string|null }) {
  if (!series.length) return <div className="empty-chart">선택한 기간에 표시할 수치가 없습니다.</div>;
  const w = 700, h = 250, px = 58, py = 26;
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
  const values = series.flatMap((s) => s.points.map((p) => p.value));
  const minValue = Math.min(...values), maxValue = Math.max(...values);
  const padding = Math.max((maxValue - minValue) * .12, Math.abs(maxValue) * .03, 1);
  const yMin = minValue - padding, yMax = maxValue + padding;
  const x = (date: string) => px + (Math.max(0, dates.indexOf(date)) * (w - px * 2)) / Math.max(1, dates.length - 1);
  const y = (v: number) => h - py - ((v - yMin) / Math.max(1e-9, yMax - yMin)) * (h - py * 2);
  const path = (item: ChartSeries) => item.points.map((d, i) => `${i ? "L" : "M"}${x(d.date)},${y(d.value)}`).join(" ");
  const ticks = [0, .25, .5, .75, 1].map((ratio) => yMin + (yMax - yMin) * ratio);
  return <div className="chart-wrap">
    <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${series.map((s) => s.label).join(", ")} 시계열 그래프`}>
      {ticks.map((v) => <g key={v}><line className="grid" x1={px} y1={y(v)} x2={w-px} y2={y(v)}/><text className="axis-label" x={px-8} y={y(v)+4}>{v.toLocaleString(undefined,{maximumFractionDigits:1})}</text></g>)}
      {series.map((item) => <g key={item.id}>{chartType==="bar" ? item.points.map((p)=><rect key={p.date} x={x(p.date)-5} y={y(p.value)} width="10" height={Math.max(1,h-py-y(p.value))} fill={item.color}><title>{item.label}: {p.value.toLocaleString()} {item.unit}</title></rect>) : <><path className="line" style={{stroke:item.color,fill:chartType==="area"?`${item.color}22`:"none"}} d={path(item)}/>{item.points.map((p) => <circle key={p.date} className="dot" style={{fill:item.color}} cx={x(p.date)} cy={y(p.value)} r={chartType==="scatter"?"5":"3.5"}><title>{item.label}: {p.value.toLocaleString()} {item.unit}</title></circle>)}</>}</g>)}
      {dates.map((date, i) => (i % Math.max(1, Math.ceil(dates.length / 7)) === 0 || i === dates.length - 1) && <text key={date} className="x-label" x={x(date)} y={h-5}>{date}</text>)}
      {xAxisLabel&&<text className="x-label" x={w/2} y={h-1}>{xAxisLabel}</text>}
      {yAxisLabel&&<text className="axis-label" transform={`translate(12 ${h/2}) rotate(-90)`}>{yAxisLabel}</text>}
    </svg>
  </div>;
}

export function PlotlyRenderer({ figure,heading }: { figure: { data: Array<Record<string, unknown>>; layout: Record<string, unknown> };heading?:{title:string;subtitle:string} }) {
  const geometry=panelGeometry(figure);
  const legendAtBottom=Number((figure.layout.legend as {y?:number}|undefined)?.y??1)<0;
  const publication=(figure.layout.meta as {publicationYoy?:boolean}|undefined)?.publicationYoy===true;
  const publicationSize=publicationImageSize(figure.layout);
  const captions=splitPlotAnnotations(figure);
  const host = useRef<HTMLDivElement>(null);
  const renderRevision=useRef(0);
  const queue=useRef<Promise<unknown>>(Promise.resolve());
  const [renderError,setRenderError] = useState("");
  useEffect(() => {
    if (!host.current) return;
    const element = host.current;
    const version=++renderRevision.current;
    element.dataset.plotReady='false';
    let active=true;
    let rendered=false;
    let unbind=()=>{};
    setRenderError("");
    const copy=structuredClone(figure);
    delete copy.layout.width;delete copy.layout.height;copy.layout.autosize=true;
    if(heading){
      copy.layout.annotations=captions.plot;
      copy.layout.title={text:''};copy.layout.showlegend=false;
      if(geometry.domainChart)copy.layout.uniformtext={minsize:10,mode:'hide'};
      copy.layout.margin={...(copy.layout.margin as Record<string,number>),t:geometry.titleSpace,b:publication?45:Math.max(geometry.domainChart?90:120,...captions.plot.filter(a=>String(a.name||'').startsWith('statbridge-axis-title:x')).map(a=>Math.abs(Number(a.yshift)||0)+40))};
    }
    const select=(kind:string,label?:string,id?:string)=>element.dispatchEvent(new CustomEvent('statbridge:graph-select',{bubbles:true,detail:{kind,label,id}}));
    const selectText=(event:MouseEvent)=>{const node=event.target as Element;if(node.closest('.gtitle'))select('title');else if(node.closest('.xtick,.xtitle'))select('x');else if(node.closest('.ytick,.ytitle'))select('y');else if(node.closest('.annotation')){const index=Number(node.closest('.annotation')?.getAttribute('data-index'));const annotation=(copy.layout.annotations as Array<{name?:string}>|undefined)?.[index];if(annotation?.name?.startsWith('statbridge-axis-title:'))select(annotation.name.includes('xaxis')?'x':annotation.name.endsWith('yaxis2')?'y2':'y');else if(annotation?.name?.startsWith('statbridge-latest:'))select('series',annotation.name.slice('statbridge-latest:'.length));else if(annotation?.name)select('note',undefined,annotation.name);}};
    element.addEventListener('click',selectText);
    queue.current=queue.current.catch(()=>{}).then(async()=>{if(!active)return;await Plotly.react(element, copy.data, copy.layout, { responsive: true, displaylogo: false });if(!active)return;const visible=await resizeVisiblePlot(element,target=>Plotly.Plots.resize(target));if(!active)return;
      rendered=true;if(visible)await Plotly.relayout(element,periodTickUpdate(figure,element.getBoundingClientRect().width));if(!active)return;
      element.dataset.plotVersion=String(version);element.dataset.plotReady='true';
      const events=element as unknown as HTMLElement & {on:(name:string,fn:(event:any)=>unknown)=>void;removeListener:(name:string,fn:(event:any)=>unknown)=>void};
      const click=(event:any)=>{const trace=event.points?.[0]?.data;const label=trace?.meta?.seriesLabel||trace?.customdata?.[0]?.[0]||trace?.name;if(label)select('series',label);};
      const annotation=(event:any)=>{if(event.annotation?.name?.startsWith('statbridge-latest:'))select('series',event.annotation.name.slice('statbridge-latest:'.length));else if(event.annotation?.name)select('note',undefined,event.annotation.name);};
      const legend=(event:any)=>{const trace=copy.data[event.curveNumber];select('series',String((trace.meta as {seriesLabel?:string})?.seriesLabel||trace.name||''));return false;};
      events.on('plotly_click',click);events.on('plotly_clickannotation',annotation);events.on('plotly_legendclick',legend);
      unbind=()=>{events.removeListener('plotly_click',click);events.removeListener('plotly_clickannotation',annotation);events.removeListener('plotly_legendclick',legend);};
    }).catch(()=>{
      if(active)setRenderError("그래프 화면을 그리지 못했습니다. 새로고침 후 다시 확인해 주세요.");
    });
    const observer=new ResizeObserver(()=>{if(active&&rendered)queue.current=queue.current.catch(()=>{}).then(async()=>{if(active&&await resizeVisiblePlot(element,target=>Plotly.Plots.resize(target))&&active)await Plotly.relayout(element,periodTickUpdate(figure,element.getBoundingClientRect().width));}).catch(()=>{if(active)setRenderError('그래프 크기를 조정하지 못했습니다. 창 크기를 바꾸거나 다시 확인해 주세요.');});});
    observer.observe(element);
    return () => {active=false;observer.disconnect();unbind();element.removeEventListener('click',selectText);};
  }, [figure,heading?.title,heading?.subtitle]);
  useEffect(()=>{const element=host.current;return()=>{void queue.current.finally(()=>{if(element&&!element.isConnected)Plotly.purge(element);});};},[]);
  const pick=(kind:string,label?:string)=>host.current?.dispatchEvent(new CustomEvent('statbridge:graph-select',{bubbles:true,detail:{kind,label}}));
  const names=new Set<string>();
  const legendData:Array<Record<string,unknown>>=geometry.domainChart?figure.data.flatMap(t=>((t.labels||[]) as unknown[]).map((label,i)=>({name:String(label),line:{color:((t.marker as {colors?:string[]}|undefined)?.colors||[])[i]},meta:{seriesLabel:String(((t.customdata||[]) as unknown[])[i]||label)}}))):figure.data;
  const legends=figure.layout.showlegend===false?[]:legendData.filter(t=>{const name=String(t.name||'');if(t.showlegend===false||!name||names.has(name))return false;names.add(name);return true;});
  const plain=(value:string)=>new DOMParser().parseFromString(value,'text/html').body.textContent||'';
  const titleStyle=figure.layout.title as {font?:{size?:number;color?:string;family?:string};x?:number}|undefined;
  const plot=<div ref={host} className="plotly-chart" style={heading?undefined:{height:Math.max(typeof figure.layout.height==='number'?figure.layout.height:580,geometry.minHeight)}} aria-label="통계 그래프"/>;
  return <>{renderError&&<p role="alert" className="error-banner">{renderError}</p>}{heading?<div className="workspace-plot-frame" style={publicationSize?{width:'100%',minWidth:400,maxWidth:650,marginInline:'auto',aspectRatio:`${publicationSize.width}/${publicationSize.height}`,height:'auto',minHeight:0}:geometry.minWidth?{minWidth:geometry.minWidth}:undefined}><header className="workspace-plot-heading" onClick={()=>pick('title')}><h4 style={{fontWeight:publication?700:undefined,fontSize:titleStyle?.font?.size??18,color:titleStyle?.font?.color,fontFamily:titleStyle?.font?.family,textAlign:(titleStyle?.x??.5)<.25?'left':(titleStyle?.x??.5)>.75?'right':'center'}}>{heading.title}</h4>{heading.subtitle&&<p>{heading.subtitle}</p>}</header><div className="workspace-plot-legend" data-legend-position={legendAtBottom?'bottom':'top'} style={{order:legendAtBottom?2:0,...(publication&&!legends.length?{display:'none'}:{})}}>{legends.map((trace,i)=><button type="button" key={i} onClick={()=>pick('series',String((trace.meta as {seriesLabel?:string})?.seriesLabel||plain(String(trace.name))))}><WorkspaceLegendSwatch trace={trace}/>{plain(String(trace.name))}</button>)}</div><div className={`workspace-plot-body ${geometry.multi?'multi-panel':geometry.expanded?'expanded-plot':''}`} style={{order:1,...(publicationSize?{height:'auto',minHeight:0,flex:'1 1 0'}:geometry.multi||geometry.expanded?{height:geometry.minHeight,minHeight:geometry.minHeight,flex:'none'}:{})}}>{plot}</div>{captions.footer.length>0&&<footer className="workspace-plot-footer" style={{order:3}} aria-label="그래프 출처와 설명">{captions.footer.map((a,i)=><p key={i}>{plain(String(a.text||'').replace(/<br\s*\/?\s*>/gi,'\n'))}</p>)}</footer>}</div>:plot}</>;

}

function LayoutChoice({ mode, setMode, reason }: { mode: "combined"|"separate"; setMode: (v:"combined"|"separate")=>void; reason?:string }) {
  return <div className="setup-block"><strong>그래프 표시 방식</strong><div className="clarification-options">{!reason&&<button type="button" className={mode==="combined"&&!reason?"selected":""} onClick={()=>setMode("combined")}><span className="multi-check">{mode==="combined"&&!reason&&<Icon name="check" size={13}/>}</span>여러 계열을 한 그래프에</button>}<button type="button" className={mode==="separate"?"selected":""} onClick={()=>setMode("separate")}><span className="multi-check">{mode==="separate"&&<Icon name="check" size={13}/>}</span>계열별 그래프 여러 개</button></div>{reason&&<div className="chart-blocker" role="status"><strong>한 그래프로 합칠 수 없는 이유</strong><p>{reason}</p><p>현재는 계열별 그래프로 표시합니다. 산점도는 각 지표를 서로 다른 X·Y축에 놓는 별도 방식입니다.</p></div>}</div>;
}

function ChartTypeChoice({ value, onChange, available, loading }: { value: "auto"|ChartType; onChange:(value:"auto"|ChartType)=>void;available:ChartType[];loading:boolean }) {
  const choices:Array<'auto'|ChartType>=available.length?['auto',...available]:[];
  return <div className="setup-block"><strong>현재 데이터로 그릴 수 있는 그래프</strong><p>조회된 지표 수, 공통 시점, 단위와 값의 조건을 확인했습니다.</p><div className="clarification-options">{choices.map(type=><button type="button" disabled={loading} key={type} className={value===type?"selected":""} onClick={()=>onChange(type)}><strong>{type==="auto"?"자동 선택":chartLabels[type]}</strong><small>{type==="auto"?'현재 데이터에 맞는 기본 그래프':chartDescriptions[type]}</small></button>)}</div>{!choices.length&&<p role="alert">그릴 수 있는 그래프가 없습니다. 데이터 조회 결과를 확인해 주세요.</p>}</div>;
}

function UnavailablePeriod({result}:{result:QueryResponse}) {
 return <div className="period-conflict" role="alert"><strong>선택한 통계표에 공통으로 조회할 수 있는 기간이 없습니다.</strong><p>제공 기간 정보가 서로 맞지 않아 날짜 선택을 중단했습니다. 질문에서 비교할 통계표를 다시 선택하거나 각 표를 따로 조회해 주세요.</p><ul>{groupSources(result.tables).map(table=><li key={table.tableId}>{table.name} · {table.tableId}</li>)}</ul></div>;
}

function PeriodConfirmation({result}:{result:QueryResponse}) {
  const period=result.periodSelection;
  return period?.applied ? <p className="period-availability" role="status">{period.source==="text"?"텍스트에서 확인한 기간":"선택한 기간"}: {period.applied.start} ~ {period.applied.end} · 원자료 주기 {result.frequency}</p> : null;
}

function OutputConfigPanel({ result, loading, submit }: { result: QueryResponse; loading:boolean; submit:(value:{chartType:"auto"|ChartType;mode:"combined"|"separate";title:string;showLegend:boolean;xAxisLabel:string;yAxisLabel:string;naturalLanguage:string})=>void }) {
  const [chartType,setChartType]=useState<"auto"|ChartType>("auto");
  const available=allowedCharts(result.outputOptions?.supportedChartTypes);
  const availableKey=available.join(',');
  useEffect(()=>setChartType('auto'),[result.outputSessionId,result.outputSessionIds,availableKey]);
  const [mode,setMode]=useState<"combined"|"separate">("combined");
  const supportsPanels=chartType==='auto'||['line','bar','stacked_bar','area','histogram','box'].includes(chartType);
  const combinedReason=supportsPanels?result.outputOptions?.combinedLayoutReason:undefined;
  useEffect(()=>{if(combinedReason)setMode('separate')},[combinedReason,result.outputSessionId,result.outputSessionIds]);
  const naturalLanguage=result.query;
  const [title,setTitle]=useState(""); const [showLegend,setShowLegend]=useState(true);
  const [xAxisLabel,setXAxisLabel]=useState(""); const [yAxisLabel,setYAxisLabel]=useState("");
  return <section className="clarification-section" id="analysis-result"><div className="clarification-card">
    <span className="eyebrow">OUTPUT AGENT ORDER</span>
    <PeriodConfirmation result={result}/>
    {result.warnings?.map((message,index)=><p className="error-banner" role="alert" key={index}>{message}</p>)}
    <div className="clarification-title"><span><Icon name="chart" size={19}/></span><div><h2>조회한 데이터를 어떤 그래프로 보여드릴까요?</h2><p>{result.seriesCount||0}개 계열의 조회가 끝났습니다. 지금 선택해도 데이터를 다시 조회하지 않습니다.</p></div></div>
    <ChartTypeChoice value={chartType} onChange={setChartType} available={available} loading={loading}/>
    {(result.seriesCount||0)>1&&supportsPanels&&<LayoutChoice mode={mode} setMode={setMode} reason={combinedReason}/>}
    <div className="setup-block"><strong>그래프 편집</strong><div className="period-form"><label>제목<input value={title} placeholder="자동 제목" onChange={(e)=>setTitle(e.target.value)}/></label><label>X축 이름<input value={xAxisLabel} onChange={(e)=>setXAxisLabel(e.target.value)}/></label><label>Y축 이름<input value={yAxisLabel} onChange={(e)=>setYAxisLabel(e.target.value)}/></label></div><label><input type="checkbox" checked={showLegend} onChange={(e)=>setShowLegend(e.target.checked)}/> 범례 표시</label></div>
    <div className="clarification-submit"><span>선택한 설정으로 그래프를 만듭니다.</span><button disabled={loading||!available.length||(!!combinedReason&&mode==='combined')||(chartType!=='auto'&&!available.includes(chartType))} onClick={()=>submit({chartType,mode:supportsPanels?mode:'combined',title,showLegend,xAxisLabel,yAxisLabel,naturalLanguage})}>그래프 생성 <Icon name="arrow" size={15}/></button></div>
  </div></section>;
}

function PeriodPanel({ result, loading, submit }: { result: QueryResponse; loading: boolean; submit: (start: string, end: string) => void }) {
  const known=result.periodSelection?.applied;
  const [start, setStart] = useState(known?.start||""); const [end, setEnd] = useState(known?.end||"");
  const minDate=result.availablePeriod?.min||undefined, maxDate=result.availablePeriod?.max||undefined;
  const invalid=emptyAvailablePeriod(result.availablePeriod)||Boolean(start&&end&&(start>end||(minDate&&start<minDate)||(maxDate&&end>maxDate)));
  return <section className="clarification-section" id="analysis-result"><div className="clarification-card">
    <span className="eyebrow">CHART PERIOD</span><div className="clarification-title"><span><Icon name="chart" size={19}/></span><div><h2>{known?"텍스트에서 확인한 기간으로 조회합니다.":"그래프로 볼 정확한 기간을 입력해 주세요."}</h2><p>{result.interpretedQuery} · 원자료 주기 {result.frequency}</p></div></div>
    <p>{result.summary}</p>{result.warnings?.map((warning,index)=><p role="status" key={index}>{warning}</p>)}
    {emptyAvailablePeriod(result.availablePeriod)?<UnavailablePeriod result={result}/>:known?<PeriodConfirmation result={result}/>:<div className="period-form"><label>시작일<DateSelector label="시작일" value={start} min={minDate} max={end&&(!maxDate||end<maxDate)?end:maxDate} onChange={setStart}/></label><span>→</span><label>종료일<DateSelector label="종료일" value={end} min={start&&(!minDate||start>minDate)?start:minDate} max={maxDate} onChange={setEnd}/></label></div>}{!emptyAvailablePeriod(result.availablePeriod)&&minDate&&maxDate&&<p className="period-availability">선택 가능 기간: {minDate} ~ {maxDate}</p>}<div className="clarification-submit"><span>{invalid?"원자료 제공 기간 안에서 선택해 주세요.":"선택한 조건으로 통계 자료를 조회합니다."}</span><button disabled={loading || !start || !end || invalid} onClick={() => submit(start,end)}>데이터 조회 <Icon name="arrow" size={15}/></button></div>
  </div></section>;
}


function ClarificationPanel({ result, loading, choose, advance, onResult }: { result: QueryResponse; loading: boolean; advance:(payload:QueryRequest)=>Promise<QueryResponse>; onResult:(next:QueryResponse)=>void; choose: (values: Array<{ clarification_id: string; values: string[] }>,start:string,end:string) => void }) {
  const [stepResult,setStepResult]=useState(result);
  const [selected,setSelected]=useState<Record<string,string[]>>({});
  const [completed,setCompleted]=useState<Array<{id:string;question:string;label:string}>>([]);
  const [stepLoading,setStepLoading]=useState(false);
  const [stepError,setStepError]=useState("");
  const [start,setStart]=useState(result.periodSelection?.applied?.start||""); const [end,setEnd]=useState(result.periodSelection?.applied?.end||"");
  const activeGroup=stepResult.status==="need_clarification" ? stepResult.clarification : undefined;
  const periodReady=stepResult.status==="need_period";
  const minDate=periodReady ? stepResult.availablePeriod?.min||undefined : undefined;
  const maxDate=periodReady ? stepResult.availablePeriod?.max||undefined : undefined;
  const datesComplete=Boolean(start&&end);
  const invalid=emptyAvailablePeriod(stepResult.availablePeriod)||Boolean(datesComplete&&(start>end||(minDate&&start<minDate)||(maxDate&&end>maxDate)));
  if (result.status !== "need_clarification" || !result.clarification) return null;
  const selectStep=async(option:{label:string;value:string})=>{
    if(!activeGroup||stepLoading)return;
    const nextSelected={...selected,[activeGroup.id]:[option.value]};
    setSelected(nextSelected); setStepLoading(true); setStepError("");
    try{
      const next=await advance({query:result.query,state:stepResult.state,clarification:{clarification_id:activeGroup.id,value:option.value},execute:true});
      if(["no_match","request_failed","data_unavailable"].includes(next.status||""))throw new Error("이 선택과 연결된 지원 통계표가 없습니다.");
      if(next.status==="resolved"||next.status==="need_output_config"){onResult(next);return;}
      setCompleted((items)=>[...items,{id:activeGroup.id,question:activeGroup.question,label:option.label}]);
      setStepResult(next); setStart(next.periodSelection?.applied?.start||""); setEnd(next.periodSelection?.applied?.end||"");
    }catch(error){
      setSelected(selected); setStepError(error instanceof Error?error.message:"다음 선택지를 불러오지 못했습니다.");
    }finally{setStepLoading(false);}
  };
  const resetSteps=()=>{setStepResult(result);setSelected({});setCompleted([]);setStart("");setEnd("");setStepError("");};
  return <section className="clarification-section" id="analysis-result">
    <div className="clarification-card">
      <span className="eyebrow">AGENT CLARIFICATION</span>
      <div className="clarification-title"><span><Icon name="chat" size={19}/></span><div><h2>질문에 맞는 조건을 순서대로 선택해 주세요.</h2><p>앞 단계의 선택에 연결되는 항목과 지원 기간만 다음 단계에 표시합니다.</p></div></div>
      <div className="clarification-progress">{completed.map((step,index)=><div className="completed-step" key={step.id}><span>{index+1}</span><div><small>{step.question}</small><strong>{step.label}</strong></div></div>)}{completed.length>0&&<button type="button" onClick={resetSteps}>처음부터 다시 선택</button>}</div>
      {activeGroup&&<div className="clarification-groups"><fieldset><legend><span className="step-number">{completed.length+1}</span>{activeGroup.question}</legend><div className="clarification-options">{activeGroup.options.map((option)=>{const active=(selected[activeGroup.id]||[]).includes(option.value);return <button type="button" aria-pressed={active} className={active?"selected":""} key={option.value} disabled={loading||stepLoading} onClick={()=>selectStep(option)}><span className="multi-check">{active&&<Icon name="check" size={13}/>}</span>{option.label}</button>})}</div>{stepLoading&&<p className="step-loading"><span className="spinner"/> 관련 통계표와 다음 질문을 확인하고 있습니다.</p>}</fieldset></div>}
      {stepError&&<div className="error-banner">{stepError}<p>현재 선택은 유지됩니다. 다른 선택지를 고르거나 같은 선택을 다시 눌러 이어서 진행하세요.</p></div>}
      {periodReady&&<div className="setup-block date-step"><strong><span className="step-number">{completed.length+1}</span>조회 날짜</strong><p>선택한 통계표가 실제 제공하는 기간 안에서 조회합니다.</p>{emptyAvailablePeriod(stepResult.availablePeriod)?<UnavailablePeriod result={stepResult}/>:stepResult.periodSelection?.applied?<PeriodConfirmation result={stepResult}/>:<div className="period-form"><label>시작일<DateSelector label="시작일" value={start} min={minDate} max={end&&(!maxDate||end<maxDate)?end:maxDate} onChange={setStart}/></label><span>→</span><label>종료일<DateSelector label="종료일" value={end} min={start&&(!minDate||start>minDate)?start:minDate} max={maxDate} onChange={setEnd}/></label></div>}{!emptyAvailablePeriod(stepResult.availablePeriod)&&minDate&&maxDate&&<p className="period-availability"><Icon name="check" size={14}/> 선택 가능 기간: {minDate} ~ {maxDate}</p>}</div>}
      {periodReady&&<div className="clarification-submit"><span>{invalid?"선택한 통계표의 제공 기간 안에서 날짜를 선택해 주세요.":!datesComplete?"시작일과 종료일을 먼저 선택해 주세요.":"조건이 준비되었습니다. 선택한 조건으로 통계 자료를 조회합니다."}</span><button disabled={loading||stepLoading||!datesComplete||invalid} onClick={()=>choose(Object.entries(selected).map(([clarification_id,values])=>({clarification_id,values})),start,end)}>선택 완료 후 데이터 조회 <Icon name="arrow" size={15}/></button></div>}
      <div className="clarification-trace"><Icon name="lineage" size={15}/><span>UI → Agent → 통계언어 사전 → 역질문 → 선택값 확정 → 사전 재검색</span></div>
    </div>
  </section>;
}

// Keep report authoring available in code while removing its result-screen UI.
const REPORT_WRITER_VISIBLE = false;

function Results({ result, onEdit, loading }: { result: QueryResponse; onEdit: (input: ChartEditInput) => Promise<boolean>; loading: boolean }) {
  const [reportView,setReportView]=useState<"graph"|"table">(wantsReportTable(result.query)?"table":"graph");
  const [lineageOpen, setLineageOpen] = useState(true);
  const [evidenceOpen,setEvidenceOpen]=useState(false);
  const [allDataOpen,setAllDataOpen]=useState(false);
  const actualPeriod=displayedPeriod(result.chart);
  const sources=groupSources(result.tables);
  const insights=resultInsights(result.insights,result.outputSpec?.editChanges);
  const allRows=result.chart.flatMap((series)=>series.points.map((point)=>({series,point})));
  const previewRows=result.chart.flatMap((series)=>{
    if(series.points.length<=5)return series.points.map((point)=>({series,point}));
    return [...series.points.slice(0,2),...series.points.slice(-3)].map((point)=>({series,point}));
  });
  const visibleRows=allDataOpen?allRows:previewRows;
  const editOptions=result.outputSpec?.visualization?.editOptions;
  const exportCsv = () => {
    const rows = ["계열,단위,시점,값", ...result.chart.flatMap((s) => s.points.map((p) => `"${s.label}","${s.unit}",${p.date},${p.value}`))];
    const blob = new Blob(["\ufeff" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "statbridge-analysis.csv"; a.click(); URL.revokeObjectURL(url);
  };
  const exportPng=async()=>{
    if(result.outputSpec?.plotlyFigure){
      const blob=await workspacePng(result.outputSpec.plotlyFigure);
      const url=URL.createObjectURL(blob);startFileDownload(url,'statbridge-chart.png');window.setTimeout(()=>URL.revokeObjectURL(url),60_000);return;
    }
    const plotlyChart=document.querySelector<HTMLElement>(".chart-panel .plotly-chart");
    if(plotlyChart){await Plotly.downloadImage(plotlyChart,{format:"png",filename:"statbridge-chart",...reportChartSize({},plotlyChart.getBoundingClientRect())});return;}
    const svgs=Array.from(document.querySelectorAll<SVGSVGElement>(".chart-panel .chart-wrap svg"));
    if(!svgs.length)return;
    const width=1400,chartHeight=500,gap=28;
    const canvas=document.createElement("canvas");canvas.width=width;canvas.height=svgs.length*chartHeight+(svgs.length-1)*gap;
    const context=canvas.getContext("2d");if(!context)return;
    context.fillStyle="#ffffff";context.fillRect(0,0,canvas.width,canvas.height);
    for(let index=0;index<svgs.length;index+=1){
      const clone=svgs[index].cloneNode(true) as SVGSVGElement;
      clone.setAttribute("xmlns","http://www.w3.org/2000/svg");clone.setAttribute("width","700");clone.setAttribute("height","250");
      const style=document.createElementNS("http://www.w3.org/2000/svg","style");
      style.textContent=".grid{stroke:#e8ebf3;stroke-width:1}.axis-label,.x-label{fill:#65718d;font:10px Arial,sans-serif}.axis-label{text-anchor:end}.x-label{text-anchor:middle}.line{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.dot{stroke:white;stroke-width:2}";
      clone.insertBefore(style,clone.firstChild);
      const blob=new Blob([new XMLSerializer().serializeToString(clone)],{type:"image/svg+xml;charset=utf-8"});
      const url=URL.createObjectURL(blob);const image=new Image();
      await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(new Error("그래프 이미지를 만들지 못했습니다."));image.src=url});
      context.drawImage(image,0,index*(chartHeight+gap),width,chartHeight);URL.revokeObjectURL(url);
    }
    const png=await new Promise<Blob|null>((resolve)=>canvas.toBlob(resolve,"image/png"));if(!png)return;
    const pngUrl=URL.createObjectURL(png);const link=document.createElement("a");link.download="statbridge-chart.png";link.href=pngUrl;link.click();
    window.setTimeout(()=>URL.revokeObjectURL(pngUrl),1000);
  };
  const noMatch=result.status==="no_match" || result.status==="data_unavailable";
  return <section className={`results ${result.editSessionId?'has-workspace':''}`} id="analysis-result">
    <div className="section-heading"><div><span className="eyebrow">ANALYSIS RESULT</span><h2>질문에서 근거까지, 한눈에</h2></div>{noMatch?<span className="unavailable-badge">지원 데이터 없음</span>:<span className="verified-badge"><Icon name="check" size={15}/> 검증된 데이터</span>}</div>
    <div className="result-grid">
      <article className="panel chart-panel">
        <div className="panel-top"><div><span className="question-label">분석한 질문</span><h3>“{result.query}”</h3></div><div className="panel-actions">{result.chart.length>0&&<button onClick={exportPng}><Icon name="chart" size={17}/> PNG</button>}<button onClick={exportCsv}><Icon name="download" size={17}/> CSV</button><button onClick={() => navigator.clipboard?.writeText(location.href)}><Icon name="share" size={17}/> 공유</button></div></div>
        {result.chart.length>0?<section className="result-conditions" aria-label="현재 그래프 조회 조건"><div><small>표시된 관측 기간</small><strong>{actualPeriod.start||result.period.start} ~ {actualPeriod.end||result.period.end}</strong></div><div><small>주기 · 계열</small><strong>{[...new Set(result.chart.map(series=>({M:'월별',Q:'분기별',A:'연별',Y:'연별',D:'일별',category:'항목별 비교'} as Record<string,string>)[series.frequency||'']||series.frequency||result.frequency))].join(' / ')} · {result.chart.length}개</strong></div><div><small>자료 출처</small><strong>{resultSourceLabel(result)}</strong></div>{titlePeriodMismatch(String(result.outputSpec?.chartState?.title||editOptions?.title||""),actualPeriod)&&<p role="status">제목에 적힌 기간과 현재 관측 기간이 다릅니다. 현재 자료는 위 관측 기간을 기준으로 확인하고, 필요하면 왼쪽에서 제목을 수정해 주세요.</p>}{result.outputSpec?.cachedSnapshot&&<p>저장 당시 자료를 표시합니다. 최신 관측값은 새로 조회해 주세요.</p>}</section>:<PeriodConfirmation result={result}/>}
        {result.outputSpec?.editChanges?.length ? <div className="setup-block" role="status"><strong>수정이 적용되었습니다</strong><ul>{result.outputSpec.editChanges.map((change,index)=><li key={index}>{change}</li>)}</ul></div> : null}
        {!result.editSessionId&&result.warnings?.map((warning, index) => <div className="error-banner" role="status" key={index}>{warning.replace(/([?&](?:apiKey|api_key|access_token)=)[^&\s)]+/gi, "$1[redacted]")}</div>)}
        {!result.editSessionId&&<div className="chart-header"><div><h3>{editOptions?.title||(result.chart.length ? `${chartLabels[result.chartType||'line']} 분석 결과` : "MCP 통계표 탐색 결과")}</h3>{result.chart.length > 0 && editOptions?.showLegend!==false && <div className="legend">{result.chart.map((s) => <span key={s.id}><i style={{background:s.color}}/> {s.label}{s.unit ? ` (${s.unit})` : ""}</span>)}</div>}</div>{result.chart.length>0&&<div className="filter-pills"><span>{result.period.start}–{result.period.end}</span><span>{result.frequency} <Icon name="chevron" size={13}/></span></div>}</div>}
        {result.outputSpec?.plotlyFigure ? result.editSessionId ? <ChartSketchEditor onViewChange={setReportView} preferTable={wantsReportTable(result.query)} warnings={result.warnings} series={result.chart} sessionId={result.editSessionId} revision={result.outputSpec} model={result.outputSpec.editCapabilities} canUndo={result.outputSpec.canUndo} canRedo={result.outputSpec.canRedo} loading={loading} onEdit={onEdit}><PlotlyRenderer figure={result.outputSpec.plotlyFigure} heading={{title:String(result.outputSpec.chartState?.title||''),subtitle:String(result.outputSpec.chartState?.subtitle||'')}}/></ChartSketchEditor> : <PlotlyRenderer figure={result.outputSpec.plotlyFigure}/> : result.chartMode === "separate" ? <div className="separate-charts">{result.chart.map((s) => <div className="single-chart" key={s.id}><h4>{s.label} <small>{s.unit}</small></h4><ChartRenderer series={[s]} chartType={result.chartType} xAxisLabel={editOptions?.xAxisLabel} yAxisLabel={editOptions?.yAxisLabel}/></div>)}</div> : <ChartRenderer series={result.chart} chartType={result.chartType} xAxisLabel={editOptions?.xAxisLabel} yAxisLabel={editOptions?.yAxisLabel}/>}
        {insights.observations.length>0&&<div className="insight-box"><div className="insight-title"><span><Icon name="sparkle" size={17}/></span><strong>핵심 인사이트</strong></div><ul>{insights.observations.map((x,index) => <li key={`${index}-${x}`}>{x}</li>)}</ul></div>}
        <DerivedEvidence key={result.derivedEvidence?.calculationId} result={result}/>
        {REPORT_WRITER_VISIBLE && (<details className="workspace-analysis-summary" open><summary>보고서 작성하기</summary><StatisticalQuestion displayMode={reportView} sessionId={result.editSessionId} revision={result.outputSpec?.editVersion??0} summary={result.summary} disabled={loading}/></details>)}
        {result.chart.length > 0 && <div className="data-table-wrap"><div className="subsection-title"><div><h3>{allDataOpen?"전체 데이터":"주요 데이터 예시"}</h3><p>{allDataOpen?"조회된 모든 관측치를 표시합니다.":"각 계열의 시작과 최근 값을 간략히 보여줍니다."}</p></div><span>전체 {allRows.length}개 관측치</span></div><table><thead><tr><th>계열</th><th>시점</th><th>값</th></tr></thead><tbody>{visibleRows.map(({series,point}) => <tr key={`${series.id}-${point.date}`}><td>{series.label}</td><td>{point.date}</td><td>{tableCell(point.value)} {series.unit}</td></tr>)}</tbody></table>{allRows.length>previewRows.length&&<div className="data-more"><span>{allDataOpen?`전체 ${allRows.length}개를 표시 중입니다.`:`${previewRows.length}개 예시만 표시 중입니다.`}</span><button type="button" aria-expanded={allDataOpen} onClick={()=>setAllDataOpen((current)=>!current)}>{allDataOpen?"간략히 보기":`전체 데이터 더 보기 (${allRows.length}개)`}<Icon name="chevron" size={14}/></button></div>}</div>}
      </article>
      <aside className="evidence-column">
        <article className="panel evidence-panel"><div className="aside-title"><span><Icon name="database" size={18}/></span><div><span className="eyebrow">DATA SOURCES</span><h3>사용한 통계</h3></div></div>{sources.map((t) => <div className="source-card" key={t.tableId}><div><span className="table-id">{t.tableId}</span><span className="source-check"><Icon name="check" size={12}/></span></div><strong>{t.name}</strong><p>{t.item} · {t.unit}</p><small>{t.source}</small></div>)}</article>
        <article className="panel lineage-panel"><button className="lineage-toggle" onClick={() => setLineageOpen(!lineageOpen)} aria-expanded={lineageOpen}><div className="aside-title"><span><Icon name="lineage" size={18}/></span><div><span className="eyebrow">DATA LINEAGE</span><h3>데이터 선택 과정</h3></div></div><Icon name="chevron" size={17}/></button><button type="button" className="lineage-log-button" onClick={()=>setEvidenceOpen(true)}>실행 로그 · Langfuse</button>{lineageOpen && <div className="timeline">{result.lineage.map((step, i) => <div className="timeline-row" key={step.id}><div className="timeline-marker"><span><Icon name="check" size={12}/></span>{i < result.lineage.length - 1 && <i/>}</div><div><strong>{step.title}</strong><p>{step.description}</p></div></div>)}</div>}</article>
      </aside>
    </div>
  <ExecutionEvidence result={result} open={evidenceOpen} onClose={()=>setEvidenceOpen(false)}/></section>;
}

export default function App() {
  const [view, setView] = useState<"home" | "lineage">("home");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [chatTransition,setChatTransition]=useState<{data:QueryResponse|null}|null>(null);
  const [chatArchive,setChatArchive]=useState<SavedChat[]|null>(null);
  const [accountAnalyses,setAccountAnalyses]=useState<AccountAnalysis[]>([]);
  const [archiveError,setArchiveError]=useState('');
  const [archiveBusy,setArchiveBusy]=useState(false);
  const [chatNotice,setChatNotice]=useState('');
  const [chatBusy,setChatBusy]=useState(false);
  const chatSaving=useRef(false);

  const liveResult=useRef<QueryResponse|null>(null);
  const editQueue=useRef<Promise<unknown>>(Promise.resolve());
  const [error, setError] = useState("");
  const [pendingOutput,setPendingOutput]=useState<QueryResponse|null>(null);
  const [failedQuery,setFailedQuery]=useState<QueryResponse|null>(null);
  const lastQueryRequest=useRef<QueryRequest|null>(null);
  const retryAction=useRef<(()=>void)|null>(null);
  const runCachedOutput=async(payload:OutputRenderRequest)=>{setLoading(true);setError('');try{showResult(await submitOutput(payload));}catch(e){if(e instanceof AnalysisTimeoutError)setFailedQuery(analysisTimedOut(lastQueryRequest.current?.query||query));else setError(e instanceof Error?e.message:'그래프를 만들지 못했습니다. 다시 시도해 주세요.');}finally{setLoading(false);}};
  const trackedQuery=async(payload:QueryRequest)=>{
    const budget=derivedBudget(payload.query,payload.state);
    setPendingOutput(null);lastQueryRequest.current=payload;retryAction.current=()=>void runQuery(payload);
    try{return await withAnalysisDeadline(async signal=>{
    const data=await submitQuery(payload,signal);if(signal.aborted)throw new AnalysisTimeoutError();const output=automaticOutput(data);
    if(!output)return data;
    retryAction.current=()=>void runCachedOutput(output);
    try{return await submitOutput(output,signal);}catch(e){
      if(e instanceof AnalysisTimeoutError||signal.aborted)throw e;
      const message=e instanceof Error?e.message:'기본 그래프를 만들지 못했습니다.';
      return {...data,summary:'조회된 자료는 유지됩니다. 아래에서 지원되는 그래프를 선택해 주세요.',warnings:[...(data.warnings||[]),message]};
    }
    },undefined,budget);}catch(e){if(e instanceof AnalysisTimeoutError)return analysisTimedOut(payload.query,budget);throw e;}
  };
  const runQuery=async(payload:QueryRequest)=>{setLoading(true);setError('');try{showResult(await trackedQuery(payload));}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setLoading(false);}};


  useEffect(()=>{
    const restore=(state:Record<string,unknown>|null)=>{
      if(!state?.statbridge)return;
      if(!ownsHistory(state,accountKey('history'))){liveResult.current=null;setResult(null);setQuery(exploreExamples[0].query);setView('home');history.replaceState(null,'',location.pathname);return;}
      setView(state.view==="lineage"?"lineage":"home");
      setQuery(typeof state.query==="string"?state.query:exploreExamples[0].query);
      liveResult.current=(state.result as QueryResponse|null)||null;setResult(liveResult.current);setError("");
      const saved=state.result as QueryResponse|null;
      if(saved?.editSessionId)void fetchWorkspace(saved.editSessionId).then(data=>{if(liveResult.current?.editSessionId!==saved.editSessionId)return;liveResult.current=data;setResult(data);}).catch(e=>{if(liveResult.current?.editSessionId===saved.editSessionId)setError(e.message);});
      window.setTimeout(()=>window.scrollTo({top:state.result?document.body.scrollHeight:0,behavior:"smooth"}),20);
    };
    // Retire automatic snapshots and overflow caches; explicit saved analyses remain.
    try{savedChats();}catch{setChatNotice('브라우저 보관 기록을 정리하지 못했습니다. 브라우저 저장소 권한을 확인해 주세요.');}
    history.replaceState({statbridge:true,account:accountKey('history'),view:'home',query:'',result:null},'',location.pathname);
    history.scrollRestoration='manual';
    window.scrollTo({top:0,behavior:'instant'});
    const onPopState=(event:PopStateEvent)=>restore(event.state);
    window.addEventListener("popstate",onPopState);return()=>window.removeEventListener("popstate",onPopState);
  },[]);

  const historyUrl=(nextView:"home"|"lineage",nextQuery=query)=>nextView==="lineage"?`${location.pathname}?view=lineage`:`${location.pathname}${nextQuery?`?q=${encodeURIComponent(nextQuery)}`:""}`;
  const pushUiState=(nextView:"home"|"lineage",nextResult:QueryResponse|null,nextQuery=query)=>history.pushState({statbridge:true,account:accountKey('history'),view:nextView,query:nextQuery,result:nextResult},"",historyUrl(nextView,nextQuery));

  const showResult = (data: QueryResponse, editing=false) => {
    const previous=liveResult.current;
    if(data.status==="need_output_config"&&data.warnings?.length&&previous?.editSessionId){setFailedQuery(null);setError("");setPendingOutput(data);return;}
    if(['need_details','no_match','data_unavailable','request_failed'].includes(data.status||'')){setFailedQuery(data);return;}
    setFailedQuery(null);setError("");liveResult.current=data;setResult(data);if(!editing){setPendingOutput(null);setQuery(data.query);}
    if(data.editSessionId&&!readChat(data.editSessionId).length)appendChat(data.editSessionId,{role:'assistant',kind:'edit',text:`그래프를 조회했습니다.\n${data.query}\n조회 기간: ${data.period.start} ~ ${data.period.end}`});

    if(editing)history.replaceState({statbridge:true,account:accountKey('history'),view:'home',query:data.query||query,result:data},'',historyUrl('home',data.query||query));
    else {pushUiState("home",data,data.query||query);window.setTimeout(() => (document.querySelector(".chart-workspace")||document.getElementById("analysis-result"))?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);}
  };

  const goHome=()=>{
    try{localStorage.removeItem(accountKey('statbridge.workspace.current'));}catch{setChatNotice('이전 대화를 보관하지 못해 현재 화면을 유지했습니다.');return;}
    liveResult.current=null;setResult(null);setPendingOutput(null);setQuery('');setView('home');setError('');setFailedQuery(null);setChatTransition(null);setChatArchive(null);lastQueryRequest.current=null;retryAction.current=null;
    history.replaceState({statbridge:true,account:accountKey('history'),view:'home',query:'',result:null},'',location.pathname);
    window.scrollTo({top:0,behavior:'smooth'});
  };

  const navigateView=(nextView:"home"|"lineage")=>{
    setView(nextView);pushUiState(nextView,nextView==="home"?result:null,query);window.scrollTo({top:0,behavior:"smooth"});
  };

  const queryCatalogTable = async (tableQuery: string, dimensionValues: Record<string, string>) => {
    setView("home"); setQuery(tableQuery);
    setLoading(true); setError("");
    try { showResult(await trackedQuery({query: tableQuery, dimension_values: dimensionValues, execute: true})); }
    catch (err) { setError(err instanceof Error ? err.message : "통계표 조회 중 오류가 발생했습니다."); }
    finally { setLoading(false); }
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true); setError("");
    try {
      const data = await trackedQuery(initialQueryRequest(query));
      showResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.");
    } finally { setLoading(false); }
  };

  const chooseClarification = async (selections: Array<{ clarification_id: string; values: string[] }>, start:string, end:string) => {
    if (!result || !selections.length) return;
    setLoading(true); setError("");
    try {
      const data = await trackedQuery({
        query: result.query,
        state: result.state,
        selections,
        execute: true,
        period_start:start, period_end:end,
      });
      showResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "역질문 처리 중 오류가 발생했습니다.");
    } finally { setLoading(false); }
  };

  const submitPeriod = async (start: string, end: string) => {
    if (!result) return;
    setLoading(true); setError("");
    try {
      const data = await trackedQuery({ query: result.query, state: result.state, execute: true, period_start: start, period_end: end });
      showResult(data);
    } catch (err) { setError(err instanceof Error ? err.message : "기간 처리 중 오류가 발생했습니다."); }
    finally { setLoading(false); }
  };

  const renderSelectedOutput = async (value:{chartType:"auto"|ChartType;mode:"combined"|"separate";title:string;showLegend:boolean;xAxisLabel:string;yAxisLabel:string;naturalLanguage:string}) => {
    const selected=pendingOutput||result;if (!selected) return;
    retryAction.current=()=>void renderSelectedOutput(value);
    const sessionIds=selected.outputSessionIds||[selected.outputSessionId||""].filter(Boolean);
    if(!sessionIds.length)return;
    setLoading(true); setError("");
    try {
      const data = await submitOutput({session_ids:sessionIds,chart_type:value.chartType,chart_mode:value.mode,title:value.title||undefined,show_legend:value.showLegend,x_axis_label:value.xAxisLabel||undefined,y_axis_label:value.yAxisLabel||undefined,natural_language:value.naturalLanguage});
      showResult(data);
    } catch (err) { if(err instanceof AnalysisTimeoutError)setFailedQuery(analysisTimedOut(selected.query));else setError(err instanceof Error ? err.message : "출력 에이전트가 그래프를 만드는 중 오류가 발생했습니다."); }
    finally { setLoading(false); }
  };

  const editSelectedOutput = (input: ChartEditInput): Promise<boolean> => {
    const targetSession=(liveResult.current||result)?.editSessionId;
    retryAction.current=()=>void editSelectedOutput(input).catch(()=>{});
    const job=editQueue.current.then(async()=>{
      const current=liveResult.current||result;if(!targetSession||current?.editSessionId!==targetSession)return false;
      if(current.outputSpec?.chatReadOnly)throw new Error('서버 세션이 만료된 저장 그래프입니다. 새 채팅에서 통계를 다시 조회해 주세요.');
      setLoading(true);setError("");
      try { const updated=await submitOutputEdit({edit_session_id:current.editSessionId,revision:current.outputSpec?.editVersion,...input},!!current.derivedEvidence);if(liveResult.current?.editSessionId!==current.editSessionId)return false;appendChat(current.editSessionId,{role:'assistant',kind:'edit',text:editDescription(current,updated,input.action)});showResult(updated,true); return true; }
      catch(err){
        if(err instanceof EditConflictError){
          try{const latest=await fetchWorkspace(current.editSessionId);if(liveResult.current?.editSessionId===current.editSessionId)showResult(latest,true);}
          catch{/* Preserve the current analysis; the same retry can refresh again. */}
        }
        if(!input.conversation?.length)setError(err instanceof Error?err.message:"그래프 수정 중 오류가 발생했습니다.");throw err;
      }
      finally{setLoading(false);}
    });
    editQueue.current=job.then(()=>{},()=>{});return job;
  };

  useEffect(()=>{const open=(event:Event)=>{const id=(event as CustomEvent<string>).detail;setLoading(true);fetchWorkspace(id).then(updated=>showResult(updated)).catch(e=>setError(e.message)).finally(()=>setLoading(false));};window.addEventListener('statbridge:open-saved',open);return()=>window.removeEventListener('statbridge:open-saved',open);},[]);

  const persistChat=async(current:QueryResponse)=>{
    saveChat(current);
    if(current.outputSpec?.chatReadOnly){setChatNotice('서버 세션이 만료되어 분석을 이 브라우저에만 보관했습니다.');return false;}
    try{await accountRequest(`/workspace/${encodeURIComponent(current.editSessionId!)}/save`,{edit_session_id:current.editSessionId,revision:current.outputSpec?.editVersion,format:'json'});window.dispatchEvent(new Event('statbridge:graph-saved'));return true;}
    catch{setChatNotice('대화와 그래프는 이 브라우저에 저장했습니다. 계정 저장은 완료하지 못했습니다. 계정 보관함은 최대 5개입니다.');return false;}
  };
  const finishChatTransition=async(save:boolean)=>{
    if(chatSaving.current)return;chatSaving.current=true;setChatBusy(true);
    const previous=liveResult.current;
    try{
      if(previous?.editSessionId){if(save)await persistChat(previous);else removeSavedChat(previous.editSessionId);}
      const next=chatTransition?.data;setChatTransition(null);
      localStorage.removeItem(accountKey('statbridge.workspace.current'));
      liveResult.current=null;setResult(null);setFailedQuery(null);setError('');
      if(next)showResult(next,false);
      else{setQuery('');setView('home');history.replaceState({statbridge:true,account:accountKey('history'),view:'home',query:'',result:null},'',location.pathname);requestAnimationFrame(()=>document.getElementById('statistics-query')?.focus());window.scrollTo({top:0,behavior:'smooth'});}
    }catch{setChatNotice('저장 공간이 부족하거나 사용할 수 없습니다. 대화를 유지했습니다. 브라우저 저장 공간을 확인해 주세요.');}finally{chatSaving.current=false;setChatBusy(false);}
  };
  useEffect(()=>{
    const save=async()=>{const current=liveResult.current;if(!current?.editSessionId||chatSaving.current)return;chatSaving.current=true;setChatNotice('분석을 보관하고 있습니다…');try{if(await persistChat(current))setChatNotice('분석과 대화·편집 기록을 보관했습니다. 그래프는 계정에, 대화는 이 브라우저에 저장됩니다.');}catch{setChatNotice('대화를 저장하지 못했습니다. 브라우저 저장 공간을 확인해 주세요.');}finally{chatSaving.current=false;}};
    const fresh=()=>goHome();
    const list=()=>{setChatArchive(savedChats());setArchiveBusy(true);setArchiveError('');setAccountAnalyses([]);accountRequest('/account/graphs').then(data=>setAccountAnalyses([...data.items,...([])])).catch(()=>setArchiveError('계정 저장 목록을 불러오지 못했습니다. 이 브라우저의 임시 분석은 열 수 있습니다.')).finally(()=>setArchiveBusy(false));};
    const failure=()=>setChatNotice('대화는 화면에 유지되지만 브라우저에 저장하지 못했습니다.');
    window.addEventListener('statbridge:save-chat',save);window.addEventListener('statbridge:new-chat',fresh);window.addEventListener('statbridge:show-chats',list);window.addEventListener('statbridge:chat-storage-error',failure);
    return()=>{window.removeEventListener('statbridge:save-chat',save);window.removeEventListener('statbridge:new-chat',fresh);window.removeEventListener('statbridge:show-chats',list);window.removeEventListener('statbridge:chat-storage-error',failure);};
  },[]);
  useEffect(()=>{
    if(!chatTransition&&!chatArchive)return;
    const previous=document.activeElement as HTMLElement|null;
    const dialog=document.querySelector<HTMLElement>('.chat-modal');
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const keys=(event:KeyboardEvent)=>{
      if(event.key==='Escape'&&!chatBusy){setChatTransition(null);setChatArchive(null);return;}
      if(event.key!=='Tab')return;
      const controls=Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea,select')||[]);
      const first=controls[0],last=controls.at(-1);if(!first)return;
      if(event.shiftKey&&(document.activeElement===first||!dialog?.contains(document.activeElement))){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&(document.activeElement===last||!dialog?.contains(document.activeElement))){event.preventDefault();first.focus();}
    };
    document.addEventListener('keydown',keys);return()=>{document.removeEventListener('keydown',keys);if(previous?.isConnected)previous.focus();};
  },[chatTransition,chatArchive,chatBusy]);
  const reopenChat=async(chat:SavedChat)=>{
    setLoading(true);try{
      let data:QueryResponse;try{data=await fetchWorkspace(chat.id);}catch{data={...chat.result,outputSpec:chat.result.outputSpec?{...chat.result.outputSpec,cachedSnapshot:true,chatReadOnly:true}:undefined};setChatNotice('저장 당시 그래프와 대화를 열었습니다. 서버 세션이 만료되어 편집하려면 그래프를 다시 조회해 주세요.');}
      const existing=readChat(chat.id);writeChat(chat.id,existing.length>=chat.messages.length?existing:chat.messages);showResult(data,true);setQuery(data.query);setView('home');setChatArchive(null);
      requestAnimationFrame(()=>document.getElementById('analysis-result')?.scrollIntoView({block:'start'}));
    }catch{setChatNotice('현재 대화를 저장하지 못해 이전 대화를 열지 않았습니다.');}finally{setLoading(false);}
  };

  return <div className="app-shell">
    <div className="main-shell">
      {chatNotice&&<p className="chat-notice" role="status">{chatNotice}<button onClick={()=>setChatNotice('')}>알림 닫기</button></p>}
      {chatTransition&&<div className="chat-modal-backdrop"><section role="dialog" aria-modal="true" aria-label="이전 그래프 대화 처리" className="chat-modal"><h2>새 그래프 대화를 시작할까요?</h2><p>현재 그래프의 대화와 편집 기록을 저장하거나 삭제한 뒤 새 대화를 시작합니다. 저장한 대화는 이 브라우저의 내 계정에서 다시 볼 수 있습니다.</p><button disabled={chatBusy} onClick={()=>void finishChatTransition(true)}>이전 대화 저장 후 시작</button><button disabled={chatBusy} onClick={()=>void finishChatTransition(false)}>이전 대화 삭제 후 시작</button><button disabled={chatBusy} onClick={()=>setChatTransition(null)}>취소 · 현재 대화 유지</button></section></div>}
      {chatArchive&&<div className="chat-modal-backdrop"><section role="dialog" aria-modal="true" aria-label="분석 보관함" className="chat-modal"><h2>분석 보관함</h2><p>저장한 최신 분석 5개를 표시합니다. 분석은 자동으로 임시 보관하지 않습니다.</p>{archiveBusy&&<p role="status">계정 저장 목록을 확인하고 있습니다.</p>}{archiveError&&<p role="alert">{archiveError}</p>}{!chatArchive.length&&!accountAnalyses.length&&!archiveBusy&&<p>보관한 분석이 없습니다.</p>}{analysisEntries(chatArchive,accountAnalyses).map(entry=><article key={entry.id}><strong>{entry.title}</strong><small>{new Date(entry.savedAt).toLocaleString('ko-KR')} · {entry.account?'계정에 그래프 저장':'이 브라우저에 임시 보관'}{entry.local?` · 대화·편집 기록 ${entry.local.messages.length}개`:''}</small><button disabled={loading} onClick={()=>{if(entry.local)void reopenChat(entry.local);else{window.dispatchEvent(new CustomEvent('statbridge:open-saved',{detail:entry.id}));setChatArchive(null);}}}>분석 열기</button><button className="control-danger" disabled={loading||archiveBusy} onClick={async()=>{if(!window.confirm('이 분석의 보관된 대화·편집 기록과 계정 저장 그래프를 삭제할까요? 다운로드한 파일과 현재 화면은 유지됩니다.'))return;setArchiveBusy(true);try{if(entry.account){await accountRequest('/account/graphs/'+encodeURIComponent(entry.id),undefined,'DELETE');window.dispatchEvent(new Event('statbridge:graph-saved'));}if(entry.local)removeSavedChat(entry.id);setChatArchive(savedChats());setAccountAnalyses(items=>items.filter(item=>item.id!==entry.id));}catch{setArchiveError('삭제하지 못한 기록이 있습니다. 보관함을 다시 열어 상태를 확인해 주세요.');}finally{setArchiveBusy(false);}}}>보관 삭제</button></article>)}<button onClick={()=>setChatArchive(null)}>닫기</button></section></div>}
      <Header view={view} goHome={goHome} selectView={navigateView} busy={loading}/>
      {view === "lineage" ? <DataCatalog onQuery={queryCatalogTable} loading={loading}/> : <main id="main-content" tabIndex={-1}>
        <Hero query={query} setQuery={setQuery} submit={onSubmit} loading={loading}/>
        {(error||failedQuery)&&!failedQuery?.availability&& !(failedQuery?.supplement&&(failedQuery.supplement.missing.length>0||failedQuery.supplement.uploaded.length>0))&& (failedQuery?.requiresQueryRefinement?<QueryRefinementCard result={failedQuery} busy={loading} onSubmit={text=>{setQuery(text);void runQuery(initialQueryRequest(text));}}/>:<RecoveryPanel message={error||failedQuery?.summary||''} result={failedQuery||result} busy={loading} retry={()=>{if(retryAction.current)retryAction.current();else void runQuery(initialQueryRequest(result?.query||query));}} onRevise={()=>document.getElementById('statistics-query')?.focus()} selectPeriod={(start,end)=>{const last=lastQueryRequest.current;void runQuery({...last||initialQueryRequest(result?.query||query),period_start:start,period_end:end,execute:true});}} selectTable={table=>{const last=lastQueryRequest.current;const next={query:`${table.tableId} ${table.name}`,execute:true,...(/^\d{4}-\d{2}-\d{2}$/.test(last?.period_start||failedQuery?.period?.start||'')?{period_start:last?.period_start||failedQuery?.period?.start,period_end:last?.period_end||failedQuery?.period?.end}:{})};setQuery(next.query);void runQuery(next);}}/>)}
        {shouldShowDerivedSupplement(failedQuery)&&failedQuery&&<DerivedSupplementPanel key={failedQuery.supplement?.workspaceId||failedQuery.query} result={failedQuery} onResult={async data=>{const output=automaticOutput(data);showResult(output?await submitOutput(output):data);}}/>}
        {failedQuery?.availability&&<DerivedAvailabilityPanel key={failedQuery.query} result={failedQuery} onResult={async data=>{showResult(data);window.setTimeout(()=>document.querySelector('.ds-panel')?.scrollIntoView({behavior:'smooth',block:'start'}),80);}} busy={loading} retry={()=>void runQuery(initialQueryRequest(failedQuery.query))} confirm={answer=>{const next=failedQuery.query+'\n추가 조건: '+answer+'\n아직 계산하지 말고 원자료 확인만 이어가 주세요.';setQuery(next);void runQuery(initialQueryRequest(next));}}/>}
        {failedQuery&&<RequestExecutionLog result={failedQuery}/>}
        {!failedQuery&&result&&["need_clarification","need_period","need_output_config"].includes(result.status||"")&&<RequestExecutionLog result={result}/>}
        {result?.status === "need_clarification" && <ClarificationPanel result={result} loading={loading} choose={chooseClarification} advance={trackedQuery} onResult={showResult}/>}
        {result?.status === "need_period" && <PeriodPanel result={result} loading={loading} submit={submitPeriod}/>}
        {(pendingOutput||result?.status === "need_output_config") && <OutputConfigPanel result={pendingOutput||result!} loading={loading} submit={renderSelectedOutput}/>}
        {result && !["need_clarification","need_period","need_output_config"].includes(result.status || "") && <Results result={result} onEdit={editSelectedOutput} loading={loading}/>}
        <UsageGuide/>
      </main>}
      <footer><span>© 2026 StatBridge</span><span>경제통계 탐색 · 시각화 · 출처 확인</span></footer>
    </div>
  </div>;
}
