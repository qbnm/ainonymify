"""Apply a snapshot of one account's preferences after independent local OCR."""
import pymupdf as fitz
from ..models import Detection, uid
from . import repeats
from .text_matching import text_key, normalized_matcher


def fragments_overlap(left, right, threshold=.1):
    return any(repeats.overlap(a, b) > threshold for a in left for b in right)


def apply(session, rules):
    # Longer phrases take priority over contained phrases; Ignore wins overlaps.
    ordered = sorted(rules, key=lambda r: (r['action'] == 'ignore', len(r['text'])), reverse=True)
    compiled = [(rule, repeats.pattern_for(text_key(rule['text']))) for rule in ordered]
    for document in session.documents:
        for number, info in enumerate(document.pages, 1):
            blocked = []
            matcher = normalized_matcher()
            for rule, pattern in compiled:
                if not pattern:
                    continue
                candidates = list(repeats.native_occurrences(info, pattern, matcher))
                for candidate in repeats.ocr_occurrences(info, pattern, matcher):
                    if not any(fragments_overlap(candidate[1], c[1], .5) for c in candidates):
                        candidates.append(candidate)
                for box, rects, original in candidates:
                    if any(fragments_overlap(rects, fragments) for fragments in blocked):
                        continue
                    if rule['action'] == 'ignore':
                        blocked.append(rects)
                        session.detections[:] = [d for d in session.detections if not (
                            d.document_id == document.id and d.page == number and
                            (text_key(d.original_text) == text_key(original) or
                             all(any(fitz.Rect(r).contains(fitz.Rect(dr)) for r in rects) for dr in (d.rects or [d.bbox]))))]
                        continue
                    entity = session.entity(rule['type'], original)
                    # Replace the same suggestion, retaining exact source geometry.
                    session.detections[:] = [d for d in session.detections if not (
                        d.document_id == document.id and d.page == number and
                        text_key(d.original_text) == text_key(original) and
                        fragments_overlap(d.rects or [d.bbox], rects, .5))]
                    session.detections.append(Detection(uid(), document.id, number, box, 'personal_rule', 1,
                        entity.id, original, entity.normalized_value, 'confirmed',
                        'Saved rule: ' + (rule['label'] or rule['type']), rects=rects))
                    blocked.append(rects)
                if rule['action'] == 'ignore':
                    # Suppress exact deterministic suggestions even if OCR could
                    # not safely resolve their complete word geometry.
                    session.detections[:] = [d for d in session.detections if not (
                        d.document_id == document.id and d.page == number and
                        text_key(d.original_text) == text_key(rule['text']))]
