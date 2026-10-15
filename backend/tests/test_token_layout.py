from pathlib import Path
import pytest
import pymupdf as fitz
from PIL import Image, ImageChops
from backend.app.services import pdf, pipeline
from backend.app.models import Session,Document,Detection,uid


def fixture(tmp_path):
    with fitz.open() as source:
        page=source.new_page(width=595,height=420)
        page.insert_text((40,80),'Order: 123456789012',fontsize=9,fontname='helv')
        page.insert_text((40,90.8),'Invoice: 2026123456789012',fontsize=9,fontname='helv')
        page.insert_text((40,150),'WWWWWWWWW1234567890',fontsize=12,fontname='hebo',color=(.2,.3,.4))
        raw=source.tobytes()
    session=Session();did=uid();info=pdf.canonicalize(raw,tmp_path/f'{did}.pdf')
    session.documents=[Document(did,'synthetic.pdf',info)]
    return session,info[0]


def add_selection(session,info,text,kind='OTHER'):
    chars=info['native_text'];full=''.join(c['text'] for c in chars);i=full.index(text)
    bbox,rects,value=pdf.select_native_characters(info,list(range(i,i+len(text))))
    entity=session.entity(kind,value)
    session.detections.append(Detection(uid(),session.documents[0].id,1,bbox,'native_text',1,entity.id,value,entity.normalized_value,rects=rects))
    return bbox


def test_distinct_lines_are_not_combined_even_when_font_boxes_overlap(tmp_path):
    session,info=fixture(tmp_path)
    a=add_selection(session,info,'123456789012')
    b=add_selection(session,info,'2026123456789012')
    assert fitz.Rect(a).intersects(fitz.Rect(b))
    groups=pdf.group_replacements([(a,'{{OTHER_001}}'),(b,'{{OTHER_002}}')])
    assert len(groups)==2
    pipeline.generate(session,tmp_path)
    assert session.status=='complete',session.warnings
    with fitz.open(tmp_path/f'output-{session.documents[0].id}.pdf') as result:
        page=result[0]
        trace=[s for s in page.get_texttrace() if s['type']!=3]
        tokens=[''.join(chr(c[0]) for c in s['chars']) for s in trace]
        assert all(e.replacement() in tokens for e in session.entities)
        origins=[s['chars'][0][2][1] for s in trace]
        assert sorted(origins)==pytest.approx([80,90.8],abs=.01)
        text=page.get_text()
        assert 'Order:' in text and 'Invoice:' in text
        assert '123456789012' not in text


def test_token_preserves_baseline_color_and_bold_style(tmp_path):
    session,info=fixture(tmp_path)
    box=add_selection(session,info,'WWWWWWWWW1234567890')
    style=pdf.token_style(info,box)
    assert style['size']==12 and style['baseline']==150
    pipeline.generate(session,tmp_path)
    assert session.status=='complete',session.warnings
    with fitz.open(tmp_path/f'output-{session.documents[0].id}.pdf') as result:
        span=next(s for b in result[0].get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if session.entities[0].replacement() in s['text'])
        assert 'Bold' in span['font']
        assert span['size']==pytest.approx(12,abs=.01)
        assert span['origin'][1]==pytest.approx(150,abs=.01)
        assert span['color']==style['color']


def test_canonical_bitmap_is_not_resampled_during_export(tmp_path):
    session,info=fixture(tmp_path)
    pipeline.generate(session,tmp_path)
    with fitz.open(tmp_path/f'{session.documents[0].id}.pdf') as original,fitz.open(tmp_path/f'output-{session.documents[0].id}.pdf') as result:
        source_image=pdf.canonical_image(original[0]);output_image=pdf.canonical_image(result[0])
        assert source_image.size==output_image.size
        assert ImageChops.difference(source_image,output_image).getbbox() is None


def test_same_row_overlapping_detections_still_combine():
    assert len(pdf.group_replacements([([10,10,80,25],'{{OTHER_001}}'),([50,10,110,25],'{{EMAIL_001}}')]))==1
