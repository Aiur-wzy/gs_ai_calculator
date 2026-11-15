"""Independent exporter structure and lossless source relocation checks."""
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from genshin_app.export import _sheet_xml, _write_xlsx, readable_equipment_rows

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def test_multiple_structured_sources_restore_individually_by_pointer_and_ordinal():
    first=json.dumps({'中文':'🧪甲'*501,'n':1.23456789},ensure_ascii=False)
    second=json.dumps([{'source':'长依据'*221},False,0],ensure_ascii=False)
    rows=[['角色','项目','属性','值','源1','源2'],['角色甲','武器','参数',123.456,first,second]]
    shown,details=readable_equipment_rows(rows)
    recovered={}
    for row in details[1:]:recovered.setdefault(row[3],[]).append((row[4],row[2]))
    assert set(recovered)=={'角色与装备!E2','角色与装备!F2'}
    for pointer,original in [('角色与装备!E2',first),('角色与装备!F2',second)]:
        parts=sorted(recovered[pointer])
        assert [n for n,_ in parts]==list(range(1,len(parts)+1))
        assert ''.join(part for _,part in parts)==original
    assert shown[1][:4]==rows[1][:4]
    assert rows[1][4:]==[first,second]


def test_nonstructured_long_text_and_short_json_stay_unchanged():
    source='普通引用与说明'*100
    rows=[['角色甲','属性','值',source,'{"short":true}','{broken'+('x'*200)]]
    shown,details=readable_equipment_rows(rows)
    assert shown==rows
    assert len(details)==1


def test_relocated_context_retains_input_fingerprint_in_primary_view():
    fp='a'*64
    source=json.dumps({'input_fingerprint':fp,'facts':'中文依据'*300},ensure_ascii=False)
    rows=[['角色甲','计算上下文','完整输入','说明',source]]
    shown,details=readable_equipment_rows(rows)
    assert fp in shown[0][4] and '装备资料' in shown[0][4]
    assert ''.join(row[2] for row in details[1:])==source


def test_numeric_boolean_and_literal_formula_like_text_are_not_stringified_or_executed():
    values=[3453550.9695627615,-.00000001234,0.0,90,True,False,'=1+1','00123',' text ']
    root=ET.fromstring(_sheet_xml([['列']*len(values),values]))
    cells=root.findall('.//s:row[@r="2"]/s:c',NS)
    assert [float(c.find('s:v',NS).text) for c in cells[:4]]==values[:4]
    assert all('t' not in c.attrib for c in cells[:4])
    assert cells[1].attrib['s']=='5'
    assert [(c.attrib['t'],c.find('s:v',NS).text) for c in cells[4:6]]==[('b','1'),('b','0')]
    assert all(c.attrib['t']=='inlineStr' and c.find('s:f',NS) is None for c in cells[6:])
    assert ''.join(cells[-1].find('s:is',NS).itertext())==' text '


def test_written_workbook_uses_full_precision_values_and_resolvable_styles():
    buffer=io.BytesIO()
    _write_xlsx(buffer,[('汇总',[['总伤',3453550.9695627615]]),
                        ('共享资源流水',[['次数'],[1]])])
    with zipfile.ZipFile(buffer) as archive:
        styles=ET.fromstring(archive.read('xl/styles.xml'))
        xfs=styles.find('s:cellXfs',NS)
        fmt={f.attrib['numFmtId']:f.attrib['formatCode'] for f in styles.findall('s:numFmts/s:numFmt',NS)}
        summary=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cell=summary.find('.//s:c[@r="B1"]',NS)
        assert cell.find('s:v',NS).text==repr(3453550.9695627615)
        assert fmt[xfs[int(cell.attrib['s'])].attrib['numFmtId']]=='#,##0.0000'
        pool=ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
        assert pool.find('s:sheetProtection',NS) is not None
        assert all(0<int(c.attrib.get('s','0'))<len(xfs) for c in pool.findall('.//s:c',NS))


def test_column_dimensions_cjk_wrapping_and_frozen_header_remain_bounded():
    rows=[['角色','原文']]+[['甲','中文依据'*120] for _ in range(30)]
    root=ET.fromstring(_sheet_xml(rows,name='装备资料'))
    assert len(root.findall('s:cols/s:col',NS))==2
    assert root.find('s:sheetViews/s:sheetView/s:pane',NS).attrib['topLeftCell']=='A2'
    assert all(20<=float(row.attrib['ht'])<=409 for row in root.findall('s:sheetData/s:row',NS))
