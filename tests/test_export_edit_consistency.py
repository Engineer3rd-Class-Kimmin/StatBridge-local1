"""Actual vector output retains partial edits and note geometry."""
import copy
import io
import pytest
from output_agent import OutputAgent
from output_schema import ChartSpec
from report_export import render_file
from test_advanced_reports import fixture
from workspace_text_edits import simple_workspace_commands


def test_compound_title_and_size_keep_text_boundary():
    commands=simple_workspace_commands('제목을 ‘수출: 가격과 물량’으로 바꾸고 제목 크기를 26으로 해줘.',ChartSpec(),[])
    assert [(c.operation,c.value) for c in commands]==[('set_title','수출: 가격과 물량'),('set_presentation',None)]
    assert commands[1].params=={'title_size':26}


@pytest.mark.parametrize('scope,start,end',[('segment','202002','202004'),('point','202003','202003')])
@pytest.mark.parametrize('kind',['line','bar'])
def test_partial_styles_survive_actual_svg_and_pdf(scope,start,end,kind):
    data=fixture(1);before=copy.deepcopy(data)
    series=OutputAgent()._series(data['execution']['rows']);label=series[0]['label']
    spec=ChartSpec(chart_type=kind,range_styles=[{'scope':scope,'label':label,'start':start,'end':end,'style':{'color':'#ab1245','width':5,'dash':'solid' if kind=='bar' else 'dash'}}])
    svg=render_file(series,spec,data,'svg')
    assert b'#ab1245' in svg
    assert render_file(series,spec,data,'pdf').startswith(b'%PDF')
    assert data==before


def test_non_arrow_note_ignores_arrow_offset_and_is_centered(monkeypatch):
    from matplotlib.axes import Axes
    calls=[];original=Axes.annotate
    def capture(self,text,*args,**kwargs):
        if text=='검증 메모':calls.append(kwargs)
        return original(self,text,*args,**kwargs)
    monkeypatch.setattr(Axes,'annotate',capture)
    data=fixture(1);series=OutputAgent()._series(data['execution']['rows'])
    spec=ChartSpec(chart_type='line',notes=[{'text':'검증 메모','x':.8,'y':.2,'ay':-80,'arrow':False}])
    assert render_file(series,spec,data,'pdf').startswith(b'%PDF')
    assert calls[0]['xy']==(.8,.2) and calls[0]['xytext']==(0,0)
    assert calls[0]['ha']=='center' and calls[0]['va']=='center'
