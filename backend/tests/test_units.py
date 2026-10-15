import json
import time
import pytest
from backend.app.models import Session, Detection, normalize, uid
from backend.app.detectors.rules import valid_iban, ocr_candidates, RuleDetector
from backend.app.services import mapping
from backend.app.services.sessions import Store


def test_iban_normalization_and_checksums():
    assert normalize('DE02 1203 0000 0000 2020 51', 'IBAN') == 'DE02120300000000202051'
    assert valid_iban('DE02 1203 0000 0000 2020 51')
    assert valid_iban('GB82 WEST 1234 5698 7654 32')
    assert not valid_iban('DE03120300000000202051')
    assert not valid_iban('DE0212030000000020205')
    assert not valid_iban('ZZ02120300000000202051')


def test_ocr_confusion_never_confirmed():
    candidates = ocr_candidates('DEO2I2O3OOOOOOOO2O2O5I')
    assert candidates == ['DE02120300000000202051']
    matches = RuleDetector().detect('IBAN: DEO2I2O3OOOOOOOO2O2O5I')
    assert any(m.type == 'IBAN' and m.suspicious for m in matches)


def test_rules_and_phone_precedence():
    matches = RuleDetector().detect('IBAN: DE02 1203 0000 0000 2020 51\nEmail: alex@example.test\nBIC: BYLADEM1001\nUSt-IdNr.: DE136695976\nTelefon: +49 30 12345678\nSteuernummer: 12/345/67890\nMusterstraße 12, 10115 Berlin\nObjekt: Haus 12')
    assert {'IBAN','EMAIL','BIC','VAT_ID','PHONE','TAX_ID','ADDRESS','PROPERTY'} <= {m.type for m in matches}
    assert not any(m.type == 'PHONE' and '2020' in m.value for m in matches)


def test_session_entities_and_token_uniqueness():
    a, b = Session(), Session()
    first = a.entity('COMPANY', 'Müller Immobilien GmbH')
    assert a.entity('COMPANY', 'MÜLLER  IMMOBILIEN GMBH').id == first.id
    assert a.entity('COMPANY', 'Mueller Immobilien GmbH').id != first.id
    assert a.entity('COMPANY', first.value, separate=True).token == '{{COMPANY_003}}'
    assert a.entity('IBAN', 'DE02 1203 0000 0000 2020 51').id == a.entity('IBAN', 'DE02120300000000202051').id
    assert b.entity('COMPANY', first.value).id != first.id
    assert len({e.token for e in a.entities}) == len(a.entities)


def sample_mapping():
    s = Session(); e = s.entity('EMAIL','alex@example.test')
    s.detections.append(Detection(uid(),'doc',1,[0,0,20,20],'text',1,e.id,e.value,e.normalized_value))
    return mapping.serialize(s)


def test_mapping_serialization_and_corruption():
    raw = sample_mapping()
    assert mapping.load(raw) == {'{{EMAIL_001}}':'alex@example.test'}
    data = json.loads(raw); data['entities'][0]['value']='modified'
    with pytest.raises(ValueError, match='integrity'): mapping.load(json.dumps(data))
    with pytest.raises(ValueError): mapping.load(b'not json')
    data=json.loads(raw); data['version']=2
    with pytest.raises(ValueError,match='version'): mapping.load(json.dumps(data))


@pytest.mark.parametrize('change', ['duplicate','token','type','value'])
def test_mapping_invalid_with_recomputed_checksum(change):
    data=json.loads(sample_mapping()); data.pop('sha256')
    if change=='duplicate': data['entities'].append(data['entities'][0].copy())
    if change=='token': data['entities'][0]['token']='EMAIL_001'
    if change=='type': data['entities'][0]['type']='iban'
    if change=='value': data['entities'][0]['value']='{{PERSON_001}}'
    data['sha256']=mapping.digest(data)
    with pytest.raises(ValueError): mapping.load(json.dumps(data))


def test_expiry_isolation_and_delete(tmp_path):
    store=Store(ttl=30,root=tmp_path)
    a,b=store.create(),store.create()
    (store.root/a.id/'secret.pdf').write_bytes(b'private')
    store.items[a.id]=(a,time.monotonic()-31)
    store.cleanup()
    assert not (store.root/a.id).exists()
    with pytest.raises(KeyError):
        with store.access(a.id): pass
    with store.access(b.id) as (session,path): assert session.id==b.id
    store.delete(b.id); assert not (store.root/b.id).exists()
    store.close()
