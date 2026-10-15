import pymupdf as fitz
import pytest
from backend.app.models import Session, Document, uid
from backend.app.services import pdf, pipeline, mapping
from .conftest import native_pdf, scanned_pdf, IBAN, EMAIL


def prepare(tmp_path, raws):
    session=Session()
    for raw in raws:
        did=uid(); pages=pdf.canonicalize(raw,tmp_path/f'{did}.pdf')
        session.documents.append(Document(did,'private-name.pdf',pages))
    pipeline.analyze(session,tmp_path)
    return session


def approve(session):
    for d in session.detections: d.status='confirmed'


@pytest.mark.parametrize('kind',['native','scan','hybrid','broken'])
def test_redaction_native_scanned_hybrid_and_broken_ocr(tmp_path,kind):
    raw=native_pdf() if kind=='native' else scanned_pdf(hybrid=kind in ('hybrid','broken'),broken=kind=='broken')
    session=prepare(tmp_path,[raw])
    assert any(e.type=='IBAN' for e in session.entities)
    assert any(e.type=='EMAIL' for e in session.entities)
    if kind=='broken':
        assert any(d.status=='pending' and ('OCR' in d.reason or 'validation' in d.reason) for d in session.detections)
        # Human confirms the visually correct reference value.
        for e in session.entities:
            if e.type=='IBAN': e.value=IBAN
    approve(session)
    pipeline.generate(session,tmp_path)
    assert session.status=='complete',session.warnings
    assert not (tmp_path/'result.zip').exists()
    assert not (tmp_path/'mapping.json').exists()
    assert session.includes_mapping is False
    output=(tmp_path/f'output-{session.documents[0].id}.pdf').read_bytes()
    with fitz.open(stream=output,filetype='pdf') as doc:
        text=''.join(p.get_text() for p in doc)
        assert IBAN not in text and EMAIL not in text
        assert next(e for e in session.entities if e.type=='IBAN').replacement(next(d.original_text for d in session.detections if d.entity_id==next(e.id for e in session.entities if e.type=='IBAN'))) in text
        assert not any(v for k,v in doc.metadata.items() if k not in ('format','encryption'))
        assert doc.embfile_count()==0
        image=pdf.render(doc[0])
        visual=' '.join(w[4] for w in pdf.ocr(image,doc[0].rect.width,doc[0].rect.height))
        assert IBAN not in visual.replace(' ','') and EMAIL not in visual
        if kind=='native': image.save(tmp_path/'anonymized-preview.png')


def test_batch_five_documents_share_tokens(tmp_path):
    session=prepare(tmp_path,[native_pdf() for _ in range(5)])
    ibans=[e for e in session.entities if e.type=='IBAN']
    assert len(ibans)==1
    assert len({d.entity_id for d in session.detections if d.entity_id==ibans[0].id})==1
    assert sum(d.entity_id==ibans[0].id for d in session.detections)==5


def test_generate_ignores_unselected_regions_and_has_no_page_gate(tmp_path):
    session=prepare(tmp_path,[native_pdf()])
    email=next(e for e in session.entities if e.type=='EMAIL')
    for detection in session.detections:
        if detection.entity_id==email.id: detection.status='pending'
    pipeline.generate(session,tmp_path,include_mapping=True)
    assert session.status=='complete' and not session.warnings
    assert all(d.status=='ignored' for d in session.detections if d.entity_id==email.id)
    assert not (tmp_path/'result.zip').exists()
    assert email.token not in mapping.load((tmp_path/'mapping.json').read_bytes())
    with fitz.open(stream=(tmp_path/f'output-{session.documents[0].id}.pdf').read_bytes(),filetype='pdf') as doc:
        text=doc[0].get_text()
        assert EMAIL in text and IBAN not in text and next(e for e in session.entities if e.type=='IBAN').replacement() in text


def test_same_value_elsewhere_is_left_visible(tmp_path):
    session=prepare(tmp_path,[native_pdf([EMAIL,EMAIL])])
    session.detections=session.detections[:1]
    pipeline.generate(session,tmp_path)
    assert session.status=='complete' and not session.warnings
    with fitz.open(tmp_path/f'output-{session.documents[0].id}.pdf') as doc:
        text=doc[0].get_text()
        assert EMAIL in text and session.entities[0].replacement(session.detections[0].original_text) in text


def test_selected_region_leak_still_blocks_download(tmp_path,monkeypatch):
    session=prepare(tmp_path,[native_pdf()])
    # Simulate the removal operation failing: selected content must still be checked.
    monkeypatch.setattr(pdf,'erase',lambda *args:None)
    pipeline.generate(session,tmp_path)
    assert session.status=='verification_warning'
    assert session.warnings and not (tmp_path/'result.zip').exists()


def test_no_regions_selected_exports_visible_content_with_empty_mapping(tmp_path):
    session=prepare(tmp_path,[native_pdf()])
    for detection in session.detections: detection.status='pending'
    pipeline.generate(session,tmp_path,include_mapping=True)
    assert session.status=='complete'
    assert not (tmp_path/'result.zip').exists()
    assert mapping.load((tmp_path/'mapping.json').read_bytes())=={}
    with fitz.open(stream=(tmp_path/f'output-{session.documents[0].id}.pdf').read_bytes(),filetype='pdf') as doc:
        assert IBAN in doc[0].get_text() and EMAIL in doc[0].get_text()


def test_metadata_annotations_and_attachments(tmp_path):
    with fitz.open(stream=native_pdf(),filetype='pdf') as doc:
        doc.set_metadata({'author':EMAIL,'title':IBAN})
        doc[0].add_text_annot((300,200),EMAIL)
        raw=doc.tobytes()
    session=prepare(tmp_path,[raw]);approve(session);pipeline.generate(session,tmp_path)
    assert session.status=='complete',session.warnings
    with fitz.open(tmp_path/f'output-{session.documents[0].id}.pdf') as out:
        assert not any(v for k,v in out.metadata.items() if k not in ('format','encryption'))
        assert not list(out[0].annots() or [])
    with fitz.open(stream=native_pdf(),filetype='pdf') as doc:
        doc.embfile_add('secret.txt',b'secret')
        with pytest.raises(ValueError,match='attachments'):pdf.canonicalize(doc.tobytes(),tmp_path/'bad.pdf')


def test_rotated_page_coordinates(tmp_path):
    session=prepare(tmp_path,[native_pdf(rotation=90)])
    assert any(e.type=='IBAN' for e in session.entities)
    approve(session);pipeline.generate(session,tmp_path)
    assert session.status=='complete',session.warnings


def test_names_addresses_tax_and_private_unicode_reference(tmp_path):
    originals=['Anna Müller','Musterstrasse 12, 10115 Berlin','12/345/67890']
    raw=native_pdf(['Name: '+originals[0], originals[1], 'Steuernummer: '+originals[2], 'IBAN: '+IBAN])
    original=tmp_path/'original.pdf';original.write_bytes(raw)
    session=prepare(tmp_path,[original.read_bytes()])
    assert {'PERSON','ADDRESS','TAX_ID','IBAN'} <= {e.type for e in session.entities}
    approve(session);pipeline.generate(session,tmp_path,include_mapping=True)
    assert session.status=='complete' and session.includes_mapping,session.warnings
    assert not (tmp_path/'result.zip').exists()
    reference=mapping.load((tmp_path/'mapping.json').read_bytes())
    for value in originals+[IBAN]: assert value in reference.values()
    with fitz.open(stream=(tmp_path/f'output-{session.documents[0].id}.pdf').read_bytes(),filetype='pdf') as doc:
        text=' '.join(doc[0].get_text().split())
        for value in originals+[IBAN]: assert value not in text
        assert next(e for e in session.entities if e.type=='PERSON').replacement() in text
    assert original.read_bytes()==raw
