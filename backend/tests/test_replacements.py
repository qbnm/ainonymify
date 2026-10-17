import json
import unicodedata
import pytest
from backend.app.models import Session, Detection, uid
from backend.app.services import mapping, pipeline


def shape(value):
    return [(unicodedata.category(c) if c.isalnum() else c) for c in value]


@pytest.mark.parametrize('original', [
    'Muster Str. 211', 'Müller GmbH, Straße 3a', 'ALEx@example.test',
    '+49 (30) 123-456/78', 'DE02 1203 0000 0000 2020 51',
    'Адрес: Улица ٣١', 'Αθήνα 12', '漢字１２', 'A  B\tC.\nD-9',
])
def test_replacements_preserve_character_groups_and_all_separators(original):
    entity = Session().entity('OTHER', original)
    replacement = entity.replacement()
    assert len(replacement) == len(original)
    assert shape(replacement) == shape(original)
    assert all(a != b for a, b in zip(original, replacement) if a.isalnum())
    assert entity.replacement() == replacement


def test_normalized_variants_and_session_isolation():
    session = Session()
    entity = session.entity('IBAN', 'DE02 1203 0000 0000 2020 51')
    assert session.entity('IBAN', 'de02120300000000202051') is entity
    assert entity.replacement().replace(' ', '').lower() == entity.replacement('de02120300000000202051')
    assert Session().entity('IBAN', entity.value).replacement() != entity.replacement()
    assert 'replacement_key' not in json.dumps(session.public())
    assert session.public()['entities'][0]['replacement'] == entity.replacement()


def test_private_reference_records_actual_occurrence_variants():
    session = Session()
    entity = session.entity('EMAIL', 'Alex@example.test')
    for value in ['Alex@example.test', 'ALEX@EXAMPLE.TEST']:
        session.detections.append(Detection(uid(), 'doc', 1, [0,0,90,20], 'text', 1,
            entity.id, value, entity.normalized_value))
    data = json.loads(mapping.serialize(session))
    item = data['entities'][0]
    assert item['replacement'] == entity.replacement()
    assert item['occurrences'] == [dict(original=d.original_text, replacement=entity.replacement(d.original_text))
                                   for d in session.detections]
    assert 'replacement_key' not in json.dumps(data)


def test_partial_overlaps_require_review_and_contained_duplicates_print_once():
    session = Session(); entity = session.entity('OTHER', 'ABC123')
    a = Detection(uid(), 'doc', 1, [0,0,60,20], 'text', 1, entity.id, entity.value, entity.normalized_value)
    b = Detection(uid(), 'doc', 1, [0,0,60,20], 'text', 1, entity.id, entity.value, entity.normalized_value)
    assert len(pipeline.replacement_items([a,b], {entity.id: entity})) == 1
    b.bbox = [40,0,90,20]
    with pytest.raises(ValueError, match='overlap'):
        pipeline.replacement_items([a,b], {entity.id: entity})
