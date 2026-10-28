"""Exact standalone edits grounded in current data; compound/ambiguous text delegates."""
import re
from output_schema import ChartEditCommand

COLORS={'회색':'#9ca3af','빨간색':'#ef4444','파란색':'#3b82f6','검은색':'#111827','노란색':'#eab308','녹색':'#22c55e'}
COLOR=r'(#[0-9a-fA-F]{6}|회색|빨간색|파란색|검은색|노란색|녹색)'
END=r'(?:바꿔줘|변경해줘|바꿔\s*주세요|변경해\s*주세요|해줘|해\s*주세요)'

def simple_workspace_commands(text,spec,series,selected='chart'):
    text=text.strip().rstrip('.!?')
    compound=re.fullmatch(r'제목(?:을|은)?\s*([‘“\"\'])(.+?)[’”\"\'](?:으?로)\s*(?:바꾸고|변경하고|하고|,)?\s*(?:제목(?:의)?\s*)?(?:(?:글자|폰트)\s*)?크기(?:를|는|도)?\s*(\d+)\s*(?:px|pt)?(?:으?로)\s*'+END,text)
    if compound:
        size=int(compound[3])
        if not 10<=size<=42:raise ValueError('제목 크기는 10~42로 지정해 주세요.')
        return [ChartEditCommand(operation='set_title',kind='STYLE_EDIT',value=compound[2]),
                ChartEditCommand(operation='set_presentation',kind='STYLE_EDIT',params={'title_size':size})]
    if re.search(r'[;\n]|그리고|바꾸고|변경하고|옮기고',text):return None
    title_size=re.fullmatch(r'제목(?:의)?\s*(?:(?:글자|폰트)\s*)?크기(?:를|는|도)?\s*(\d+)\s*(?:px|pt)?(?:으?로)\s*'+END,text)
    if title_size:
        size=int(title_size[1])
        if not 10<=size<=42:raise ValueError('제목 크기는 10~42로 지정해 주세요.')
        return [ChartEditCommand(operation='set_presentation',kind='STYLE_EDIT',params={'title_size':size})]
    # Attribute requests without an explicit new title must reach the normal
    # interpreter. Otherwise "제목 크기도 24로" becomes the literal title.
    if re.match(r'제목(?:의)?\s*(?:크기|글자|글씨|폰트|색|위치|정렬|두께|높이|너비)',text):return None
    title=re.fullmatch(r'제목(?:을|은)?\s*(.+?)(?:으?로)\s*'+END,text)
    if title:
        value=title[1].strip()
        if re.search(r'(?:크기|폰트|글자).*\d+.*(?:으로|로)',value):return None
        pairs={'"':'"',"'":"'",'‘':'’','“':'”'}
        if len(value)>=2 and pairs.get(value[0])==value[-1]:value=value[1:-1]
        return [ChartEditCommand(operation='set_title',kind='STYLE_EDIT',value=value)]
    period=re.fullmatch(r'(\d{4})년\s*([1-4])분기부터\s*(\d{4})년\s*([1-4])분기까지\s*'+COLOR+r'(?:으?로)\s*강조(?:해줘|해\s*주세요)',text)
    if period and series and all(s.get('frequency')=='Q' for s in series):
        start=f'{period[1]}0{period[2]}';end=f'{period[3]}0{period[4]}'
        periods={p['date'] for s in series for p in s['points']}
        if start not in periods or end not in periods:raise ValueError('강조 범위의 실제 관측 시점을 확인해 주세요.')
        color=COLORS.get(period[5],period[5]);existing=next((h for h in reversed(spec.highlights) if h.start==start and h.end==end),None)
        if existing:return [ChartEditCommand(operation='update_highlight',kind='STYLE_EDIT',value=existing.id,params={'color':color})]
        return [ChartEditCommand(operation='highlight_period',kind='STYLE_EDIT',start=start,end=end,params={'color':color})]
    color=re.fullmatch(r'(?:가장\s*)?최근\s*강조\s*영역(?:의)?\s*색(?:상)?(?:을)?\s*'+COLOR+r'(?:으?로)\s*'+END,text)
    opacity=re.fullmatch(r'(?:가장\s*)?최근\s*강조\s*영역(?:의)?\s*(불투명도|투명도)(?:를|을)?\s*(\d+(?:\.\d+)?)%로\s*'+END,text)
    if color or opacity:
        if not spec.highlights:raise ValueError('먼저 강조할 기간을 지정해 주세요.')
        if color:params={'color':COLORS.get(color[1],color[1])}
        else:
            percent=float(opacity[2])
            if not 0<=percent<=100:raise ValueError('투명도는 0~100%로 지정해 주세요.')
            params={'opacity':percent/100 if opacity[1]=='불투명도' else 1-percent/100}
        return [ChartEditCommand(operation='update_highlight',kind='STYLE_EDIT',value=spec.highlights[-1].id,params=params)]
    line=re.fullmatch(r'(.*?)\s*선\s*색상(?:을)?\s*'+COLOR+r'(?:으?로)\s*'+END,text)
    if line:
        name=re.sub(r'\s+','',line[1]);labels=[s['label'] for s in series]
        target=next((label for label in labels if re.sub(r'\s+','',label)==name),None)
        if not name:target=selected if selected in labels else labels[0] if len(labels)==1 else None
        if target:return [ChartEditCommand(operation='set_series_color',kind='STYLE_EDIT',label=target,value=COLORS.get(line[2],line[2]))]
    return None
