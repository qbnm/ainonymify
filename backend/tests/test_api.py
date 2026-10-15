import pymupdf as fitz
from .conftest import native_pdf


def new_session(client):
    response=client.post('/api/sessions');assert response.status_code==200,response.text
    return response.json()['id']


def test_auth_csrf_and_private_assets(client):
    assert client.get('/').status_code==200
    assert client.get('/vendor/pdfjs/pdf.mjs').status_code==200
    assert client.get('/',headers={'Authorization':'Basic bad'}).status_code==401
    response=client.post('/api/sessions',headers={'X-Privacy-Gateway':'wrong'})
    assert response.status_code==403
    assert client.post('/api/sessions',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    response=client.get('/')
    assert response.headers['cache-control']=='no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


def test_upload_delete_and_isolation(client):
    sid=new_session(client); other=new_session(client)
    response=client.post(f'/api/sessions/{sid}/documents',files={'files':('private.pdf',native_pdf(),'application/pdf')})
    assert response.status_code==200,response.text
    did=response.json()['documents'][0]['id']
    assert client.get(f'/api/sessions/{other}/documents/{did}/pdf').status_code==404
    assert client.get(f'/api/sessions/{sid}/download').status_code==400
    assert client.post(f'/api/sessions/{sid}/generate').status_code==400
    assert client.delete(f'/api/sessions/{sid}').status_code==200
    assert client.get(f'/api/sessions/{sid}').status_code==404
    assert client.get(f'/api/sessions/{other}').status_code==200


def test_review_edits_manual_merge_and_split(client):
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('private.pdf',native_pdf(),'application/pdf')})
    response=client.post(f'/api/sessions/{sid}/analyze');assert response.status_code==200,response.text
    data=response.json();did=data['documents'][0]['id'];d=data['detections'][0]
    entity=next(e for e in data['entities'] if e['id']==d['entity_id'])
    for status in ['ignored','confirmed']:
        response=client.post(f'/api/sessions/{sid}/detections/{d["id"]}',json={'type':entity['type'],'value':entity['value'],'status':status,'separate':status=='confirmed'})
        assert response.status_code==200,response.text
    data=response.json();split=next(e for e in data['entities'] if e['id']==data['detections'][0]['entity_id'])
    assert split['token']!=entity['token']
    response=client.post(f'/api/sessions/{sid}/entities/merge',json={'source':split['id'],'target':entity['id']})
    assert response.status_code==200
    response=client.post(f'/api/sessions/{sid}/detections',json={'document_id':did,'page':1,'bbox':[300,200,420,230],'type':'OTHER','visual_only':True})
    assert response.status_code==200,response.text
    response=client.post(f'/api/sessions/{sid}/generate')
    assert response.status_code==200 and response.json()['status']=='complete'
    assert client.get(f'/api/sessions/{sid}/download').status_code==200
    response=client.post(f'/api/sessions/{sid}/detections/{d["id"]}',json={'type':entity['type'],'value':entity['value'],'status':'confirmed'})
    assert response.json()['status']=='review'
    assert client.get(f'/api/sessions/{sid}/download').status_code==400


def test_reject_invalid_upload_and_remove_restore_api(client):
    sid=new_session(client)
    response=client.post(f'/api/sessions/{sid}/documents',files={'files':('bad.pdf',b'not a pdf','application/pdf')})
    assert response.status_code==400
    response=client.post('/api/restore',files=[('files',('result.pdf',native_pdf(),'application/pdf')),('mapping_file',('mapping.json',b'{}','application/json'))])
    assert response.status_code in (404,405)
    assert client.get('/api/restore').status_code==404
    html=client.get('/').text.lower()
    assert 'restore' not in html and 'processing mode' not in html


def test_failed_analysis_cannot_be_generated(client,monkeypatch):
    from backend.app.services import pipeline
    sid=new_session(client)
    response=client.post(f'/api/sessions/{sid}/documents',files={'files':('sample.pdf',native_pdf(),'application/pdf')})
    did=response.json()['documents'][0]['id']
    def broken(*args): raise RuntimeError('internal content must never reach the client')
    monkeypatch.setattr(pipeline,'analyze',broken)
    response=client.post(f'/api/sessions/{sid}/analyze')
    assert response.status_code==422 and 'internal content' not in response.text
    assert client.get(f'/api/sessions/{sid}').json()['status']=='analysis_failed'
    assert client.post(f'/api/sessions/{sid}/generate').status_code==400


def test_generate_automatically_ignores_pending_suggestions(client):
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('sample.pdf',native_pdf(),'application/pdf')})
    data=client.post(f'/api/sessions/{sid}/analyze').json()
    d=data['detections'][0];e=next(e for e in data['entities'] if e['id']==d['entity_id'])
    client.post(f'/api/sessions/{sid}/detections/{d["id"]}',json={'type':e['type'],'value':e['value'],'status':'pending'})
    response=client.post(f'/api/sessions/{sid}/generate')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['status']=='complete'
    assert next(item for item in data['detections'] if item['id']==d['id'])['status']=='ignored'
    assert client.get(f'/api/sessions/{sid}/download').status_code==200


def test_bulk_updates_occurrences_atomically_and_preserves_entities(client):
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files=[
        ('files',('one.pdf',native_pdf(),'application/pdf')),
        ('files',('two.pdf',native_pdf(),'application/pdf'))])
    data=client.post(f'/api/sessions/{sid}/analyze').json()
    ids=[d['id'] for d in data['detections'][:2]]
    original_entities=data['entities']
    original_detections={d['id']:d for d in data['detections']}
    response=client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':ids,'status':'ignored'})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['entities']==original_entities
    for d in data['detections']:
        assert d['status']==('ignored' if d['id'] in ids else original_detections[d['id']]['status'])
        assert d['entity_id']==original_detections[d['id']]['entity_id']
    # Unknown or foreign IDs must not apply even the valid portion of the action.
    other=new_session(client)
    client.post(f'/api/sessions/{other}/documents',files={'files':('other.pdf',native_pdf(),'application/pdf')})
    foreign=client.post(f'/api/sessions/{other}/analyze').json()['detections'][0]['id']
    for unknown in ['missing',foreign]:
        response=client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':[ids[0],unknown],'status':'confirmed'})
        assert response.status_code==404
        after=client.get(f'/api/sessions/{sid}').json()
        assert next(d for d in after['detections'] if d['id']==ids[0])['status']=='ignored'
    # Repeated IDs are harmless and no new tokens or identities are created.
    response=client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':ids+[ids[0]],'status':'confirmed'})
    assert response.status_code==200
    assert response.json()['entities']==original_entities
    assert all(d['status']=='confirmed' for d in response.json()['detections'] if d['id'] in ids)


def test_bulk_invalidates_old_download_and_ignore_changes_export(client):
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('sample.pdf',native_pdf(),'application/pdf')})
    data=client.post(f'/api/sessions/{sid}/analyze').json()
    ids=[d['id'] for d in data['detections']]
    assert client.post(f'/api/sessions/{sid}/generate').json()['status']=='complete'
    assert client.get(f'/api/sessions/{sid}/download').status_code==200
    response=client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':ids,'status':'ignored'})
    assert response.json()['status']=='review'
    assert client.get(f'/api/sessions/{sid}/download').status_code==400
    assert client.post(f'/api/sessions/{sid}/generate').json()['status']=='complete'
    output=client.get(f'/api/sessions/{sid}/download').content
    with fitz.open(stream=output,filetype='pdf') as doc:
        assert 'DE02120300000000202051' in doc[0].get_text()
        assert '{{IBAN_001}}' not in doc[0].get_text()


def test_bulk_rejects_empty_selection_and_invalid_action(client):
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('sample.pdf',native_pdf(),'application/pdf')})
    data=client.post(f'/api/sessions/{sid}/analyze').json()
    ids=[d['id'] for d in data['detections']]
    assert client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':[],'status':'ignored'}).status_code==422
    for status in ['pending','delete']:
        assert client.post(f'/api/sessions/{sid}/detections/bulk',json={'detection_ids':ids,'status':status}).status_code==400
    assert client.get(f'/api/sessions/{sid}').json()['detections']==data['detections']


def test_mapping_export_is_optional_and_excluded_by_default(client):
    from backend.app.services.mapping import load
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('private-name.pdf',native_pdf(),'application/pdf')})
    client.post(f'/api/sessions/{sid}/analyze')
    assert client.get(f'/api/sessions/{sid}/download/mapping').status_code==400
    response=client.post(f'/api/sessions/{sid}/generate')
    assert response.status_code==200 and response.json()['includes_mapping'] is False
    download=client.get(f'/api/sessions/{sid}/download')
    assert download.headers['content-type']=='application/pdf'
    assert download.content.startswith(b'%PDF-')
    assert download.headers['content-disposition']=='attachment; filename="document-01.anonymized.pdf"'
    assert client.get(f'/api/sessions/{sid}/download/mapping').status_code==400
    response=client.post(f'/api/sessions/{sid}/generate',json={'include_mapping':True})
    assert response.status_code==200 and response.json()['includes_mapping'] is True
    assert client.get(f'/api/sessions/{sid}/download').headers['content-type']=='application/pdf'
    reference=client.get(f'/api/sessions/{sid}/download/mapping')
    assert reference.headers['content-type']=='application/json'
    assert '{{IBAN_001}}' in load(reference.content)
    response=client.post(f'/api/sessions/{sid}/generate',json={'include_mapping':False})
    assert response.json()['includes_mapping'] is False
    assert client.get(f'/api/sessions/{sid}/download/mapping').status_code==400


def test_multiple_documents_download_as_zip_with_optional_reference(client):
    from io import BytesIO
    from zipfile import ZipFile
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files=[
        ('files',('private-a.pdf',native_pdf(),'application/pdf')),
        ('files',('private-b.pdf',native_pdf(),'application/pdf'))])
    client.post(f'/api/sessions/{sid}/analyze')
    for include in [False,True]:
        result=client.post(f'/api/sessions/{sid}/generate',json={'include_mapping':include})
        assert result.json()['status']=='complete',result.text
        response=client.get(f'/api/sessions/{sid}/download')
        assert response.headers['content-type']=='application/zip'
        assert response.headers['content-disposition']=='attachment; filename="privacy-session.zip"'
        archive=ZipFile(BytesIO(response.content))
        expected={'documents/document-01.anonymized.pdf','documents/document-02.anonymized.pdf'}
        assert set(archive.namelist())==expected|({'mapping.json'} if include else set())
        for name in expected:
            assert archive.read(name).startswith(b'%PDF-')


def test_failed_regeneration_does_not_release_previous_pdf_or_reference(client,monkeypatch):
    from backend.app.services import pdf
    sid=new_session(client)
    client.post(f'/api/sessions/{sid}/documents',files={'files':('sample.pdf',native_pdf(),'application/pdf')})
    client.post(f'/api/sessions/{sid}/analyze')
    assert client.post(f'/api/sessions/{sid}/generate',json={'include_mapping':True}).json()['status']=='complete'
    def fail(*args): raise ValueError('Synthetic failure')
    monkeypatch.setattr(pdf,'canonical_image',fail)
    assert client.post(f'/api/sessions/{sid}/generate').status_code==400
    assert client.get(f'/api/sessions/{sid}/download').status_code==400
    assert client.get(f'/api/sessions/{sid}/download/mapping').status_code==400
