"""Bounded, vector-first report export and observation-bound JSON round trips."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import textwrap
from threading import RLock
from uuid import uuid4

import numpy as np
from advanced_chart_spec import fingerprint, validate_report, PALETTE
from advanced_chart_render import aligned, regression, source_caption

EXPORT_LOCK=RLock()  # Matplotlib font/rc state is shared between request threads.


def document(result, spec, chart_id=None):
    from output_agent import OutputAgent
    original=OutputAgent()._series(result.get('execution',{}).get('rows',[]))
    displayed=OutputAgent()._display_series(result,spec)
    from chart_designs import palette
    colors=palette(spec) or (PALETTE if spec.report else None)
    if colors:displayed=[{**s,'color':spec.series_colors.get(s['label'],colors[i%len(colors)])} for i,s in enumerate(displayed)]
    displayed=[{**s,'color':(spec.series_styles[s['label']].color if s['label'] in spec.series_styles and spec.series_styles[s['label']].color else spec.series_colors.get(s['label'],s['color']))} for s in displayed]
    plans=result.get('api_plans') or ([result['api_plan']] if result.get('api_plan') else [])
    return {'schema_version':'statbridge.report/1','chart_id':chart_id or (spec.report.chart_id if spec.report else fingerprint(original)[:24]),
        'dataset_reference':[p['table_id'] for p in plans], 'dataset_version':fingerprint(original),
        'data_transformations':{'transform':spec.transform,'period_start':spec.period_start,'period_end':spec.period_end,'hidden_series':spec.hidden_series,'top_n':spec.top_n},
        'series':displayed,'axes':{k:v.model_dump() for k,v in spec.axes.items()},
        'units':{s['label']:s['unit'] for s in displayed},'title':spec.title,'subtitle':spec.subtitle,
        'legend':{'position':spec.legend_position,'visible':spec.show_legend},
        'annotations':[n.model_dump() for n in spec.notes], 'reference_lines':[g.model_dump() for g in spec.guides],
        'highlighted_regions':[h.model_dump() for h in spec.highlights],
        'forecast_regions':[] if not spec.report or not spec.report.forecast_start else [{'start':spec.report.forecast_start,'description':spec.report.forecast_description,'origin':'user_marked_no_estimation'}],
        'confidence_intervals':[b.model_dump() for b in spec.report.intervals] if spec.report else [],
        'panels':{'columns':spec.report.columns if spec.report else 1,'layout':spec.layout},
        'typography':spec.presentation.model_dump(),'theme':spec.presentation.design_template or (spec.report.template if spec.report else 'legacy'),
        'source':copy.deepcopy(result.get('execution',{}).get('sources',[])),
        'footnotes':spec.report.footnotes if spec.report else [],'export_settings':{'width':spec.report.width,'height':spec.report.height,'dpi':spec.report.dpi} if spec.report else {},
        'state':spec.model_dump()}


def restore_document(result, payload):
    from output_schema import ChartSpec
    allowed=set(document(result,ChartSpec()))
    if set(payload)!=allowed or payload.get('schema_version')!='statbridge.report/1':
        raise ValueError('지원되지 않거나 필드가 누락된 보고서 JSON 버전입니다.')
    payload=copy.deepcopy(payload)
    for field in ('typography',):
        payload[field].setdefault('latest_values',False)
        payload[field].setdefault('publication_yoy',False)
        payload[field].setdefault('publication_axes',False)
    payload['state'].setdefault('presentation',{}).setdefault('latest_values',False)
    payload['state']['presentation'].setdefault('publication_yoy',False)
    payload['state']['presentation'].setdefault('publication_axes',False)
    # Additive optional presentation field for documents saved before designs.
    if 'design_template' not in payload.get('typography',{}) and 'design_template' not in payload.get('state',{}).get('presentation',{}):
        payload['typography']['design_template']=None
        payload['state']['presentation']['design_template']=None
    table_options=payload['state'].get('report_table')
    if isinstance(table_options,dict):
        table_options.setdefault('template','default')
        table_options.setdefault('emphasized_labels',[])
    spec=ChartSpec.model_validate(payload['state'])
    # Older observation-bound documents predate the optional report table settings.
    payload['state'].setdefault('report_table', None)
    rebuilt=document(result,spec,payload['chart_id'])
    # Neither imported data nor redundant metadata may silently replace observations.
    if rebuilt!=payload:
        raise ValueError('JSON의 데이터 버전·출처·표시 수치·명세가 현재 조회 결과와 일치하지 않습니다.')
    return spec


def migrate_legacy(result, legacy):
    from output_schema import ChartSpec
    return document(result,ChartSpec.model_validate(legacy))


def _font():
    import matplotlib.font_manager as fm
    candidates=['C:/Windows/Fonts/malgun.ttf','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    for p in candidates:
        if Path(p).exists(): return fm.FontProperties(fname=p)
    for p in fm.findSystemFonts():
        if any(t in p.lower() for t in ('notosanscjk','nanumgothic','malgun')): return fm.FontProperties(fname=p)
    raise ValueError('인쇄 출력용 한글 폰트가 없습니다. 맑은 고딕 또는 Noto Sans CJK를 설치해 주세요.')


def export_preflight(series,spec,result,format):
    if spec.presentation.publication_yoy or spec.presentation.publication_axes:
        raise ValueError('양쪽 동일 눈금 그래프는 화면과 동일한 Plotly PNG 또는 CSV로 내보내 주세요.')
    from chart_designs import materialize,palette
    spec=materialize(spec,[s['label'] for s in series])
    colors=palette(spec) or PALETTE
    if format not in {'png','svg','pdf'}: raise ValueError('지원하는 출력 형식은 PNG·SVG·PDF입니다.')
    legacy=spec.chart_type if not spec.report else None
    if not spec.report:
        from advanced_chart_spec import ReportOptions
        kinds={'auto':'forecast','line':'forecast','bar':'forecast','area':'forecast','stacked_bar':'contribution','waterfall':'waterfall','scatter':'regression','bubble':'bubble','heatmap':'heatmap'}
        if legacy not in kinds:raise ValueError('현재 종류의 벡터 인쇄는 아직 지원하지 않습니다. 화면 PNG 저장을 사용하세요.')
        right=spec.secondary_axis_series
        if right and (spec.layout!='combined' or any(spec.series_chart_types.get(label,spec.chart_type) not in {'line','bar','area'} for label in right)):
            raise ValueError('복합축 인쇄는 선·막대·영역 배치만 지원합니다.')
        columns=min(4,len(series)) if spec.layout=='horizontal' else spec.presentation.panel_columns if spec.layout=='grid' else 1
        rows=math_ceil(len(series)/columns) if spec.layout!='combined' else 1
        height=spec.presentation.height or min(2000,max(720,rows*320+280))
        spec=spec.model_copy(update={'report':ReportOptions(kind='dual_axis' if right else 'panels' if spec.layout!='combined' else kinds[legacy],right_axis=right,columns=columns,height=height,contribution_confirmed=legacy=='stacked_bar'),
            'secondary_axis_series':[], 'series_chart_types':{**spec.series_chart_types,**{label:spec.series_chart_types.get(label,spec.chart_type) for label in right}}})
    if spec.presentation.width is not None and spec.presentation.height is not None:
        spec=spec.model_copy(update={'report':spec.report.model_copy(update={'width':spec.presentation.width,'height':spec.presentation.height,'dpi':100})})
    o=spec.report
    from output_dimensions import validate_output_dimensions
    validate_output_dimensions(o.width,o.height)
    periods=validate_report(series,o)
    if o.kind=='dual_axis' and set(spec.secondary_axis_series)==set(o.right_axis):
        # The editor mirrors report assignments for its controls. Consume the
        # report assignment once without treating that mirror as a conflict.
        spec=spec.model_copy(update={'secondary_axis_series':[]})
    period_index={d:i for i,d in enumerate(periods)}
    if spec.presentation.bar_orientation=='horizontal' or any(kind not in {'line','bar','area'} for kind in spec.series_chart_types.values()) or spec.secondary_axis_series:
        raise ValueError('기존 가로 막대·혼합 종류·보조축 설정은 보고서 인쇄와 함께 사용할 수 없습니다. 보고서의 종류·right_axis 설정을 사용하세요.')
    if o.kind in {'regression','bubble','violin'} and (spec.guides or spec.reference_lines or spec.highlights or spec.annotations or spec.notes):
        raise ValueError('비시계열 인쇄 그래프에 기존 시계열 위치 주석을 재해석하지 않습니다. 해당 주석을 해제해 주세요.')
    if spec.range_styles and o.kind in {'regression','bubble','violin','heatmap','waterfall','stacked_area'}:
        raise ValueError('이 인쇄 그래프 종류는 구간 스타일을 지원하지 않습니다. 선·막대·영역 그래프를 사용해 주세요.')
    if any(s.line_shape not in {None,'linear'} for s in spec.series_styles.values()) or any(s.style.line_shape not in {None,'linear'} for s in spec.range_styles):
        raise ValueError('인쇄 렌더러는 비선형 연결을 지원하지 않습니다. Plotly PNG 출력 또는 해당 스타일 해제를 사용하세요.')
    if spec.paper_shapes and any(s.data_anchor for s in spec.paper_shapes):
        raise ValueError('데이터 좌표에 고정된 강조 도형은 현재 인쇄 렌더러가 지원하지 않습니다.')
    if o.width*o.height*(o.dpi/100)**2>32_000_000:
        raise ValueError('PNG 출력 크기가 32메가픽셀 제한을 초과합니다. 해상도를 낮춰 주세요.')
    _print_layout(series,spec,result)
    return spec,legacy,periods,colors


def render_file(series,spec,result,format):
    spec,legacy,periods,colors=export_preflight(series,spec,result,format)
    period_index={d:i for i,d in enumerate(periods)}
    from advanced_chart_spec import PALETTE
    return _render_validated(series,spec,result,format,legacy,periods,colors,period_index)


def export_formats(series,spec,result):
    formats=['png','csv']
    try:
        export_preflight(series,spec,result,'svg')
    except (ValueError,TypeError):
        return formats
    return [*formats,'svg','pdf']


def _render_validated(series,spec,result,format,legacy,periods,colors,period_index):
    o=spec.report
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import ticker, patches
    from PIL import Image
    from chart_stickers import sticker_asset,ASSET_ROOT
    font=_font()
    with EXPORT_LOCK,matplotlib.rc_context({'font.family':font.get_name(),'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none' if o.svg_text_mode=='text' else 'path','axes.unicode_minus':False}):
        count=len(series) if o.kind=='panels' else 1
        columns=min(o.columns,count);rows=(count+columns-1)//columns
        fig,axes=plt.subplots(rows,columns,figsize=(o.width/100,o.height/100),squeeze=False)
        primary=axes.flat[0];secondary=primary.twinx() if o.kind=='dual_axis' else None
        bottom,title_lines,subtitle_lines,subtitle_y,legend_labels,legend_columns,legend_rows,max_lines,legend_height,legend_top,top,lines=_print_layout(series,spec,result)
        fig.subplots_adjust(left=max(.1,(85+(spec.axes["y"].title_standoff if "y" in spec.axes else 0))/o.width),right=1-max(.1,(85+(spec.axes["y2"].title_standoff if "y2" in spec.axes else 0))/o.width),top=top,bottom=bottom,wspace=.3,hspace=.5)
        def color(i,s):return (spec.series_styles[s['label']].color if s['label'] in spec.series_styles and spec.series_styles[s['label']].color else spec.series_colors.get(s['label'],colors[i%len(colors)]))
        def target(i,s):return axes.flat[i] if o.kind=='panels' else secondary if secondary and s['label'] in o.right_axis else primary
        try:
            if o.kind in {'regression','bubble'}:
                dates,values=aligned(series)
                if not dates: raise ValueError('같은 시점의 관측 쌍이 없습니다.')
                if o.kind=='bubble' and (min(values[2])<0 or max(values[2])<=0): raise ValueError('버블 크기는 양수 관측값이 필요합니다.')
                size=35 if o.kind=='regression' else np.asarray(values[2])/max(values[2])*600
                primary.scatter(values[0],values[1],s=size,color=PALETTE[0],alpha=.75)
                primary.set_xlabel(f"{series[0]['label']} ({series[0]['unit']})",fontproperties=font)
                primary.set_ylabel(f"{series[1]['label']} ({series[1]['unit']})",fontproperties=font)
                if o.kind=='regression':
                    fit=regression(series);primary.plot(fit['x'],fit['y'],'--',color='#555555',label=f"OLS n={fit['n']}")
            elif o.kind=='heatmap':
                matrix=np.asarray([[next((p['value'] for p in s['points'] if p['date']==d),np.nan) for d in periods] for s in series])
                image=primary.imshow(np.ma.masked_invalid(matrix),aspect='auto',cmap='Blues')
                fig.colorbar(image,ax=primary,shrink=.8)
                primary.set_yticks(range(len(series)),[s['label'] for s in series],fontproperties=font)
            elif o.kind=='violin':
                values=[[p['value'] for p in s['points']] for s in series]
                if any(len(v)<2 or len(set(v))<2 for v in values): raise ValueError('바이올린 밀도에는 서로 다른 관측값이 2개 이상 필요합니다.')
                primary.violinplot(values,showmeans=True,showextrema=True)
                primary.boxplot(values,widths=.1)
                primary.set_xticks(range(1,len(series)+1),[s['label'] for s in series],fontproperties=font)
            elif o.kind=='waterfall':
                if len(series)!=1: raise ValueError('워터폴에는 한 계열이 필요합니다.')
                vals=np.asarray([p['value'] for p in series[0]['points']]);previous=np.r_[0,vals[:-1]]
                primary.bar(range(len(vals)),abs(vals-previous),bottom=np.minimum(vals,previous),color=['#2255a4' if b>=a else '#c64949' for a,b in zip(previous,vals)])
                primary.plot(range(len(vals)),vals,'o',ms=3,color='#263247')
            else:
                positive=np.zeros(len(periods));negative=positive.copy()
                bar_bases={};bar_tops={}
                for i,s in enumerate(series):
                    from snapshot_chart_style import legend_name
                    ax=target(i,s);x=[period_index[p['date']] for p in s['points']];y=np.asarray([p['value'] for p in s['points']]);name=legend_name(s,series,spec)
                    local_kind=spec.series_chart_types.get(s['label'],'stacked_bar' if o.kind=='contribution' else 'bar' if o.kind=='dual_axis' and s['label'] in o.right_axis else legacy or 'line')
                    if local_kind=='bar':
                        style=spec.series_styles.get(s['label']);alpha=style.opacity if style and style.opacity is not None else 1
                        if spec.presentation.bar_mode in {'stack','relative'}:
                            bottoms=[]
                            for date,value in zip(x,y):
                                key=(id(ax),date,value>=0);bottom=bar_bases.get(key,0);bottoms.append(bottom);bar_bases[key]=bottom+value;bar_tops[(s['label'],date)]=bottom+value
                            bars_out=ax.bar(x,y,bottom=bottoms,width=(1-spec.presentation.bar_gap)*(1-spec.presentation.bar_group_gap),label=name,color=color(i,s),alpha=alpha)
                        else:
                            bars=[j for j,item in enumerate(series) if target(j,item) is ax and spec.series_chart_types.get(item['label'],'bar' if o.kind=='dual_axis' and item['label'] in o.right_axis else legacy or 'line')=='bar']
                            slot=(1-spec.presentation.bar_gap)/max(1,len(bars));width=slot*(1-spec.presentation.bar_group_gap);offset=(bars.index(i)-(len(bars)-1)/2)*slot
                            bars_out=ax.bar(np.asarray(x)+offset,y,width=width,label=name,color=color(i,s),alpha=alpha)
                        _style_print_bars(bars_out,s,spec)
                    elif o.kind=='contribution' and local_kind not in {'line','area'}:
                        base=np.where(y>=0,positive,negative)
                        ax.bar(x,y,bottom=base,width=(1-spec.presentation.bar_gap)*(1-spec.presentation.bar_group_gap),label=name,color=color(i,s));positive+=np.maximum(y,0);negative+=np.minimum(y,0)
                    elif o.kind=='stacked_area': pass
                    else:
                        if local_kind=='area':ax.fill_between(x,y,alpha=.15,color=color(i,s),zorder=0)
                        if any(rule.label==s['label'] for rule in spec.range_styles):
                            _draw_styled_series(ax,s,spec,period_index,name)
                            if o.kind=='panels':ax.set_title(name,fontproperties=font,fontsize=11)
                            continue
                        style=spec.series_styles.get(s['label']);dash=spec.series_dashes.get(s['label'],style.dash if style and style.dash else 'solid')
                        ax.plot(x,y,label=name,color=color(i,s),linewidth=style.width if style and style.width else spec.line_width if spec.presentation.design_template=='snapshot' else spec.line_width/1.5,
                            linestyle={'solid':'-','dash':'--','dot':':','dashdot':'-.','longdash':(0,(6,2))}.get(dash,'-'),marker={'circle':'o','square':'s','diamond':'D','cross':'+','triangle-up':'^','triangle-down':'v'}.get(style.marker_symbol,'o') if style and style.markers else None,
                            alpha=style.opacity if style and style.opacity is not None else 1)
                    if o.kind=='panels': ax.set_title(name,fontproperties=font,fontsize=11)
                # These labels are display text read from the current observations.
                # No value, unit, date or axis range is copied from a reference.
                if spec.presentation.latest_values and o.kind in {'forecast','panels','dual_axis','events','fan','scenario'} and legacy not in {'stacked_bar'}:
                    from snapshot_chart_style import spaced_offsets
                    fig.canvas.draw()
                    groups={}
                    for i,s in enumerate(series):
                        if not s['points']:continue
                        ax=target(i,s);last=s['points'][-1];anchor=bar_tops.get((s['label'],period_index[last['date']]),last['value']);position=ax.transData.transform((period_index[last['date']],anchor))[1]
                        bounds=tuple(round(v,1) for v in ax.get_position().bounds)
                        groups.setdefault(bounds,[]).append((i,position))
                    offsets={key:spaced_offsets(entries,(spec.presentation.font_size+6)*fig.dpi/72) for key,entries in groups.items()}
                    for i,s in enumerate(series):
                        if not s['points']:continue
                        ax=target(i,s);last=s['points'][-1];bounds=tuple(round(v,1) for v in ax.get_position().bounds)
                        anchor=bar_tops.get((s['label'],period_index[last['date']]),last['value'])
                        ax.annotate(f"{last['value']:,.{spec.presentation.number_decimals}f}",(period_index[last['date']],anchor),xytext=(6,offsets[bounds][i]*72/fig.dpi),textcoords='offset points',fontproperties=font,fontsize=spec.presentation.font_size,color=color(i,s),va='center',clip_on=False)
                if o.kind=='stacked_area': primary.stackplot(range(len(periods)),*[ [p['value'] for p in s['points']] for s in series],labels=[s['label'] for s in series],colors=[color(i,s) for i,s in enumerate(series)])
            for band in sorted(o.intervals,key=lambda b:-b.level):
                primary.fill_between([period_index[d] for d in band.periods],band.lower,band.upper,color=PALETTE[0],alpha=.15,label=f'{band.level:.0%} 구간',zorder=0)
            for i,ax in enumerate(axes.flat):
                if i>=count: ax.remove();continue
                if o.kind not in {'regression','bubble','violin'}:
                    limit=max(3,8//columns) if o.kind=='panels' else 8
                    step=max(1,math_ceil((len(periods)-1)/max(1,limit-1)));ticks=list(range(0,len(periods),step))
                    if len(periods)-1 not in ticks:ticks.append(len(periods)-1)
                    from advanced_chart_render import period_text
                    ax.set_xticks(ticks,[period_text(periods[j],series[i if o.kind=='panels' else 0].get('frequency'),o.date_format) for j in ticks],fontsize=spec.presentation.font_size*.8,fontproperties=font)
                if o.kind not in {'regression','bubble','heatmap'}:
                    ax.set_ylabel(series[i]['unit'] if o.kind=='panels' else next(s['unit'] for s in series if s['label'] not in o.right_axis),fontproperties=font)
                ax.tick_params(labelsize=spec.presentation.font_size*.72)
                ax.spines[['top','right']].set_visible(False)
                ax.grid(axis='y',color=spec.presentation.grid_color,linewidth=.6);ax.set_axisbelow(True)
                if spec.x_axis_label:ax.set_xlabel(spec.x_axis_label,fontproperties=font)
                elif o.kind not in {'regression','bubble','violin'}:ax.set_xlabel('시점',fontproperties=font)
                if spec.y_axis_label:ax.set_ylabel(spec.y_axis_label,fontproperties=font)
                xstyle=spec.axes.get('x')
                if xstyle:
                    # Matplotlib turns a disabled grid back on when style kwargs
                    # accompany visible=False. Keep the disabling call separate.
                    if xstyle.show_grid:ax.grid(True,axis='x',color=spec.presentation.grid_color,linewidth=.6)
                    else:ax.grid(False,axis='x')
                    ax.tick_params(axis='x',labelrotation=xstyle.tick_angle)
                    if xstyle.reverse:ax.invert_xaxis()
                axis=spec.axes.get('y')
                if axis:
                    if axis.show_grid:ax.grid(True,axis='y',color=spec.presentation.grid_color,linewidth=.6)
                    else:ax.grid(False,axis='y')
                    if axis.minimum is not None:ax.set_ylim(axis.minimum,axis.maximum)
                    if axis.scale=='log':
                        if any(p['value']<=0 for s in series for p in s['points']):raise ValueError('로그축에는 양수 데이터만 사용할 수 있습니다.')
                        ax.set_yscale('log')
                    if axis.reverse:ax.invert_yaxis()
                    if axis.decimals is not None:ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:.'+str(axis.decimals)+'f}'))
                if o.forecast_start:ax.axvspan(periods.index(o.forecast_start)-.5,len(periods)-.5,color='#dce2e9',alpha=.35,zorder=0)
                for e in o.events:
                    ax.axvline(periods.index(e.period),color='#777777',ls=':',lw=1);ax.text(periods.index(e.period),1.01,e.text,transform=ax.get_xaxis_transform(),fontproperties=font,fontsize=9)
            if secondary:
                primary.set_zorder(secondary.get_zorder()+1);primary.patch.set_visible(False)
                secondary.set_ylabel(next(s['unit'] for s in series if s['label'] in o.right_axis),fontproperties=font)
                if spec.presentation.design_template=='snapshot':
                    secondary.spines[['top','left','bottom']].set_visible(False)
                    secondary.spines['right'].set_color('#d3d9df');secondary.spines['right'].set_linewidth(.7)
                    secondary.tick_params(labelsize=spec.presentation.font_size*.8,colors=spec.presentation.font_color)
                    secondary.grid(False)
                    primary.set_zorder(secondary.get_zorder()+1);primary.patch.set_visible(False)
                axis=spec.axes.get('y2')
                if axis and axis.minimum is not None:secondary.set_ylim(axis.minimum,axis.maximum)
            from synchronized_axes import synchronized_scale
            sync=synchronized_scale(series,spec)
            if secondary and sync:
                bounds,step,reverse=sync
                for ax in [primary,secondary]:
                    ax.set_ylim(list(reversed(bounds)) if reverse else bounds)
                    ax.yaxis.set_major_locator(ticker.MultipleLocator(step))
            # Position titles in axes coordinates; retain independent left/right units.
            for name,style in spec.axes.items():
                targets=[secondary] if name=='y2' and secondary else list(axes.flat) if name in {'x','y'} else []
                for ax in targets:
                    horizontal=name=='x';obj=ax.xaxis if horizontal else ax.yaxis
                    if style.title is not None:obj.label.set_text(style.title)
                    if style.title_position is not None or style.title_standoff or style.title_orientation!='auto' or style.title_placement!='side':
                        pos=style.title_position if style.title_position is not None else .5
                        size=(ax.get_position().height*o.height if horizontal else ax.get_position().width*o.width)
                        shift=(60+style.title_standoff)/max(1,size)
                        if horizontal:obj.set_label_coords(pos,-shift);obj.label.set_ha('left' if pos==0 else 'right' if pos==1 else 'center')
                        else:
                            right=name=='y2'
                            obj.label.set_rotation(0 if style.title_orientation=='horizontal' or (style.title_orientation=='auto' and style.title_placement!='side') else 270 if right else 90)
                            if style.title_placement=='side':obj.set_label_coords(1+shift if right else -shift,pos)
                            else:
                                x=style.title_x if style.title_placement=='custom' else (1 if right else 0)
                                y=style.title_y if style.title_placement=='custom' else (1 if style.title_placement=='top' else 0)
                                obj.set_label_coords(x,y+(18+style.title_standoff)/max(1,ax.get_position().height*o.height)*( -1 if style.title_placement=='bottom' else 1))
                                obj.label.set_ha('right' if right else 'left');obj.label.set_va('bottom' if style.title_placement!='bottom' else 'top')
            for g in spec.guides:
                if g.orientation=='horizontal':primary.axhline(float(g.value),color=g.color,ls='--',lw=g.width)
                elif str(g.value) in periods:primary.axvline(periods.index(str(g.value)),color=g.color,ls='--',lw=g.width)
            for v in spec.reference_lines:primary.axhline(v,color='#777777',ls='--',lw=1)
            for h in spec.highlights:
                if h.start in periods and h.end in periods:primary.axvspan(periods.index(h.start)-.5,periods.index(h.end)+.5,color=h.color,alpha=h.opacity,zorder=0)
            for note in spec.notes:
                if note.placement == "below":continue
                xy=(note.x,note.y);coords='axes fraction';target_ax=primary
                if note.label and note.period:
                    s=next((s for s in series if s['label']==note.label),None)
                    if s:
                        point=next((p for p in s['points'] if p['date']==note.period),None)
                        if point:xy=(periods.index(note.period),point['value']);coords='data';target_ax=target(series.index(s),s)
                offset=(note.x_shift+note.ax,note.y_shift-note.ay) if note.arrow else (note.x_shift,note.y_shift)
                target_ax.annotate(note.text,xy=xy,xycoords=coords,xytext=offset,textcoords='offset pixels',fontproperties=font,fontsize=note.font_size*.72,color=note.color,ha='center',va='center',alpha=note.opacity,
                    arrowprops={'arrowstyle':'->','color':note.color,'lw':note.arrow_width} if note.arrow else None)
            for annotation in spec.annotations:
                if annotation.period in periods:primary.text(periods.index(annotation.period),1.01,annotation.text,transform=primary.get_xaxis_transform(),fontproperties=font,fontsize=9)
            for shape in spec.paper_shapes:
                rect=patches.Rectangle((shape.x0,shape.y0),shape.x1-shape.x0,shape.y1-shape.y0) if shape.type=='rect' else patches.Ellipse(((shape.x0+shape.x1)/2,(shape.y0+shape.y1)/2),abs(shape.x1-shape.x0),abs(shape.y1-shape.y0))
                if shape.type=='line':primary.plot([shape.x0,shape.x1],[shape.y0,shape.y1],transform=primary.transAxes,color=shape.color)
                else:
                    rect.set(transform=primary.transAxes,facecolor=shape.fill,edgecolor=shape.color,alpha=shape.opacity);primary.add_patch(rect)
            for s in spec.stickers:
                asset=sticker_asset(s.asset_id);image=Image.open(ASSET_ROOT/asset['file']).convert('RGBA')
                side=math_ceil((image.width**2+image.height**2)**.5)
                padded=Image.new('RGBA',(side,side));padded.alpha_composite(image,((side-image.width)//2,(side-image.height)//2));padded=padded.rotate(-s.angle)
                fig.canvas.draw();bounds=primary.get_window_extent();w=s.size*min(bounds.width,bounds.height)/bounds.width;h=s.size*min(bounds.width,bounds.height)/bounds.height
                limits=(primary.get_xlim(),primary.get_ylim())
                primary.imshow(padded,extent=(s.x-w/2,s.x+w/2,s.y-h/2,s.y+h/2),transform=primary.transAxes,aspect='auto',alpha=s.opacity,zorder=10)
                primary.set_xlim(limits[0]);primary.set_ylim(limits[1])
            handles=[];labels=[]
            for axis in axes.flat:
                h,l=axis.get_legend_handles_labels()
                for handle,label in zip(h,l):
                    if label not in labels:handles.append(handle);labels.append(label)
            if secondary:
                h,l=secondary.get_legend_handles_labels();handles+=h;labels+=l
            legend_anchors={'top':('upper center',(.5,legend_top)),'bottom':('upper center',(.5,bottom-.07)),
                'left':('center left',(.01,.5)),'right':('center right',(.99,.5)),
                'top-left':('upper left',(.1,.82)),'top-right':('upper right',(.9,.82)),
                'bottom-left':('lower left',(.1,bottom)),'bottom-right':('lower right',(.9,bottom))}
            loc,anchor=legend_anchors[spec.legend_position]
            font.set_size(spec.presentation.font_size*.72)
            if spec.show_legend and handles:fig.legend(handles,['\n'.join(textwrap.wrap(label,24)) for label in labels],loc=loc,bbox_to_anchor=anchor,ncol=legend_columns if spec.legend_position in {'top','bottom'} else 1,prop=font,frameon=False)
            fig.suptitle('\n'.join(title_lines),y=.98,va='top',fontproperties=font,fontsize=spec.presentation.title_size*.72,color='#173b70')
            if spec.subtitle:fig.text(.5,subtitle_y,'\n'.join(subtitle_lines),ha='center',va='top',fontproperties=font,fontsize=10)
            fig.text(.06,.04,'\n'.join(lines),fontproperties=font,fontsize=9,color='#4b5563',va='bottom')
            buffer=io.BytesIO();fig.savefig(buffer,format=format,dpi=o.dpi,facecolor='white',metadata={'Creator':'StatBridge (user-generated chart)'} if format in {'pdf','svg'} else None)
            return buffer.getvalue()
        finally:plt.close(fig)


def math_ceil(value):
    import math
    return math.ceil(value)


def _draw_styled_series(ax,item,spec,period_index,name):
    """Consume the same edge/point styles as the interactive Plotly renderer."""
    from research_plotly_styles import cartesian_traces
    dashes={'solid':'-','dash':'--','dot':':','dashdot':'-.','longdash':(0,(6,2))}
    markers={'circle':'o','square':'s','diamond':'D','cross':'+','triangle-up':'^','triangle-down':'v'}
    for trace in cartesian_traces(item,spec,'line'):
        x=[period_index[d] for d in trace.x];y=list(trace.y)
        label=name if trace.showlegend is not False else '_nolegend_'
        if 'lines' in trace.mode:
            ax.plot(x,y,color=trace.line.color,linewidth=trace.line.width,
                    linestyle=dashes.get(trace.line.dash,'-'),alpha=trace.opacity,label=label)
            label='_nolegend_'
        if 'markers' in trace.mode:
            for j,(xp,yp) in enumerate(zip(x,y)):
                size=trace.marker.size[j] if isinstance(trace.marker.size,(tuple,list)) else trace.marker.size
                color=trace.marker.color[j] if isinstance(trace.marker.color,(tuple,list)) else trace.marker.color
                symbol=trace.marker.symbol[j] if isinstance(trace.marker.symbol,(tuple,list)) else trace.marker.symbol
                if size:
                    if color and color.startswith('rgba('):
                        values=[float(v) for v in color[5:-1].split(',')];color=(*[v/255 for v in values[:3]],values[3])
                    ax.scatter([xp],[yp],s=size**2,color=color,marker=markers.get(symbol,'o'),label=label)
                    label='_nolegend_'
        if 'text' in trace.mode and trace.text:
            for xp,yp,text in zip(x,y,trace.text):
                if text:ax.annotate(text,(xp,yp),xytext=(0,8),textcoords='offset points',ha='center')


def _style_print_bars(bars,item,spec):
    from research_plotly_styles import style_for
    if not any(rule.label==item['label'] for rule in spec.range_styles):return
    for bar,point in zip(bars,item['points']):
        style=style_for(spec,item,period=point['date'])
        bar.set_facecolor(style['color']);bar.set_alpha(style['opacity'])
        if any(r.label==item['label'] and r.start<=point['date']<=r.end and r.style.width is not None for r in spec.range_styles):
            bar.set_edgecolor(style['color']);bar.set_linewidth(style['width'])


def _print_layout(series,spec,result):
    o=spec.report
    count=len(series) if o.kind=='panels' else 1
    columns=min(o.columns,count);rows=(count+columns-1)//columns
    footer=[*(n.text for n in spec.notes if n.placement=="below"),source_caption(result,o),*o.footnotes]
    footer.extend(f'{b.label} {b.level:.0%}: {b.method}; {b.source}' for b in o.intervals)
    lines=[line for text in footer for line in textwrap.wrap(text,max(35,int(o.width/13)-12))]
    bottom=max(.15,len(lines)*.18/(o.height/100)+.08)
    if spec.axes.get("x") and (spec.axes["x"].title_position is not None or spec.axes["x"].title_standoff):
        bottom=max(bottom,(110+spec.axes["x"].title_standoff+len(lines)*18)/o.height)
    if bottom>.42:
        raise ValueError('출처·각주가 출력 높이에 비해 너무 깁니다. 내용을 줄이거나 높이를 늘려 주세요.')
    title_lines=textwrap.wrap(spec.title,50) or ['']
    subtitle_lines=textwrap.wrap(spec.subtitle,70)
    physical_height=o.height/100
    subtitle_y=.97-len(title_lines)*spec.presentation.title_size*.72/72*1.35/physical_height-.025
    legend_top=subtitle_y-len(subtitle_lines)*10/72*1.35/physical_height-.03
    legend_labels=[spec.display_names.get(s['label'],s['label']) for s in series]
    legend_columns=min(3,len(legend_labels)) if not any(len(label)>18 for label in legend_labels) else min(2,len(legend_labels))
    legend_rows=math_ceil(len(legend_labels)/legend_columns) if legend_columns else 0
    max_lines=max((len(textwrap.wrap(label,24)) for label in legend_labels),default=1)
    legend_height=legend_rows*max_lines*spec.presentation.font_size*.72/72*1.4/physical_height if spec.show_legend else 0
    top=legend_top-legend_height-.04
    if top-bottom<.25 or (top-bottom)*o.height/rows<140:
        raise ValueError('제목·범례·패널이 출력 높이에 비해 너무 많습니다. 높이를 늘리거나 제목·범례를 줄여 주세요.')
    return bottom,title_lines,subtitle_lines,subtitle_y,legend_labels,legend_columns,legend_rows,max_lines,legend_height,legend_top,top,lines
