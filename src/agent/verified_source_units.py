"""Narrow, source-backed unit metadata; never changes API observations.

CCSI has no physical unit. BOK defines its long-run mean as 100 and standard
deviation as 10. '지수' is our documented computational label, not UNIT_NM
claimed to have been returned by KOSIS. Other CSI components are not covered.
The ESI original series has a separate, equally narrow official-definition rule.
"""
import copy

TABLE='DT_511Y002'
ITEM='13103134688999'
CLASSES={'objL1':'13102134688CSI_CD.FME','objL2':'13102134688CSI_CLF_CD.99988'}
BASIS={
    'unit':'지수', 'kind':'dimensionless_standardized_index',
    'basis':'한국은행의 CCSI 산출 정의에 따른 계산용 단위',
    'scale':'장기평균 100, 장기표준편차 10',
    'api_unit_status':'not_supplied', 'checked_on':'2026-10-22',
    'sources':[
        {'publisher':'한국은행','title':'2025년 12월 소비자동향조사 결과',
         'url':'https://www.bok.or.kr/portal/bbs/B0000501/view.do?menuNo=200690&nttId=10095310',
         'pdf_url':'https://www.bok.or.kr/fileSrc/portal/56590ec4ef374f519035e1983f8dc74f/2/705f67aaf5754453812dc43d323720b5.pdf',
         'pdf_page':9,'section':'부록 2 소비자심리지수 개요',
         'sha256':'d6de3f90e0162dd7ff02f6f2a171dca4a81cc9f2705c233811f39926604785eb'},
        {'publisher':'KOSIS','endpoint':'https://kosis.kr/openapi/Param/statisticsParameterData.do',
         'organization_id':'301','table_id':TABLE,'item_id':ITEM,'classifications':CLASSES,
         'frequency':'M','finding':'실제 관측 응답과 ITM 메타데이터에 UNIT_NM이 제공되지 않음'},
    ],
    'limitation':'CCSI의 상대변화율은 표준화된 지수값에 적용한 계산이며 소비심리 자체의 비율 증가나 한국은행의 공식 증감률을 뜻하지 않습니다.',
}


ESI_CLASSES = {'objL1':'13102134473ACC_CD.E1000'}
ESI_BASIS = {
    'unit':'지수', 'kind':'dimensionless_sentiment_index',
    'basis':'한국은행 경제심리지수 공표 정의에 따른 계산용 단위',
    'scale':'한국은행이 공표한 경제심리지수 원계열의 수준',
    'api_unit_status':'not_supplied', 'checked_on':'2026-11-02',
    'sources':[{'publisher':'한국은행', 'title':'2026년 6월 기업경기조사 결과 및 경제심리지수(ESI)',
                'url':'https://www.bok.or.kr/portal/bbs/B0000501/view.do?nttId=11062198&menuNo=201264',
                'finding':'ESI를 지수 수준으로 공표하고 전월 차이를 p로 표기함'}],
    'limitation':'경제심리지수 원계열의 수학적 변환이며, 지수 상대변화율을 공식 경기 증가율로 해석하지 않습니다.',
}


def unit_resolution(source,response):
    profiles = [(TABLE, ITEM, CLASSES, BASIS),
                ('DT_513Y001', '13103134473999', ESI_CLASSES, ESI_BASIS)]
    match = next((p for p in profiles if source.table_id == p[0] and source.item_id == p[1]
                  and source.classifications == p[2]), None)
    if match is None:
        return None
    table, item, classes, basis = match
    if (source.frequency!='M' or response.get('status')!='success'
            or response.get('source')!='kosis_api' or response.get('table_id')!=table):
        return None
    rows=response.get('rows') or []
    if not rows:return None
    for row in rows:
        raw_unit=row.get('UNIT_NM')
        if raw_unit is not None and (not isinstance(raw_unit,str) or raw_unit.strip()):
            return None
        if (str(row.get('ORG_ID'))!='301' or row.get('TBL_ID')!=table
                or row.get('ITM_ID')!=item or row.get('PRD_SE')!='M'
                or any(str(row.get(k.replace('objL','C')))!=v for k,v in classes.items())):
            return None
    return copy.deepcopy(basis)
