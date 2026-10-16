"""Apply a snapshot of one account's preferences after independent local OCR."""
import re
import pymupdf as fitz
from ..models import Detection, uid
from . import repeats
from .accounts import text_key


def apply(session, rules):
    # Longer phrases take priority over contained phrases; Ignore wins overlaps.
    ordered = sorted(rules, key=lambda r: (r['action'] == 'ignore', len(r['text'])), reverse=True)
    blocked = []
    for rule in ordered:
        pattern = repeats.pattern_for(rule['text'])
        if not pattern:
            continue
        pattern = re.compile(pattern.pattern, re.IGNORECASE)
        for document in session.documents:
            for number, info in enumerate(document.pages, 1):
                candidates = list(repeats.native_occurrences(info, pattern))
                for candidate in repeats.ocr_occurrences(info, pattern):
                    if not any(repeats.overlap(candidate[0], c[0]) > .5 for c in candidates):
                        candidates.append(candidate)
                for box, rects, original in candidates:
                    if any(did == document.id and page == number and repeats.overlap(box, b) > .1
                           for did, page, b in blocked):
                        continue
                    if rule['action'] == 'ignore':
                        blocked.append((document.id, number, box))
                        session.detections[:] = [d for d in session.detections if not (
                            d.document_id == document.id and d.page == number and
                            (text_key(d.original_text) == text_key(original) or
                             all(any(fitz.Rect(r).contains(fitz.Rect(dr)) for r in rects) for dr in (d.rects or [d.bbox]))))]
                        continue
                    entity = session.entity(rule['type'], original)
                    # Replace the same suggestion, retaining exact native geometry.
                    session.detections[:] = [d for d in session.detections if not (
                        d.document_id == document.id and d.page == number and
                        text_key(d.original_text) == text_key(original) and repeats.overlap(d.bbox, box) > .5)]
                    session.detections.append(Detection(uid(), document.id, number, box, 'personal_rule', 1,
                        entity.id, original, entity.normalized_value, 'confirmed',
                        'Saved rule: ' + (rule['label'] or rule['type']), rects=rects))
                    blocked.append((document.id, number, box))
        if rule['action'] == 'ignore':
            # Also suppress an exact deterministic detection if OCR word boxes
            # could not safely resolve the saved phrase (e.g. punctuation).
            session.detections[:] = [d for d in session.detections if text_key(d.original_text) != text_key(rule['text'])]
