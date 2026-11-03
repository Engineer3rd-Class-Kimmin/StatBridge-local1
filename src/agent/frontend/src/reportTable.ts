import type {TableLayout,LayoutSection} from './tableLayout';
export const tableTemplates=[['default','기본 화면형'],['BOK-T01','기본 3선형 통계표'],['BOK-T02','다단 머리글 표'],['BOK-T03','시계열 비교표'],['BOK-T04','부록 대형 데이터표'],['BOK-T05','항목 · 소계 강조표'],['BOK-T06','주석 · 출처 결합표']] as const;
export type TableOptions={export_width?:number|null;export_height?:number|null;layout?:TableLayout;comparison?:'none'|'previous_change'|'yearly_change'|'period_change'|'period_growth';table_number?:string;print_preset?:'auto'|'a4_portrait'|'a4_landscape';orientation:'indicators'|'periods';decimals:number;recent_periods:number|null;labels:string[];highlight_latest:boolean;title:string;note:string;template?:typeof tableTemplates[number][0];emphasized_labels?:string[]};
export type TableDocument={layoutSections?:LayoutSection[];annotations?:Array<{number:number;text:string;target:string}>;title:string;caption?:string;options:TableOptions;sections:Array<{frequency:string;periods:string[];periodCaption:string;headers:string[];rows:Array<Array<string|number|null>>;headerGroups?:Array<{label:string;start:number;span:number}>;computedCells?:Array<{label:string;column:number;unit:string;formula:string;value:number|null;reason?:string;observations:Array<{date:string;value:number}>}>;emphasizedRows?:number[]}>;sources:Array<{tableId:string;tableName:string;source?:string;displaySource?:string}>;notes:string[]};
export function wantsReportTable(query:string){return /(?:표로\s*(?:보여|작성|정리|만들)|(?:비교표|추이표|보고서용\s*표|표)\s*(?:를\s*)?(?:만들|작성|생성))/.test(query);}
export function tableFrequency(value:string){return ({M:'월별',Q:'분기별',Y:'연별',A:'연별',D:'일별',category:'항목별 비교'} as Record<string,string>)[value]||value;}
export function tableFrequencyMatches(actual:string,requested:string){return requested==='Y'?actual==='Y'||actual==='A':actual===requested;}
export function tableCell(value:string|number|null,decimals=2){return value===null?'—':typeof value==='number'?value.toLocaleString('ko-KR',{minimumFractionDigits:decimals,maximumFractionDigits:decimals}):value;}
// Only observation fields use numeric-string conversion; labels and periods stay text.
export function tableObservation(value:string|number|null){
 if(typeof value!=='string'||!value.trim())return tableCell(value);
 const number=Number(value.replaceAll(',',''));
 return tableCell(Number.isFinite(number)?number:value);
}
// Ambiguous instructions never become chart edits or arbitrary numeric changes.
export function tableInstruction(text:string):Partial<TableOptions>|null {
 const s=text.trim().replace(/[.!。]$/,'');
 const template=tableTemplates.find(([id,name])=>id!=='default'&&(s===`${id}로 바꿔줘`||s===`${name}로 바꿔줘`));
 if(template)return {template:template[0]};
 const decimal=s.match(/^(?:표의?\s*)?소수점\s*(여섯|다섯|[0-6영일이삼사오육한두세네])\s*(?:자리|자릿수)(?:로)?(?:\s*(?:바꿔줘|변경해줘|표시해줘|보여줘|설정해줘))?$/);
 if(decimal){const words:Record<string,number>={영:0,일:1,이:2,삼:3,사:4,오:5,육:6,한:1,두:2,세:3,네:4,다섯:5,여섯:6};return {decimals:words[decimal[1]]??Number(decimal[1])};}
 const recent=s.match(/^(?:표에서\s*)?최근\s*(\d+)\s*(?:개\s*)?(?:개월|월|분기|년|시점)(?:만)?\s*(?:보여줘|표시해줘)$/);
 if(recent&&Number(recent[1])>=1&&Number(recent[1])<=600)return {recent_periods:Number(recent[1])};
 if(/^(?:표의?\s*)?(?:시점|기간|월|분기|연도)(?:을|를)?\s*행으로\s*(?:바꿔줘|변경해줘|표시해줘)$/.test(s))return {orientation:'periods'};
 if(/^(?:표의?\s*)?(?:지표|항목|국가|지역)(?:을|를)?\s*행으로\s*(?:바꿔줘|변경해줘|표시해줘)$/.test(s))return {orientation:'indicators'};
 if(/^(?:표에서\s*)?전체\s*(?:기간|시점)(?:을|를)?\s*(?:보여줘|표시해줘)$/.test(s))return {recent_periods:null};
 if(/^(?:표의?\s*)?최신\s*(?:값|시점)(?:을|를)?\s*강조해줘$/.test(s))return {highlight_latest:true};
 if(/^(?:표의?\s*)?강조(?:를)?\s*(?:없애줘|해제해줘)$/.test(s))return {highlight_latest:false};
 return null;
}
