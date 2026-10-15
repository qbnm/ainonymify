import pymupdf as fitz
from backend.app.services import pdf, repeats
from .conftest import native_pdf
from .test_text_selection import ids_for, upload_and_analyze


def multi_page_pdf():
    with fitz.open() as doc:
        for lines in [['PrivateReference123 / PrivateReference123'],
                      ['PrivateReference123', 'PrivateReference1234', 'privatereference123']]:
            for i, text in enumerate(lines):
                if i == 0: page = doc.new_page(width=595, height=420)
                page.insert_text((45,65+i*35),text,fontsize=13)
        return doc.tobytes()


def select(client, sid, doc, value='PrivateReference123'):
    ids = ids_for(doc['pages'][0], value)
    response = client.post(f'/api/sessions/{sid}/detections/from-text', json={
        'document_id':doc['id'],'page':1,'character_ids':ids,'type':'OTHER'})
    assert response.status_code == 200, response.text
    return response.json()


def test_native_repeats_all_pages_exact_case_boundaries_export_and_no_duplicates(client):
    sid,doc = upload_and_analyze(client,multi_page_pdf())
    data = select(client,sid,doc)
    assert len(data['detections']) == 3
    assert {d['page'] for d in data['detections']} == {1,2}
    assert len({d['entity_id'] for d in data['detections']}) == 1
    assert len({d['replacement'] for d in data['detections']}) == 1
    assert all(d['status']=='confirmed' for d in data['detections'])
    assert len(select(client,sid,doc)['detections']) == 3
    generated = client.post(f'/api/sessions/{sid}/generate').json()
    assert generated['status']=='complete', generated['warnings']
    output=client.get(f'/api/sessions/{sid}/download').content
    with fitz.open(stream=output,filetype='pdf') as output:
        first,second=[p.get_text() for p in output]
        replacement=data['detections'][0]['replacement']
        assert first.count(replacement)==2 and replacement in second
        assert 'PrivateReference1234' in second and 'privatereference123' in second
        assert 'PrivateReference123' not in first


def test_only_current_document_and_local_ignore_remains_possible(client):
    sid=client.post('/api/sessions').json()['id']
    raw=native_pdf(['PrivateReference123','PrivateReference123'])
    client.post(f'/api/sessions/{sid}/documents',files=[('files',('a.pdf',raw,'application/pdf')),('files',('b.pdf',raw,'application/pdf'))])
    documents=client.post(f'/api/sessions/{sid}/analyze').json()['documents']
    data=select(client,sid,documents[0])
    assert len(data['detections'])==2
    assert all(d['document_id']==documents[0]['id'] for d in data['detections'])
    repeat=next(d for d in data['detections'] if d['source']=='repeated_text')
    entity=next(e for e in data['entities'] if e['id']==repeat['entity_id'])
    response=client.post(f'/api/sessions/{sid}/detections/{repeat["id"]}',json={
        'type':entity['type'],'value':entity['value'],'status':'ignored'})
    assert next(d for d in response.json()['detections'] if d['id']==repeat['id'])['status']=='ignored'
    assert client.post(f'/api/sessions/{sid}/generate').json()['status']=='complete'
    assert client.post(f'/api/sessions/{sid}/detections/{data["detections"][0]["id"]}',json={
        'type':entity['type'],'value':entity['value'],'status':'confirmed'}).json()['status']=='review'
    assert client.get(f'/api/sessions/{sid}/download').status_code==400
    assert all(d['status']=='confirmed' for d in client.get(f'/api/sessions/{sid}').json()['detections'])


def test_multiline_repeats_keep_separate_geometry_and_flexible_whitespace(client):
    sid,doc=upload_and_analyze(client,native_pdf(['Secret Alpha','Secret  Alpha','Secret','Alpha']))
    data=select(client,sid,doc,'Secret Alpha')
    assert len(data['detections'])==3
    multiline=next(d for d in data['detections'] if len(d['rects'])==2)
    assert multiline['original_text']=='Secret\nAlpha'
    assert all(d['status']=='confirmed' for d in data['detections'])
    result=client.post(f'/api/sessions/{sid}/generate').json()
    assert result['status']=='complete',result['warnings']


def test_ocr_word_geometry_matches_whole_words_and_all_lines():
    info={'ocr_words':[(10,10,40,25,'Secret',1,1,0),(50,10,80,25,'Alpha',1,1,1),
                       (10,40,40,55,'Secret',1,2,0),(10,60,40,75,'Alpha',1,3,0)]}
    matches=list(repeats.ocr_occurrences(info,repeats.pattern_for('Secret Alpha')))
    assert len(matches)==2
    assert matches[0][1]==[[10,10,80,25]]
    assert matches[1][1]==[[10,40,40,55],[10,60,40,75]]
    assert not list(repeats.ocr_occurrences(info,repeats.pattern_for('Secr')))


def test_manual_rectangle_also_marks_repeats(client):
    sid,doc=upload_and_analyze(client,native_pdf(['PrivateReference123','PrivateReference123']))
    box,_,_=pdf.select_native_characters(doc['pages'][0],ids_for(doc['pages'][0],'PrivateReference123'))
    response=client.post(f'/api/sessions/{sid}/detections',json={
        'document_id':doc['id'],'page':1,'bbox':box,'type':'OTHER','value':'PrivateReference123'})
    assert response.status_code==200,response.text
    assert len(response.json()['detections'])==2


def test_visual_only_does_not_mark_text_repeats(client):
    sid,doc=upload_and_analyze(client,native_pdf(['PrivateReference123','PrivateReference123']))
    box,_,_=pdf.select_native_characters(doc['pages'][0],ids_for(doc['pages'][0],'PrivateReference123'))
    response=client.post(f'/api/sessions/{sid}/detections',json={
        'document_id':doc['id'],'page':1,'bbox':box,'type':'OTHER','visual_only':True})
    assert response.status_code==200,response.text
    assert len(response.json()['detections'])==1


def test_scanned_repeats_use_cached_ocr_and_export(client):
    with fitz.open(stream=native_pdf(['Secret Alpha','Secret Alpha']),filetype='pdf') as source:
        image=source[0].get_pixmap(dpi=200).tobytes('png')
        with fitz.open() as scanned:
            scanned.new_page(width=595,height=420).insert_image(fitz.Rect(0,0,595,420),stream=image)
            raw=scanned.tobytes()
    sid,doc=upload_and_analyze(client,raw)
    assert not doc['pages'][0]['text_selectable']
    assert 'ocr_words' not in doc['pages'][0]
    response=client.post(f'/api/sessions/{sid}/detections',json={
        'document_id':doc['id'],'page':1,'bbox':[42,50,140,72],'type':'OTHER','value':'Secret Alpha'})
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['detections'])==2
    assert len({d['entity_id'] for d in data['detections']})==1
    result=client.post(f'/api/sessions/{sid}/generate').json()
    assert result['status']=='complete', result['warnings']
    output=client.get(f'/api/sessions/{sid}/download').content
    with fitz.open(stream=output,filetype='pdf') as output:
        text=output[0].get_text()
        assert 'Secret' not in text and 'Alpha' not in text
