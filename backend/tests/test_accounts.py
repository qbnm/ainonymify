"""Optional local accounts, durable preferences, and per-browser/account isolation."""
import pytest
import pymupdf as fitz
from backend.app.services.accounts import Accounts
from backend.app.services import pdf, personal_rules
from backend.app.models import Session, Document, Detection, uid
from .conftest import native_pdf, scanned_pdf, EMAIL
from .test_text_selection import ids_for

PASSWORD = 'test-account-password-123'


def login(client, username='alice', register=True):
    r = client.post('/api/account/' + ('register' if register else 'login'), json={'username': username, 'password': PASSWORD})
    assert r.status_code == 200, r.text
    return r


def batch(client, raw=None):
    sid = client.post('/api/sessions').json()['id']
    r = client.post(f'/api/sessions/{sid}/documents', files={'files': ('example.pdf', raw or native_pdf(), 'application/pdf')})
    assert r.status_code == 200, r.text
    r = client.post(f'/api/sessions/{sid}/analyze')
    assert r.status_code == 200, r.text
    return sid, r.json()


def save(client, text='PrivateReference123', action='redact', kind='OTHER', label='My reference'):
    r = client.post('/api/account/rules', json={'text': text, 'type': kind, 'action': action, 'label': label})
    assert r.status_code == 200, r.text
    return r.json()['rules']


def test_accounts_credentials_cookies_and_revocation(client):
    assert client.get('/api/account').json() == {'account': None}
    for method in ['get', 'post']:
        r = getattr(client, method)('/api/account/rules', **({'json': {'text': 'x', 'type': 'OTHER', 'action': 'ignore'}} if method == 'post' else {}))
        assert r.status_code == 401
    response = login(client)
    cookie = response.headers.get('set-cookie')
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert 'password' not in response.text and 'token' not in response.text
    token = client.cookies.get('ainonymify_login')
    assert client.get('/api/account').json()['account']['username'] == 'alice'
    assert client.post('/api/account/register', json={'username': 'ALICE', 'password': PASSWORD}).status_code == 400
    assert client.delete('/api/account/login').status_code == 200
    client.cookies.set('ainonymify_login', token)
    assert client.get('/api/account').json()['account'] is None
    client.cookies.clear()
    assert client.post('/api/account/login', json={'username': 'alice', 'password': 'wrong-password-123'}).status_code == 401
    login(client, 'ALICE', register=False)
    assert client.post('/api/account/register', json={'username': 'bob', 'password': 'short'}).status_code == 422
    assert client.post('/api/account/rules', json={'text': 'x', 'type': 'OTHER', 'action': 'ignore'}, headers={'X-Privacy-Gateway': 'wrong'}).status_code == 403
    assert client.delete('/api/account', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_users_and_guest_cannot_read_or_change_other_sessions_or_rules(client):
    sid_guest = client.post('/api/sessions').json()['id']
    guest_cookie = client.cookies.get('ainonymify_guest')
    login(client)
    sid = client.post('/api/sessions').json()['id']
    alice_token = client.cookies.get('ainonymify_login')
    save(client)
    assert client.get(f'/api/sessions/{sid_guest}').status_code == 404
    client.delete('/api/account/login')
    login(client, 'bob')
    assert client.get('/api/account/rules').json()['rules'] == []
    for suffix, method, args in [('', 'get', {}), ('', 'delete', {}), ('/analyze', 'post', {}), ('/generate', 'post', {})]:
        assert getattr(client, method)(f'/api/sessions/{sid}{suffix}', **args).status_code == 404
    rule = save(client)[0]
    client.delete('/api/account/login')
    assert client.get('/api/account/rules').status_code == 401
    assert client.get(f'/api/sessions/{sid_guest}').status_code == 200
    client.cookies.clear()
    assert client.get(f'/api/sessions/{sid_guest}').status_code == 404
    client.cookies.set('ainonymify_guest', guest_cookie)
    login(client, 'alice', register=False)
    assert client.delete('/api/account/rules/' + rule['id']).status_code == 404
    assert client.get(f'/api/sessions/{sid}').status_code == 200
    # Previously revoked Alice token remains invalid after another login.
    client.cookies.clear(); client.cookies.set('ainonymify_login', alice_token)
    assert client.get(f'/api/sessions/{sid}').status_code == 404


def test_personal_rule_native_case_whitespace_boundaries_and_export(client):
    login(client)
    save(client, 'Secret Lane 17', kind='ADDRESS', label='Home address')
    sid, data = batch(client, native_pdf(['prefix secret  lane 17 suffix', 'Secret Lane 170', 'SECRET LANE 17']))
    matches = [d for d in data['detections'] if d['source'] == 'personal_rule']
    assert len(matches) == 2
    assert all(d['status'] == 'confirmed' and d['reason'] == 'Saved rule: Home address' for d in matches)
    assert not any(d['original_text'] == 'Secret Lane 170' for d in matches)
    response = client.post(f'/api/sessions/{sid}/generate')
    assert response.status_code == 200 and response.json()['status'] == 'complete', response.text
    with fitz.open(stream=client.get(f'/api/sessions/{sid}/download').content, filetype='pdf') as result:
        assert 'secret  lane 17' not in result[0].get_text()
        assert 'Secret Lane 170' in result[0].get_text()


def test_ignore_is_learned_and_can_be_forgotten_without_affecting_guest(client):
    login(client)
    sid, data = batch(client)
    detection = next(d for d in data['detections'] if d['original_text'] == EMAIL)
    r = client.post(f'/api/sessions/{sid}/detections/{detection["id"]}', json={'type': 'EMAIL', 'value': EMAIL, 'status': 'ignored'})
    assert r.status_code == 200, r.text
    rules = client.get('/api/account/rules').json()['rules']
    assert len(rules) == 1 and rules[0]['action'] == 'ignore'
    _, future = batch(client)
    assert not any(d['original_text'] == EMAIL for d in future['detections'])
    assert client.delete('/api/account/rules/' + rules[0]['id']).status_code == 200
    _, future = batch(client)
    assert any(d['original_text'] == EMAIL for d in future['detections'])
    save(client, EMAIL, action='ignore', kind='EMAIL')
    client.delete('/api/account/login')
    _, guest = batch(client)
    assert any(d['original_text'] == EMAIL for d in guest['detections'])


def test_native_manual_and_bulk_learning_opt_out_and_automatic_generate_does_not_learn(client):
    login(client)
    sid, data = batch(client, native_pdf(['prefix PrivateReference123 suffix', 'Email: '+EMAIL]))
    assert client.get('/api/account/rules').json()['rules'] == []
    doc = data['documents'][0]
    body = {'document_id': doc['id'], 'page': 1, 'character_ids': ids_for(doc['pages'][0], 'PrivateReference123'), 'type': 'OTHER', 'personal_label': 'My reference'}
    r = client.post(f'/api/sessions/{sid}/detections/from-text', json={**body, 'remember': False})
    assert r.status_code == 200 and client.get('/api/account/rules').json()['rules'] == []
    r = client.post(f'/api/sessions/{sid}/detections/from-text', json=body)
    assert r.status_code == 200, r.text
    assert client.get('/api/account/rules').json()['rules'][0]['label'] == 'My reference'
    _, future = batch(client, native_pdf(['PrivateReference123']))
    assert any(d['source'] == 'personal_rule' for d in future['detections'])
    ids = [d['id'] for d in r.json()['detections']]
    r = client.post(f'/api/sessions/{sid}/detections/bulk', json={'detection_ids': ids, 'status': 'ignored'})
    assert r.status_code == 200, r.text
    rules = client.get('/api/account/rules').json()['rules']
    assert all(r['action'] == 'ignore' for r in rules)
    assert next(r for r in rules if r['text'] == 'PrivateReference123')['label'] == 'My reference'
    # A new explicit decision replaces the old Ignore rule.
    client.post(f'/api/sessions/{sid}/detections/bulk', json={'detection_ids': ids, 'status': 'confirmed'})
    assert all(r['action'] == 'redact' for r in client.get('/api/account/rules').json()['rules'])
    before = client.get('/api/account/rules').json()
    client.post(f'/api/sessions/{sid}/generate')
    assert client.get('/api/account/rules').json() == before


def test_manual_regions_are_learned_but_visual_only_and_split_are_not(client):
    login(client)
    sid, data = batch(client, native_pdf(['PrivateReference123']))
    doc = data['documents'][0]
    body = {'document_id': doc['id'], 'page': 1, 'bbox': [43, 48, 180, 70], 'type': 'OTHER', 'value': 'PrivateReference123', 'personal_label': 'Reference'}
    r = client.post(f'/api/sessions/{sid}/detections', json=body)
    assert r.status_code == 200, r.text
    rules = client.get('/api/account/rules').json()['rules']
    assert any(rule['text'] == 'PrivateReference123' for rule in rules)
    before = client.get('/api/account/rules').json()
    r = client.post(f'/api/sessions/{sid}/detections', json={**body, 'bbox': [300, 200, 400, 230], 'visual_only': True})
    assert r.status_code == 200, r.text
    d = r.json()['detections'][0]
    assert client.post(f'/api/sessions/{sid}/detections/{d["id"]}', json={'type':'OTHER','value':'Another value','separate':True}).status_code == 200
    assert client.get('/api/account/rules').json() == before


def test_rule_upsert_validation_and_delete_account(client):
    login(client)
    first = save(client)
    second = save(client, 'privatereference123', action='ignore')
    assert len(second) == 1 and first[0]['id'] == second[0]['id']
    cleared = save(client, label='')
    assert cleared[0]['label'] == 'OTHER'
    for body in [{'text':'!!!','type':'OTHER','action':'ignore'}, {'text':'x','type':'INVALID','action':'ignore'}, {'text':'x','type':'OTHER','action':'delete'}]:
        assert client.post('/api/account/rules',json=body).status_code == 400
    sid = client.post('/api/sessions').json()['id']
    assert client.delete('/api/account').status_code == 200
    assert client.get('/api/account').json()['account'] is None
    assert client.get(f'/api/sessions/{sid}').status_code == 404
    assert client.post('/api/account/login',json={'username':'alice','password':PASSWORD}).status_code == 401
    login(client)
    assert client.get('/api/account/rules').json()['rules'] == []


def test_persistence_secret_hashes_limits_and_atomic_preferences(tmp_path):
    root = tmp_path / 'data'
    db = Accounts(root)
    user, token = db.authenticate('alice', PASSWORD, True)
    user_id = user['id']
    db.save_rule(user_id, 'PrivateReference123', 'OTHER', 'My reference', 'redact')
    db.close()
    raw = (root / 'accounts.sqlite3').read_bytes()
    assert PASSWORD.encode() not in raw and token.encode() not in raw
    db = Accounts(root)
    assert db.identity(token) == user
    assert db.list_rules(user_id)[0]['text'] == 'PrivateReference123'
    for i in range(199):
        db.save_rule(user_id, f'Value{i}', 'OTHER', '', 'ignore')
    with pytest.raises(ValueError, match='limit'):
        db.save_rules(user_id, [('Value0', 'OTHER', '', 'redact'), ('Overflow', 'OTHER', '', 'redact')])
    assert next(r for r in db.list_rules(user_id) if r['text'] == 'Value0')['action'] == 'ignore'
    assert len(db.list_rules(user_id)) == 200
    db.logout(token); assert db.identity(token) is None
    for i in range(20): db.throttle('peer')
    with pytest.raises(Exception) as exc: db.throttle('peer')
    assert exc.value.status_code == 429
    db.close()


def test_multiline_native_and_ocr_ignore_precedence(tmp_path):
    raw = native_pdf(['Secret Lane', '17', 'PrivateReference123'])
    path = tmp_path / 'native.pdf'
    pages = pdf.canonicalize(raw, path)
    session = Session(); session.documents.append(Document('doc', 'example.pdf', pages))
    personal_rules.apply(session, [
        {'text':'Secret Lane 17','type':'ADDRESS','label':'Home','action':'redact'},
        {'text':'Secret Lane','type':'ADDRESS','label':'Safe','action':'ignore'},
        {'text':'PrivateReference123','type':'OTHER','label':'Reference','action':'redact'}])
    assert len(session.detections) == 1 and session.detections[0].original_text == 'PrivateReference123'
    session.detections.clear()
    personal_rules.apply(session,[{'text':'Secret Lane 17','type':'ADDRESS','label':'Home','action':'redact'}])
    assert len(session.detections) == 1 and len(session.detections[0].rects) == 2
    pages = pdf.canonicalize(scanned_pdf(), tmp_path / 'scan.pdf')
    with fitz.open(tmp_path / 'scan.pdf') as doc:
        pages[0]['ocr_words'] = pdf.ocr(pdf.render(doc[0]), 595, 420)
    session = Session(); session.documents.append(Document('scan', 'scan.pdf', pages))
    personal_rules.apply(session,[{'text':EMAIL,'type':'EMAIL','label':'My email','action':'redact'}])
    assert len(session.detections) == 1 and session.detections[0].source == 'personal_rule'


def test_account_survives_application_restart_but_documents_do_not(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    monkeypatch.setenv('APP_USERNAME', 'tester')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('APP_TEMP_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'accounts'))
    headers = {'X-Privacy-Gateway':'1', 'Authorization':'Basic dGVzdGVyOnRlc3QtcGFzc3dvcmQtMTIz'}
    with TestClient(app, headers=headers) as first:
        login(first)
        save(first)
        token = first.cookies.get('ainonymify_login')
        sid = first.post('/api/sessions').json()['id']
    with TestClient(app, headers=headers) as restarted:
        restarted.cookies.set('ainonymify_login', token)
        assert restarted.get('/api/account').json()['account']['username'] == 'alice'
        assert restarted.get('/api/account/rules').json()['rules'][0]['text'] == 'PrivateReference123'
        assert restarted.get(f'/api/sessions/{sid}').status_code == 404
        _, data = batch(restarted, native_pdf(['PrivateReference123']))
        assert any(d['source'] == 'personal_rule' for d in data['detections'])


def test_unknown_session_delete_never_touches_parent_directory(tmp_path):
    from backend.app.services.sessions import Store
    store = Store(root=tmp_path)
    sid = store.create().id
    marker = tmp_path / 'keep.txt'
    marker.write_text('keep')
    store.delete('..')
    store.delete('unknown')
    assert marker.read_text() == 'keep'
    with store.access(sid) as (session, root):
        assert root.exists()
    store.close()
