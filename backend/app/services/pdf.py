"""Canonical, display-coordinate PDFs and bounded local OCR.

Exports contain only newly encoded page pixels, fresh OCR and visible replacements.
Source PDF objects are never copied into an export.
"""
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import os
import math
import re
import pymupdf as fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

DPI = 200
OCR_LANG = os.getenv('OCR_LANGUAGES', 'deu+eng')
FONT_PATH = os.getenv('FONT_PATH', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
if not Path(FONT_PATH).exists():
    FONT_PATH = '/System/Library/Fonts/Supplemental/Arial.ttf'
MAX_PAGES = int(os.getenv('MAX_PAGES', '60'))
MAX_PIXELS = 24_000_000
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)


@dataclass
class Line:
    text: str
    spans: list  # (start, end, bbox)

    def box(self, start, end):
        rect = fitz.Rect()
        for a, b, bbox in self.spans:
            if a < end and b > start:
                rect |= fitz.Rect(bbox)
        return list(rect)


def lines_from_words(words):
    grouped = defaultdict(list)
    for x0, y0, x1, y1, value, block, line, *_ in words:
        grouped[(block, line)].append((x0, y0, x1, y1, value))
    result = []
    for group in grouped.values():
        text, spans = '', []
        for x0, y0, x1, y1, value in group:
            if text:
                text += ' '
            start = len(text)
            text += value
            spans.append((start, len(text), [x0, y0, x1, y1]))
        result.append(Line(text, spans))
    return result


def render(page, dpi=DPI, clip=None):
    rect = clip or page.rect
    if rect.width * rect.height * (dpi / 72) ** 2 > MAX_PIXELS:
        raise ValueError('Page or region exceeds the rendering limit')
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False, clip=clip)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def ocr(image, width, height, psm=3):
    data = pytesseract.image_to_data(image, lang=OCR_LANG, config=f'--psm {psm}',
                                    output_type=pytesseract.Output.DICT, timeout=90)
    words = []
    for i, value in enumerate(data['text']):
        if not value.strip():
            continue
        x, y, w, h = (data[key][i] for key in ('left', 'top', 'width', 'height'))
        words.append((x * width / image.width, y * height / image.height,
                      (x+w) * width / image.width, (y+h) * height / image.height,
                      value, (data['block_num'][i], data['par_num'][i]), data['line_num'][i], i))
    return words


def put_text(page, box, value, invisible=False, min_size=3, style=None):
    rect = fitz.Rect(box)
    if invisible:
        value = ' '.join(value.split())
    if not value.strip():
        return
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError('Invalid replacement region')
    page.insert_font(fontname='local', fontfile=FONT_PATH)
    font = fitz.Font(fontfile=FONT_PATH)
    if invisible:
        # Preserve the original word rectangle exactly, including horizontal scale.
        # Otherwise text search can return a narrower box than the visible pixels.
        size = rect.height / (font.ascender - font.descender)
        point = fitz.Point(rect.x0, rect.y0 + font.ascender * size)
        width = max(font.text_length(value, fontsize=size), .001)
        page.insert_text(point, value, fontsize=size, fontname='local', render_mode=3,
                         morph=(point, fitz.Matrix(rect.width / width, 1)))
        return
    if style:
        font_path = matching_font(style)
        name = 'replacement_' + Path(font_path).stem.replace('-', '_')
        page.insert_font(fontname=name, fontfile=str(font_path))
        font = fitz.Font(fontfile=str(font_path))
        rotation = style['rotation']
        available = rect.height if rotation in (90, 270) else rect.width
        size = min(style['size'], available / max(font.text_length(value, fontsize=1), 1))
        baseline = style['baseline']
        point = {0:(rect.x0, baseline), 90:(baseline, rect.y1),
                 180:(rect.x1, baseline), 270:(baseline, rect.y0)}[rotation]
        color = tuple(((style['color'] >> shift) & 255) / 255 for shift in (16, 8, 0))
    else:
        vertical = rect.height > rect.width * 2
        rotation = 90 if vertical else 0
        width, height = (rect.height, rect.width) if vertical else (rect.width, rect.height)
        size = min(11, height / (font.ascender - font.descender),
                   width / max(font.text_length(value, fontsize=1), 1))
        point = (rect.x0 + font.ascender * size, rect.y1) if vertical else (rect.x0, rect.y0 + font.ascender * size)
        name, color = 'local', (0, 0, 0)
    if size < min_size:
        raise ValueError('Replacement is too long for this region; enlarge the region or shorten the value')
    page.insert_text(point, value, fontsize=size, fontname=name, rotate=rotation, color=color)


def fresh_page(output, image, width, height, replacements=(), page_info=None):
    page = output.new_page(width=width, height=height)
    png = BytesIO()
    image.save(png, format='PNG')
    page.insert_image(page.rect, stream=png.getvalue())
    # OCR happens AFTER removal and BEFORE visible token insertion.
    for word in ocr(image, width, height):
        put_text(page, word[:4], word[4], invisible=True, min_size=0)
    for bbox, value in replacements:
        put_text(page, bbox, value, style=token_style(page_info, bbox))
    return page


def canonicalize(raw, destination):
    try:
        source = fitz.open(stream=raw, filetype='pdf')
    except Exception as exc:
        raise ValueError('Unreadable PDF') from exc
    with source, fitz.open() as out:
        if source.is_encrypted:
            raise ValueError('Password-protected PDFs are not supported')
        if source.embfile_count() or any(a.type[0] == fitz.PDF_ANNOT_FILE_ATTACHMENT for p in source for a in (p.annots() or [])):
            raise ValueError('PDF contains embedded attachments; remove them before uploading')
        if not 0 < len(source) <= MAX_PAGES:
            raise ValueError('PDF page count exceeds the configured limit')
        pages = []
        for original in source:
            image = render(original)
            width, height = original.rect.width, original.rect.height
            page = out.new_page(width=width, height=height)
            buf = BytesIO()
            image.save(buf, format='PNG')
            page.insert_image(page.rect, stream=buf.getvalue())
            for word in original.get_text('words'):
                rect = fitz.Rect(word[:4]) * original.rotation_matrix
                # Native words are hints only; analysis also independently OCRs pixels.
                put_text(page, list(rect), word[4], invisible=True, min_size=0)
            native = native_characters(original)
            pages.append(dict(width=width, height=height, native_text=native, text_selectable=bool(native)))
        from .legal import pdf_metadata
        out.set_metadata(pdf_metadata())
        out.save(destination, garbage=4, deflate=True)
        return pages


def secondary(page, bbox):
    rect = (fitz.Rect(bbox) + (-3, -3, 3, 3)) & page.rect
    image = render(page, dpi=300, clip=rect)
    gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(2)
    texts = []
    for variant in (gray, gray.point(lambda p: 255 if p > 160 else 0)):
        texts.append(pytesseract.image_to_string(variant, lang=OCR_LANG, config='--psm 6', timeout=30).strip())
    return '\n'.join(dict.fromkeys(texts))[:4096]


def erase(image, width, height, boxes):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    for box in boxes:
        r = fitz.Rect(box)
        draw.rectangle((int(r.x0 * image.width / width), int(r.y0 * image.height / height),
                        int(r.x1 * image.width / width + 1), int(r.y1 * image.height / height + 1)), fill='white')


def group_replacements(items):
    """Intersecting detections are removed as one region, with each token once."""
    groups = []
    for box, token in items:
        rect, tokens = fitz.Rect(box), [token] if token else []
        changed = True
        while changed:
            changed = False
            for i, (other, values) in enumerate(groups):
                if rect.intersects(other) and same_text_row(rect, other):
                    rect |= other
                    tokens = list(dict.fromkeys(tokens + values))
                    groups.pop(i)
                    changed = True
                    break
        groups.append((rect, tokens))
    return [(list(rect), ' '.join(tokens)) for rect, tokens in groups]


def native_characters(page):
    """Visible source glyphs only. Hidden OCR (render mode 3) is not selectable."""
    visible = {(code, round(origin[0], 3), round(origin[1], 3))
               for span in page.get_texttrace() if span['type'] != 3 and span.get('opacity', 1) > 0
               for code, _, origin, _ in span['chars']}
    result = []
    for bi, block in enumerate(page.get_text('rawdict', sort=True, flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']):
        for li, line in enumerate(block.get('lines', [])):
            for span in line['spans']:
                for char in span['chars']:
                    text = char['c']
                    origin = char['origin']
                    if len(text) != 1 or (ord(text), round(origin[0], 3), round(origin[1], 3)) not in visible:
                        continue
                    rect = (fitz.Rect(char['bbox']) * page.rotation_matrix) & page.rect
                    if rect.is_empty or rect.is_infinite:
                        continue
                    direction = fitz.Point(line['dir']) * page.rotation_matrix - fitz.Point(0, 0) * page.rotation_matrix
                    origin = fitz.Point(char['origin']) * page.rotation_matrix
                    result.append(dict(text=text, bbox=list(rect), line=f'{bi}:{li}',
                                       font=span['font'], size=span['size'], color=span['color'],
                                       flags=span['flags'], origin=list(origin), direction=list(direction)))
                    if len(result) > 20000:
                        # Dense pathological pages remain available for rectangle review.
                        return []
    return result


def select_native_characters(page_info, character_ids):
    """Resolve selection against server-owned source glyph geometry, never client boxes."""
    chars = page_info.get('native_text', [])
    ids = sorted(set(character_ids))
    if not chars or not ids or ids[0] < 0 or ids[-1] >= len(chars):
        raise ValueError('Select visible native text on this page')
    groups, previous = [], None
    for index in ids:
        char = chars[index]
        rect = fitz.Rect(char['bbox'])
        join = previous is not None and index == previous + 1 and char['line'] == chars[previous]['line']
        if join:
            last = groups[-1][0]
            gap = max(0, rect.x0-last.x1, last.x0-rect.x1, rect.y0-last.y1, last.y0-rect.y1)
            join = gap <= min(rect.height, last.height) * .8
        if join:
            groups[-1][0] |= rect
            groups[-1][1] += char['text']
        else:
            groups.append([rect, char['text']])
        previous = index
    groups = [(r, text) for r, text in groups if text.strip()]
    if not groups:
        raise ValueError('Select at least one non-space character')
    bbox = fitz.Rect()
    for rect, _ in groups:
        bbox |= rect
    return list(bbox), [list(rect) for rect, _ in groups], '\n'.join(text for _, text in groups)


def same_text_row(a, b):
    """Font ascender/descender rectangles can overlap across distinct lines."""
    vertical = a.height > a.width * 2 and b.height > b.width * 2
    if vertical:
        return abs((a.x0+a.x1-b.x0-b.x1)/2) < .35*min(a.width,b.width)
    return abs((a.y0+a.y1-b.y0-b.y1)/2) < .35*min(a.height,b.height)


def canonical_image(page):
    """Read our single canonical page bitmap without a second interpolation pass."""
    images = page.get_images(full=True)
    if len(images) != 1:
        raise ValueError('Unexpected canonical page image structure')
    data = page.parent.extract_image(images[0][0])['image']
    with Image.open(BytesIO(data)) as image:
        return image.convert('RGB')


def token_style(page_info, bbox):
    if not page_info:
        return None
    rect = fitz.Rect(bbox)
    chars = [c for c in page_info.get('native_text', [])
             if c['text'].strip() and 'origin' in c and rect.intersects(fitz.Rect(c['bbox']))]
    if not chars:
        return None
    scores = defaultdict(float)
    for character in chars:
        scores[character['line']] += (rect & fitz.Rect(character['bbox'])).get_area()
    line = max(scores, key=scores.get)
    chars = [c for c in chars if c['line']==line]
    character = max(chars, key=lambda c:(rect & fitz.Rect(c['bbox'])).get_area())
    dx, dy = character['direction']
    rotation = int(round(-math.degrees(math.atan2(dy,dx))/90)*90)%360
    baseline = character['origin'][0 if rotation in (90,270) else 1]
    return dict(font=character['font'],size=character['size'],color=character['color'],
                flags=character['flags'],rotation=rotation,baseline=baseline)


def matching_font(style):
    # Complete local fonts are needed: source embedded subsets often omit braces
    # and uppercase letters used in tokens. Never carry source font objects over.
    family = style['font'].lower()
    flags = style.get('flags',0)
    bold, italic = bool(flags & 16) or 'bold' in family, bool(flags & 2) or 'italic' in family or 'oblique' in family
    if any(name in family for name in ('sans','arial','helvetica')):
        variant = 'BoldItalic' if bold and italic else 'Bold' if bold else 'Italic' if italic else 'Regular'
        candidate = Path(__file__).resolve().parents[3] / 'frontend/vendor/pdfjs/standard_fonts' / f'LiberationSans-{variant}.ttf'
        if candidate.exists():
            return candidate
    suffix = '-BoldOblique' if bold and italic else '-Bold' if bold else '-Oblique' if italic else ''
    candidate = Path(FONT_PATH).with_name('DejaVuSans'+suffix+'.ttf')
    return candidate if candidate.exists() else Path(FONT_PATH)
