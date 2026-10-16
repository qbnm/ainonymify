"""Regression cases from the security review, using synthetic data only."""
import re
import pymupdf as fitz
import pytest
from fastapi import HTTPException
from backend.app.models import Session, Document
from backend.app.services import pdf, pipeline, personal_rules, repeats
from backend.app.services.accounts import Accounts, owner
from backend.app.services.sessions import Store
from backend.app.services.text_matching import text_key, normalized_text, normalized_matcher
from .conftest import native_pdf, EMAIL
from .test_accounts import login, batch, PASSWORD
from .test_text_selection import ids_for


def rule(text, action='redact'):
    return dict(text=text, type='OTHER', label='Synthetic rule', action=action)


@pytest.mark.parametrize('public_action', ['ignore', 'redact'])
def test_multiline_rule_does_not_suppress_private_text_between_fragments(tmp_path, public_action):
    with fitz.open() as source:
        page = source.new_page(width=595, height=420)
        page.insert_text((45,65), 'PUBLIC INFORMATION THAT IS SAFE\nSHORT', fontsize=13)
        page.insert_text((220,84), 'PrivateReference123', fontsize=13)
        pages = pdf.canonicalize(source.tobytes(), tmp_path/'doc.pdf')
    session = Session(documents=[Document('doc', 'synthetic.pdf', pages)])
    pipeline.analyze(session, tmp_path)
    personal_rules.apply(session, [rule('PUBLIC INFORMATION THAT IS SAFE SHORT', public_action), rule('PrivateReference123')])
    private = [d for d in session.detections if d.original_text == 'PrivateReference123']
    assert len(private) == 1 and private[0].status == 'confirmed'
    pipeline.generate(session, tmp_path)
    assert session.status == 'complete', session.warnings
    with fitz.open(tmp_path/'output-doc.pdf') as result:
        assert 'PrivateReference123' not in result[0].get_text()
        entity = next(e for e in session.entities if e.id == private[0].entity_id)
        assert entity.replacement('PrivateReference123') in result[0].get_text()


@pytest.mark.parametrize('value', [
    'Musterstraße 12', 'MUSTERSTRASSE 12', 'Mu\u0308ller', 'MÜLLER',
    '  ＡＢＣ１２３\t\nﬃ  ', '\u1100\u1161\u11a8', '가\u11a8',
    'ｶﾞ', 'İSTANBUL', 'a\u0315\u0300', '  two\u00a0\u2003words  ',
])
def test_normalized_source_text_uses_exactly_the_rule_key_algorithm(value):
    normalized, offsets = normalized_text(value)
    assert normalized == text_key(value)
    assert len(normalized) == len(offsets)
    assert all(0 <= start < end <= len(value) for start, end in offsets)


def test_normalized_matches_never_select_half_an_expansion():
    matcher = normalized_matcher()
    assert list(matcher(re.compile('s'), 'ß')) == []
    assert list(matcher(re.compile('ss'), 'ß')) == [(0,1)]
    assert list(matcher(re.compile('ffi'), 'ﬃ')) == [(0,1)]
    assert list(matcher(re.compile('ü'), 'u\u0308')) == [(0,2)]


def test_one_unicode_rule_matches_all_native_spellings_and_preserves_source_rects(tmp_path):
    db = Accounts(tmp_path/'accounts')
    try:
        user, _ = db.authenticate('unicode-user', PASSWORD, True)
        for spelling in ['Musterstraße 12', 'MUSTERSTRASSE 12']:
            db.save_rule(user['id'], spelling, 'ADDRESS', 'Home', 'redact')
        rules = db.list_rules(user['id'])
        assert len(rules) == 1
        pages = pdf.canonicalize(native_pdf(['Musterstraße 12', 'MUSTERSTRASSE 12']), tmp_path/'doc.pdf')
        session = Session(documents=[Document('doc', 'synthetic.pdf', pages)])
        personal_rules.apply(session, rules)
        assert {d.original_text for d in session.detections} == {'Musterstraße 12', 'MUSTERSTRASSE 12'}
        for d in session.detections:
            ids = ids_for(pages[0], d.original_text)
            bbox, rects, original = pdf.select_native_characters(pages[0], ids)
            assert (d.bbox, d.rects, d.original_text) == (bbox, rects, original)
        pipeline.generate(session, tmp_path)
        assert session.status == 'complete', session.warnings
    finally:
        db.close()


def test_unicode_rule_matches_ocr_words_without_using_normalized_offsets_as_coordinates():
    words = [(10,10,80,25,'Musterstraße',0,0,0), (85,10,100,25,'12',0,0,1),
             (10,40,90,55,'MUSTERSTRASSE',0,1,2), (95,40,110,55,'12',0,1,3)]
    page = {'width':200, 'height':100, 'native_text':[], 'ocr_words':words}
    session = Session(documents=[Document('doc', 'scan.pdf', [page])])
    personal_rules.apply(session, [rule('MUSTERSTRASSE 12')])
    assert [d.original_text for d in session.detections] == ['Musterstraße 12', 'MUSTERSTRASSE 12']
    assert [d.bbox for d in session.detections] == [[10,10,100,25], [10,40,110,55]]
    personal_rules.apply(session, [rule('Musterstraße 12', 'ignore')])
    assert not session.detections


@pytest.mark.parametrize('endpoint', ['bulk', 'single', 'native', 'manual'])
def test_rule_limit_failure_rolls_back_all_review_changes(client, endpoint):
    from backend.app import main
    user = login(client).json()['account']
    main.accounts.save_rules(user['id'], [(f'Existing rule {i}', 'OTHER', 'Seed', 'redact') for i in range(200)])
    sid, before = batch(client, native_pdf(['Email: '+EMAIL, 'PrivateReference123', 'PrivateReference123']))
    email = next(d for d in before['detections'] if d['original_text'] == EMAIL)
    doc = before['documents'][0]
    if endpoint == 'bulk':
        response = client.post(f'/api/sessions/{sid}/detections/bulk', json={'detection_ids':[email['id']], 'status':'ignored'})
    elif endpoint == 'single':
        response = client.post(f'/api/sessions/{sid}/detections/{email["id"]}', json={'type':'EMAIL', 'value':EMAIL, 'status':'ignored'})
    elif endpoint == 'native':
        response = client.post(f'/api/sessions/{sid}/detections/from-text', json={
            'document_id':doc['id'], 'page':1, 'type':'OTHER', 'character_ids':ids_for(doc['pages'][0], 'PrivateReference123')})
    else:
        bbox, _, _ = pdf.select_native_characters(doc['pages'][0], ids_for(doc['pages'][0], 'PrivateReference123'))
        response = client.post(f'/api/sessions/{sid}/detections', json={
            'document_id':doc['id'], 'page':1, 'type':'OTHER', 'value':'PrivateReference123', 'bbox':bbox})
    assert response.status_code == 400 and 'limit' in response.json()['detail'], response.text
    assert client.get(f'/api/sessions/{sid}').json() == before
    assert len(client.get('/api/account/rules').json()['rules']) == 200


def test_failed_review_preserves_existing_verified_download_and_mapping(client):
    from backend.app import main
    user = login(client).json()['account']
    sid, _ = batch(client, native_pdf([EMAIL]))
    before = client.post(f'/api/sessions/{sid}/generate', json={'include_mapping':True}).json()
    assert before['status'] == 'complete'
    output = client.get(f'/api/sessions/{sid}/download').content
    mapping = client.get(f'/api/sessions/{sid}/download/mapping').content
    main.accounts.save_rules(user['id'], [(f'Existing rule {i}', 'OTHER', 'Seed', 'redact') for i in range(200)])
    d = before['detections'][0]
    response = client.post(f'/api/sessions/{sid}/detections/bulk', json={'detection_ids':[d['id']], 'status':'ignored'})
    assert response.status_code == 400
    assert client.get(f'/api/sessions/{sid}').json() == before
    assert client.get(f'/api/sessions/{sid}/download').content == output
    assert client.get(f'/api/sessions/{sid}/download/mapping').content == mapping
    response = client.post(f'/api/sessions/{sid}/detections/bulk', json={'detection_ids':[d['id']], 'status':'ignored', 'remember':False})
    assert response.status_code == 200 and response.json()['status'] == 'review'
    assert client.get(f'/api/sessions/{sid}/download').status_code == 400
    assert client.get(f'/api/sessions/{sid}/download/mapping').status_code == 400


def test_review_and_saved_rules_roll_back_together_if_batch_exceeds_limit(client):
    from backend.app import main
    user = login(client).json()['account']
    main.accounts.save_rules(user['id'], [(f'Existing rule {i}', 'OTHER', 'Seed', 'redact') for i in range(199)])
    sid, before = batch(client)
    response = client.post(f'/api/sessions/{sid}/detections/bulk', json={
        'detection_ids':[d['id'] for d in before['detections']], 'status':'ignored'})
    assert response.status_code == 400
    assert client.get(f'/api/sessions/{sid}').json() == before
    assert len(client.get('/api/account/rules').json()['rules']) == 199


def test_owner_quota_leaves_room_for_other_users_and_guest_cookie_rotation_is_bounded(client):
    first = [client.post('/api/sessions').json()['id'] for _ in range(3)]
    assert client.post('/api/sessions').status_code == 429
    client.cookies.clear()
    second = [client.post('/api/sessions').json()['id'] for _ in range(3)]
    assert len(set(first+second)) == 6
    client.cookies.clear()
    assert client.post('/api/sessions').status_code == 429
    # An authenticated account still has room even if this address's guest quota is full.
    login(client)
    own = client.post('/api/sessions')
    assert own.status_code == 200, own.text
    sid = own.json()['id']
    assert client.delete(f'/api/sessions/{sid}').status_code == 200
    assert client.post('/api/sessions').status_code == 200


def test_session_creation_rate_limit_survives_delete_and_then_expires(tmp_path, monkeypatch):
    from backend.app.services import sessions
    now = [1000.0]
    monkeypatch.setattr(sessions.time, 'monotonic', lambda:now[0])
    store = Store(root=tmp_path)
    context = owner.set('u:test')
    try:
        for _ in range(10):
            store.delete(store.create().id)
        with pytest.raises(HTTPException) as error:
            store.create()
        assert error.value.status_code == 429
        now[0] += 61
        assert store.create()
    finally:
        owner.reset(context); store.close()


def test_auth_failures_for_one_account_do_not_block_another_at_same_address(client):
    login(client, 'alice'); client.delete('/api/account/login')
    login(client, 'bob'); client.delete('/api/account/login')
    for _ in range(20):
        assert client.post('/api/account/login', json={'username':'ALICE','password':'wrong-password-123'}).status_code == 401
    assert client.post('/api/account/login', json={'username':'alice','password':PASSWORD}).status_code == 429
    for _ in range(22):
        response = client.post('/api/account/login', json={'username':'bob','password':PASSWORD})
        assert response.status_code == 200, response.text


def test_authentication_keeps_a_broad_peer_abuse_limit_and_independent_registration_budget(tmp_path, monkeypatch):
    from backend.app.services import accounts
    now = [1000.0]
    monkeypatch.setattr(accounts.time, 'monotonic', lambda:now[0])
    db = Accounts(tmp_path)
    try:
        for i in range(200):
            db.throttle('peer', f'user-{i}')
        with pytest.raises(HTTPException): db.throttle('peer', 'another-user')
        assert db.throttle('other-peer', 'another-user')
        now[0] += 901
        ticket = db.throttle('peer', 'alice')
        db.authentication_succeeded(ticket)
        assert not any(key[0] == 'login' and key[1] == 'peer' for key in db.attempts)
        for i in range(20): db.throttle('peer', f'new-{i}', register=True)
        with pytest.raises(HTTPException): db.throttle('peer', 'new-user', register=True)
        assert db.throttle('peer', 'existing-user')
    finally:
        db.close()


def test_casefolded_combining_mark_does_not_drop_a_word_boundary():
    matcher = normalized_matcher()
    pattern = repeats.pattern_for(text_key('İ'))
    assert list(matcher(pattern, 'İstanbul')) == []
    assert list(matcher(pattern, 'İ')) == [(0,1)]


def test_account_session_quota_is_per_user_and_deletion_releases_its_slot(client):
    login(client, 'alice')
    alice = [client.post('/api/sessions').json()['id'] for _ in range(3)]
    assert client.post('/api/sessions').status_code == 429
    login(client, 'bob')
    bob = [client.post('/api/sessions').json()['id'] for _ in range(3)]
    assert client.post('/api/sessions').status_code == 429
    assert client.get(f'/api/sessions/{alice[0]}').status_code == 404
    login(client, 'alice', register=False)
    assert client.delete(f'/api/sessions/{alice[0]}').status_code == 200
    assert client.post('/api/sessions').status_code == 200
    assert client.get(f'/api/sessions/{bob[0]}').status_code == 404


def test_review_serialization_error_does_not_commit_regions_or_learning(client, monkeypatch):
    from backend.app.models import Entity
    replacement = Entity.replacement
    def failing_replacement(entity, value=None):
        if entity.value == 'Synthetic failure':
            raise ValueError('Synthetic replacement failure')
        return replacement(entity, value)
    monkeypatch.setattr(Entity, 'replacement', failing_replacement)
    login(client)
    sid, before = batch(client, native_pdf([EMAIL]))
    d = before['detections'][0]
    # Simulate a public replacement failure after learning has been staged.
    response = client.post(f'/api/sessions/{sid}/detections/{d["id"]}', json={
        'type':'OTHER', 'value':'Synthetic failure', 'status':'confirmed'})
    assert response.status_code == 400, response.text
    assert client.get(f'/api/sessions/{sid}').json() == before
    assert client.get('/api/account/rules').json()['rules'] == []
