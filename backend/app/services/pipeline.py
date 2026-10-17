from zipfile import ZipFile, ZIP_DEFLATED
import re
import unicodedata
import pymupdf as fitz
from ..models import Detection, TOKEN_RE, uid, normalize
from ..detectors.rules import RuleDetector
from . import pdf, mapping, legal

DETECTOR = RuleDetector()


def analyze(session, root):
    session.detections.clear()
    session.entities.clear()
    session.warnings.clear()
    for document in session.documents:
        with fitz.open(root / f'{document.id}.pdf') as doc:
            for index, page in enumerate(doc):
                # OCR all pages, including ones with existing native/OCR text.
                sources = [('text', page.get_text('words')),
                           ('ocr', pdf.ocr(pdf.render(page), page.rect.width, page.rect.height))]
                document.pages[index]['ocr_words'] = sources[1][1]
                occurrences = []
                for source, words in sources:
                    for line in pdf.lines_from_words(words):
                        for match in DETECTOR.detect(line.text):
                            bbox = line.box(match.start, match.end)
                            rect = fitz.Rect(bbox)
                            overlaps = [d for d in occurrences if next(e for e in session.entities if e.id == d.entity_id).type == match.type
                                and (fitz.Rect(d.bbox) & rect).get_area() > min(fitz.Rect(d.bbox).get_area(), rect.get_area()) * .4]
                            if overlaps:
                                old = overlaps[0]
                                old.bbox = list(fitz.Rect(old.bbox) | rect)
                                if normalize(match.value, match.type) != old.normalized_value:
                                    old.status = 'pending'
                                    old.reason = 'Native text and visual OCR disagree; check and correct the original value'
                                    old.secondary_ocr = pdf.secondary(page, old.bbox)
                                continue
                            entity = session.entity(match.type, match.value)
                            detection = Detection(uid(), document.id, index + 1, bbox, source, .5 if match.suspicious else 1,
                                entity.id, match.value, entity.normalized_value, 'pending' if match.suspicious else 'confirmed', match.reason)
                            if match.suspicious:
                                detection.secondary_ocr = pdf.secondary(page, bbox)
                            occurrences.append(detection)
                            session.detections.append(detection)
    session.status = 'review'


def compact(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', value).casefold())


def detection_rects(detection):
    return detection.rects or [detection.bbox]


def replacement_items(detections, entities, page_info=None):
    items = []
    for detection in detections:
        entity = entities[detection.entity_id]
        rects = detection_rects(detection)
        value = (detection.original_text or entity.value) if entity.value else ''
        parts = value.split('\n')
        # Manual rectangles can contain several native text lines. Resolve their
        # geometry before placing replacements, but erase the entire drawn box.
        if len(rects) == 1 and len(parts) > 1 and page_info:
            box = fitz.Rect(rects[0])
            ids = [i for i, c in enumerate(page_info.get('native_text', []))
                   if box.contains((fitz.Rect(c['bbox']).tl + fitz.Rect(c['bbox']).br) / 2)]
            if ids:
                _, native_rects, native_value = pdf.select_native_characters(page_info, ids)
                if compact(native_value) == compact(value):
                    rects, parts = native_rects, native_value.split('\n')
        if len(parts) != len(rects):
            if len(rects) == 1:
                box = fitz.Rect(rects[0])
                height = box.height / len(parts)
                rects = [[box.x0, box.y0 + i * height, box.x1, box.y0 + (i + 1) * height]
                         for i in range(len(parts))]
            else:
                raise ValueError('Text lines do not match the selected regions; select the text again')
        # Generate once for the whole string, so line breaks do not reset the
        # character stream of a logical selection.
        replacements = entity.replacement('\n'.join(parts)).split('\n')
        items.extend(zip(rects, replacements))
    # Duplicate/contained suggestions must not print two surrogates on top of
    # each other. Partial overlaps need an explicit review rather than a string
    # concatenation which would change the selected text's structure.
    result = []
    for box, value in sorted(items, key=lambda item: fitz.Rect(item[0]).get_area(), reverse=True):
        rect = fitz.Rect(box)
        if any(fitz.Rect(other).contains(rect) for other, _ in result):
            continue
        if any(rect.intersects(fitz.Rect(other)) and pdf.same_text_row(rect, fitz.Rect(other))
               for other, _ in result):
            raise ValueError('Selected regions overlap on the same text line; ignore one or draw one combined region')
        result.append((box, value))
    return result


def verify(path, session, document):
    """Verify removal inside selected regions; content outside them is intentional.

    Generate is the user's final selection decision. An occurrence elsewhere on
    the page is allowed to remain even if its value equals a redacted entity.
    """
    entities = {e.id: e for e in session.entities}
    warnings = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc):
            selected = [d for d in session.detections if d.status == 'confirmed'
                        and d.document_id == document.id and d.page == index + 1]
            groups = replacement_items(selected, entities, document.pages[index])
            for bbox, _ in groups:
                rect = fitz.Rect(bbox)
                originals = {value for d in selected if any(rect.intersects(fitz.Rect(r)) for r in detection_rects(d))
                             for value in (d.original_text, entities[d.entity_id].value, *d.original_text.splitlines()) if compact(value)}
                image = pdf.render(page, dpi=250, clip=rect)
                words = pdf.ocr(image, rect.width, rect.height)
                texts = [page.get_textbox(rect), '\n'.join(line.text for line in pdf.lines_from_words(words))]
                for text in texts:
                    text = TOKEN_RE.sub('', text)
                    leaked = any((compact(value) in compact(text)) if len(compact(value)) >= 4 else
                                 re.search(r'(?<!\w)' + re.escape(value.strip()) + r'(?!\w)', text, re.I)
                                 for value in originals)
                    # Surrogates intentionally look like emails, phones, etc.
                    # Check source values, not whether replacement text matches
                    # a sensitive-data pattern.
                    if leaked:
                        warnings.append(dict(document_id=document.id, page=index + 1, bbox=bbox,
                                             reason='Content may remain inside a selected redaction region'))
                        break
    unique = []
    for warning in warnings:
        if warning not in unique:
            unique.append(warning)
    return unique


def generate(session, root, include_mapping=False):
    (root / 'result.zip').unlink(missing_ok=True)
    (root / 'mapping.json').unlink(missing_ok=True)
    session.status = 'review'
    session.includes_mapping = False
    # Clicking Generate accepts the current selection. Unresolved suggestions
    # become explicit ignores and are excluded from both redaction and mapping.
    for detection in session.detections:
        if detection.status == 'pending':
            detection.status = 'ignored'
            detection.reason = 'Not selected; left visible when Generate was clicked'
    if not session.documents:
        raise ValueError('Upload and analyze a PDF first')
    entities = {e.id: e for e in session.entities}
    session.warnings.clear()
    generated = []
    for number, document in enumerate(session.documents, 1):
        destination = root / f'output-{document.id}.pdf'
        with fitz.open(root / f'{document.id}.pdf') as source, fitz.open() as output:
            for index, page in enumerate(source):
                selected = [d for d in session.detections
                            if d.document_id == document.id and d.page == index+1 and d.status == 'confirmed']
                items = replacement_items(selected, entities, document.pages[index])
                groups = items
                image = pdf.canonical_image(page)
                pdf.erase(image, page.rect.width, page.rect.height, [box for d in selected for box in detection_rects(d)])
                pdf.fresh_page(output, image, page.rect.width, page.rect.height, groups, document.pages[index])
            output.set_metadata(legal.pdf_metadata())
            output.save(destination, garbage=4, deflate=True)
        session.warnings.extend(verify(destination, session, document))
        generated.append((destination, f'documents/document-{number:02d}.anonymized.pdf'))
    if session.warnings:
        session.status = 'verification_warning'
        return
    if include_mapping:
        (root / 'mapping.json').write_bytes(mapping.serialize(session))
    if len(generated) > 1:
        with ZipFile(root / 'result.zip', 'w', ZIP_DEFLATED) as archive:
            if include_mapping:
                archive.write(root / 'mapping.json', 'mapping.json')
            for path, name in generated:
                archive.write(path, name)
    session.includes_mapping = include_mapping
    session.status = 'complete'

