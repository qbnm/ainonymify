import pytest
import pymupdf as fitz
from backend.app.services import pdf
from .conftest import native_pdf, scanned_pdf


def upload_and_analyze(client, raw):
    sid=client.post('/api/sessions').json()['id']
    response=client.post(f'/api/sessions/{sid}/documents',files={'files':('native.pdf',raw,'application/pdf')})
    assert response.status_code==200,response.text
    data=client.post(f'/api/sessions/{sid}/analyze').json()
    doc=data['documents'][0]
    return sid,doc


def ids_for(info, value):
    text=''.join(c['text'] for c in info['native_text'])
    start=text.index(value)
    return list(range(start,start+len(value)))


@pytest.mark.parametrize('kind',['scan','ocr','broken'])
def test_scans_and_hidden_ocr_are_not_native_text(tmp_path,kind):
    raw=scanned_pdf(hybrid=kind!='scan',broken=kind=='broken')
    info=pdf.canonicalize(raw,tmp_path/'scan.pdf')[0]
    assert info['text_selectable'] is False
    assert info['native_text']==[]


def test_transparent_text_is_not_selectable(tmp_path):
    with fitz.open() as doc:
        p=doc.new_page()
        p.insert_text((30,50),'Hidden text',fill_opacity=0)
        info=pdf.canonicalize(doc.tobytes(),tmp_path/'invisible.pdf')[0]
        assert info['text_selectable'] is False


def test_character_selection_uses_exact_source_geometry(client):
    sid,doc=upload_and_analyze(client,native_pdf(['prefix PrivateReference123 suffix']))
    info=doc['pages'][0]
    assert info['text_selectable']
    ids=ids_for(info,'PrivateReference123')
    response=client.post(f'/api/sessions/{sid}/detections/from-text',json={
        'document_id':doc['id'],'page':1,'character_ids':ids,'type':'OTHER'})
    assert response.status_code==200,response.text
    data=response.json();d=data['detections'][-1]
    assert d['source']=='native_text' and d['original_text']=='PrivateReference123'
    assert len(d['rects'])==1
    assert d['rects'][0][0]==pytest.approx(info['native_text'][ids[0]]['bbox'][0])
    assert d['rects'][0][2]==pytest.approx(info['native_text'][ids[-1]]['bbox'][2])
    assert client.post(f'/api/sessions/{sid}/generate').json()['status']=='complete'
    output=client.get(f'/api/sessions/{sid}/download').content
    with fitz.open(stream=output,filetype='pdf') as result:
        text=result[0].get_text()
        assert 'PrivateReference123' not in text
        assert 'prefix' in text and 'suffix' in text
        assert data['detections'][-1]['replacement'] in text


def test_multiline_selection_preserves_intervening_content_and_structure(client):
    sid,doc=upload_and_analyze(client,native_pdf(['TOP SECRET','DO NOT REMOVE','SECOND SECRET']))
    info=doc['pages'][0]
    ids=ids_for(info,'TOP SECRET')+ids_for(info,'SECOND SECRET')
    response=client.post(f'/api/sessions/{sid}/detections/from-text',json={
        'document_id':doc['id'],'page':1,'character_ids':ids,'type':'OTHER'})
    assert response.status_code==200,response.text
    assert len(response.json()['detections'][-1]['rects'])==2
    generated=client.post(f'/api/sessions/{sid}/generate')
    assert generated.json()['status']=='complete',generated.text
    output=client.get(f'/api/sessions/{sid}/download').content
    with fitz.open(stream=output,filetype='pdf') as result:
        text=' '.join(result[0].get_text().split())
        assert 'DO NOT REMOVE' in text
        assert 'TOP SECRET' not in text and 'SECOND SECRET' not in text
        assert all(part in text for part in response.json()['detections'][-1]['replacement'].splitlines())


@pytest.mark.parametrize('rotation',[90,180,270])
def test_rotated_source_glyphs_have_display_coordinates(client,rotation):
    sid,doc=upload_and_analyze(client,native_pdf(['PrivateReference123'],rotation=rotation))
    info=doc['pages'][0]
    ids=ids_for(info,'PrivateReference123')
    for char in info['native_text']:
        x0,y0,x1,y1=char['bbox']
        assert 0<=x0<x1<=info['width'] and 0<=y0<y1<=info['height']
    response=client.post(f'/api/sessions/{sid}/detections/from-text',json={
        'document_id':doc['id'],'page':1,'character_ids':ids,'type':'OTHER'})
    assert response.status_code==200,response.text
    generated=client.post(f'/api/sessions/{sid}/generate')
    assert generated.json()['status']=='complete',generated.text


def test_text_selection_rejects_invalid_and_foreign_ids_without_mutation(client):
    sid,doc=upload_and_analyze(client,native_pdf(['PrivateReference123']))
    body=dict(document_id=doc['id'],page=1,type='OTHER')
    before=client.get(f'/api/sessions/{sid}').json()['detections']
    for ids in [[-1],[999999]]:
        assert client.post(f'/api/sessions/{sid}/detections/from-text',json={**body,'character_ids':ids}).status_code==400
    assert client.post(f'/api/sessions/{sid}/detections/from-text',json={**body,'character_ids':[]}).status_code==422
    assert client.post(f'/api/sessions/{sid}/detections/from-text',json={**body,'document_id':'foreign','character_ids':[0]}).status_code==404
    assert client.get(f'/api/sessions/{sid}').json()['detections']==before
    sid,doc=upload_and_analyze(client,scanned_pdf(hybrid=True))
    assert client.post(f'/api/sessions/{sid}/detections/from-text',json={
        'document_id':doc['id'],'page':1,'type':'OTHER','character_ids':[0]}).status_code==400
