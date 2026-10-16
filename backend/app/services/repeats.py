"""Expand explicit text confirmations to matching occurrences in one PDF."""
import re
import unicodedata
import pymupdf as fitz
from ..models import Detection, uid
from . import pdf


def text_key(value):
    return ' '.join(value.split())


def pattern_for(value):
    parts = value.split()
    if not parts or not any(c.isalnum() for c in value):
        return None
    expression = r'\s+'.join(re.escape(part) for part in parts)
    # A name/number must not select the prefix of a different name/number.
    first = next((c for c in parts[0] if not unicodedata.category(c).startswith('M')), parts[0][0])
    last = next((c for c in reversed(parts[-1]) if not unicodedata.category(c).startswith('M')), parts[-1][-1])
    if re.match(r'\w', first):
        expression = r'(?<!\w)' + expression
    if re.match(r'\w', last):
        expression += r'(?!\w)'
    return re.compile(expression)


def overlap(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    smaller = min(a.get_area(), b.get_area())
    return (a & b).get_area() / smaller if smaller else 0


def same_occurrence(a, b):
    return (a.document_id == b.document_id and a.page == b.page
            and text_key(a.original_text) == text_key(b.original_text)
            and overlap(a.bbox, b.bbox) > .7)


def add_selection(session, selection):
    # Reconfirming an existing text selection updates it instead of creating
    # duplicate boxes that would print duplicate replacements.
    existing = next((d for d in session.detections if same_occurrence(d, selection)), None)
    if existing:
        identity = existing.id
        existing.__dict__.update(selection.__dict__)
        existing.id = identity
        return existing
    session.detections.append(selection)
    return selection


def native_occurrences(info, pattern, matcher=None):
    chars = info.get('native_text', [])
    text, indexes, previous_line = [], [], None
    for i, char in enumerate(chars):
        if previous_line is not None and char['line'] != previous_line:
            text.append('\n'); indexes.append(None)
        text.append(char['text']); indexes.append(i)
        previous_line = char['line']
    text = ''.join(text)
    matches = matcher(pattern, text) if matcher else (m.span() for m in pattern.finditer(text))
    for start, end in matches:
        ids = [i for i in indexes[start:end] if i is not None]
        if ids:
            yield pdf.select_native_characters(info, ids)


def ocr_occurrences(info, pattern, matcher=None):
    text, spans, lines = '', [], pdf.lines_from_words(info.get('ocr_words', []))
    for line_number, line in enumerate(lines):
        if text:
            text += '\n'
        offset = len(text)
        text += line.text
        spans.extend((offset + a, offset + b, bbox, line_number) for a, b, bbox in line.spans)
    matches = matcher(pattern, text) if matcher else (m.span() for m in pattern.finditer(text))
    for start, end in matches:
        selected = [(a, b, bbox, line) for a, b, bbox, line in spans if a < end and b > start]
        # OCR supplies word boxes, so only complete words can be selected safely.
        if not selected or selected[0][0] != start or selected[-1][1] != end:
            continue
        # Preserve line-level spaces and use separate boxes across lines.
        groups = []
        for a, b, bbox, line in selected:
            if groups and groups[-1][0] == line:
                groups[-1][2] = b
                groups[-1][3] |= fitz.Rect(bbox)
            else:
                groups.append([line, a, b, fitz.Rect(bbox)])
        rects = [list(rect) for _, _, _, rect in groups]
        box = fitz.Rect(rects[0])
        for rect in rects[1:]:
            box |= fitz.Rect(rect)
        yield list(box), rects, '\n'.join(text[a:b] for _, a, b, _ in groups)



def mark_repeats(session, document, seed):
    """Case-sensitive text matching; only whitespace differences are tolerated.

    Explicit Ignore is local. A later explicit confirmation marks all matches
    again. Splitting an entity remains a local action and bypasses this helper.
    """
    entity = next(e for e in session.entities if e.id == seed.entity_id)
    if not entity.value or seed.status != 'confirmed':
        return
    pattern = pattern_for(seed.original_text or entity.value)
    if pattern is None:
        return
    for page_number, info in enumerate(document.pages, 1):
        candidates = list(native_occurrences(info, pattern))
        # OCR may find text embedded as an image on a native-text page too.
        for candidate in ocr_occurrences(info, pattern):
            if not any(overlap(candidate[0], native[0]) > .5 for native in candidates):
                candidates.append(candidate)
        for box, rects, original in candidates:
            candidate = Detection(uid(), document.id, page_number, box, 'repeated_text', 1,
                entity.id, original, entity.normalized_value, 'confirmed',
                'Repeated text: selected automatically after confirmation elsewhere in this document', rects=rects)
            existing = [d for d in session.detections if same_occurrence(d, candidate)]
            if existing:
                for detection in existing:
                    detection.entity_id = entity.id
                    detection.normalized_value = entity.normalized_value
                    detection.status = 'confirmed'
                continue
            # An existing broader selection already removes this occurrence.
            # Adding another overlapping label would corrupt the output layout.
            if any(d.document_id == document.id and d.page == page_number and d.status == 'confirmed'
                   and any(fitz.Rect(r).contains(fitz.Rect(box)) for r in (d.rects or [d.bbox]))
                   for d in session.detections):
                continue
            session.detections.append(candidate)
