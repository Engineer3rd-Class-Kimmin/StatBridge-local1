import {useState} from 'react';
import {calculationFormula} from './calculationFormula';
import {accountRequest} from './accountApi';
import {tableObservation} from './reportTable';
import type {QueryResponse} from './api/types';

type SourcePage={alias:string;total:number;rows:Array<Record<string,string|number>>};

export default function DerivedEvidence({result}:{result:QueryResponse}) {
 const evidence=result.derivedEvidence;
 const [raw,setRaw]=useState<SourcePage[]|null>(null);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 if(!evidence)return null;
 const labels:Record<string,string>=Object.fromEntries(evidence.sourceSelections.map(s=>[s.alias,s.selection]));
 evidence.plan.nodes.forEach((n,i)=>{labels[n.id]=`계산 ${i+1} 결과`;});
 const load=async()=>{
  if(raw){setRaw(null);return;}
  setBusy(true);setError('');
  try{
   const page=await accountRequest('/output/derived-data',{edit_session_id:result.editSessionId,limit:200});
   setRaw(page.sources);
  }catch(e){setError(e instanceof Error?e.message:'원자료를 불러오지 못했습니다.');}
  finally{setBusy(false);}
 };
 return <details className="summary-evidence" open><summary>어떻게 계산했나요? · 파생지표 계산 근거 · {evidence.plan.indicator_name}</summary>
  <p>{evidence.plan.economic_definition}</p>
  <p>{evidence.sourceSelections.some(s=>s.origin==='user_file')?'API·사용자 제공 파일':'한국은행 KOSIS API'} · 표시기간 {evidence.plan.start_period}–{evidence.plan.end_period} · 원자료 조회기간 {evidence.bounds.fetch_start}–{evidence.bounds.fetch_end}</p>
  <ul>{evidence.sourceSelections.map(s=><li key={s.alias}><code>{s.alias}</code>: {s.selection}{s.tableId&&<small> · {s.tableId} / 항목 {s.itemId} · {s.frequency}</small>}{s.origin==='user_file'&&<p>사용자 제공 자료 · {s.filename} · {s.provenance?.join(' / ')} · SHA-256 {s.sha256}</p>}</li>)}</ul>
  <ol>{evidence.plan.nodes.map(n=><li key={n.id}><p><strong>{labels[n.id]}</strong>: {calculationFormula(n,labels)}</p><small><code>{n.id} = {n.operator}({n.inputs.join(', ')})</code></small>{n.lag!=null&&` · 시차 ${n.lag}`}{n.window!=null&&` · 창 ${n.window}`}{n.base_period&&` · 기준 ${n.base_period}`}{n.exponent!=null&&` · 지수 ${n.exponent}`}{n.operator==='period_change'&&<><p>적용 산식: {n.direction==='inverse'?'(시작값 ÷ 종료값 − 1) × 100':'(종료값 ÷ 시작값 − 1) × 100'} (%)</p>{n.periods?.map(p=><p key={p.label}>{p.label}: {p.start} ~ {p.end} · {p.basis==='monthly_mean'?'시작 월·종료 월 원자료 관측값 평균 후 변화율':'특정일 실제 관측값'}</p>)}</>}</li>)}</ol>
  {evidence.categoryAverages?.map(a=><p key={a.label}>{a.label} 평균선: 선택한 {a.nodes.length}개 변화율의 산술평균 = {a.value.toFixed(6)}% · 포함 노드: {a.nodes.join(', ')}</p>)}
  <p>{evidence.missingPolicy}</p>
  <details><summary>결측·계산 불가 시점 {evidence.issues.length}건</summary><ul>{evidence.issues.slice(0,200).map((i,k)=><li key={k}>{i.node} · {i.period}: {i.reason}</li>)}</ul>{evidence.issues.length>200&&<p>처음 200건을 표시합니다. 전체 근거는 계산 기록에 보존됩니다.</p>}</details>
  <p><small>계산 ID: {evidence.calculationId} · 근거 해시: {evidence.sha256}</small></p>
  {result.editSessionId&&<button type="button" disabled={busy} onClick={load}>{busy?'불러오는 중':raw?'계산 결과로 돌아가기':'사용한 원자료 보기'}</button>}
  {error&&<p role="alert">{error}</p>}
  {raw?.map(s=><section key={s.alias}><h4>{s.alias} 원자료 ({s.total}개 중 {s.rows.length}개)</h4><table><thead><tr><th>시점</th><th>원자료</th><th>단위</th></tr></thead><tbody>{s.rows.map((r,i)=><tr key={i}><td>{r.PRD_DE}</td><td>{tableObservation(r.DT)}</td><td>{r.UNIT_NM}</td></tr>)}</tbody></table></section>)}
 </details>;
}
