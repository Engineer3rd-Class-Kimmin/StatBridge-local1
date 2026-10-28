import type {QueryResponse} from './api/types';
type Evidence=NonNullable<QueryResponse['derivedEvidence']>;
type Node=Evidence['plan']['nodes'][number];
export function calculationFormula(n:Node, labels:Record<string,string>):string {
 const x=n.inputs.map(id=>labels[id]||id), a=x[0], b=x[1];
 const lag=n.lag??1;
 switch(n.operator){
 case 'add':return x.join(' ＋ ');
 case 'subtract':return `${a} − ${b}`;
 case 'multiply':return x.join(' × ');
 case 'divide':return `${a} ÷ ${b}`;
 case 'percent_ratio':return `(${a} ÷ ${b}) × 100 (%)`;
 case 'pct_change':return `(${a}(당기) ÷ ${a}(${lag}기간 전) − 1) × 100 (%)`;
 case 'yoy_pct':return `(${a}(당기) ÷ ${a}(전년 동일 시점) − 1) × 100 (%)`;
 case 'difference':return `${a}(당기) − ${a}(${lag}기간 전)`;
 case 'rolling_mean':return `${a}의 당기 포함 ${n.window}기간 산술평균 (결측이면 계산하지 않음)`;
 case 'cumulative_sum':return `${a}의 표시 시작기간부터 당기까지 누적합`;
 case 'rebase_100':return `${a}(당기) ÷ ${a}(${n.base_period}) × 100`;
 case 'base_change':return `(${a}(당기) ÷ ${a}(${n.base_period}) − 1) × 100`;
 case 'lag':return `${a}의 ${lag}기간 전 값`;
 case 'inverse':return `1 ÷ ${a}`;
 case 'log':return `ln(${a}) (자연로그)`;
 case 'power':return `(${a}) ^ ${n.exponent}`;
 case 'period_change':return n.direction==='inverse'?'(시작값 ÷ 종료값 − 1) × 100 (%)':'(종료값 ÷ 시작값 − 1) × 100 (%)';
 default:return `${n.operator}(${x.join(', ')})`;
 }
}
