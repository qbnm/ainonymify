from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from uuid import uuid4
import re
import unicodedata
import hmac
import string

TYPES = ('PERSON', 'COMPANY', 'PROPERTY', 'ADDRESS', 'LOCATION', 'IBAN', 'BIC',
         'EMAIL', 'PHONE', 'TAX_ID', 'VAT_ID', 'ACCOUNT', 'URL', 'OTHER')
TOKEN_RE = re.compile(r'\{\{(' + '|'.join(TYPES) + r')_[0-9]{3,}\}\}')


def uid():
    return uuid4().hex


def normalize(value, kind):
    value = unicodedata.normalize('NFKC', value)
    if kind in ('IBAN', 'BIC', 'VAT_ID', 'TAX_ID', 'PHONE', 'ACCOUNT'):
        return re.sub(r'[^A-Z0-9+]', '', value.upper())
    # No fuzzy transliteration; Müller and Mueller stay separate.
    return ' '.join(value.split()).casefold()


def character_pool(char):
    """Replace letters/numbers; keep whitespace, punctuation and symbols verbatim."""
    category = unicodedata.category(char)
    if category == 'Nd':
        zero = ord(char) - unicodedata.decimal(char)
        return ''.join(chr(zero + i) for i in range(10))
    if category not in ('Lu', 'Ll', 'Lt', 'Lo', 'Lm', 'Nl', 'No'):
        return ''
    name = unicodedata.name(char, '')
    for script, upper, lower in (
        ('LATIN', string.ascii_uppercase, string.ascii_lowercase),
        ('CYRILLIC', 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ', 'абвгдежзийклмнопрстуфхцчшщэюя'),
        ('GREEK', 'ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ', 'αβγδεζηθικλμνξοπρστυφχψω')):
        if script in name and category in ('Lu', 'Ll'):
            return upper if category == 'Lu' else lower
    # Preserve the Unicode category for other scripts and nondecimal numbers.
    start = ord(char) // 256 * 256
    return ''.join(chr(i) for i in range(start, min(start + 256, 0x110000))
                   if unicodedata.category(chr(i)) == category)


@dataclass
class Entity:
    id: str
    type: str
    value: str
    normalized_value: str
    token: str
    replacement_key: str = field(default_factory=uid, repr=False)

    def replacement(self, value=None):
        value = self.value if value is None else value
        output, position = [], 0
        for char in value:
            pool = character_pool(char)
            if not pool:
                output.append(char)
                continue
            choices = [c for c in pool if c.casefold() != char.casefold()]
            if not choices:
                raise ValueError('This character has no replacement in its character group')
            # Ignore punctuation when indexing: formatting/case variants of an
            # entity share the same surrogate letters and digits.
            message = f'{position}:{char.casefold()}'.encode()
            number = int.from_bytes(hmac.digest(self.replacement_key.encode(), message, 'sha256'), 'big')
            output.append(choices[number % len(choices)])
            position += 1
        return ''.join(output)


@dataclass
class Detection:
    id: str
    document_id: str
    page: int
    bbox: list[float]
    source: str
    confidence: float
    entity_id: str
    original_text: str
    normalized_value: str
    status: str = 'confirmed'
    reason: str = ''
    secondary_ocr: str = ''
    rects: list[list[float]] = field(default_factory=list)


@dataclass
class Document:
    id: str
    name: str
    pages: list[dict]


@dataclass
class Session:
    id: str = field(default_factory=uid)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    documents: list[Document] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    status: str = 'upload'
    includes_mapping: bool = False

    def entity(self, kind, value, separate=False):
        norm = normalize(value, kind)
        if not separate and norm:
            for item in self.entities:
                if item.type == kind and item.normalized_value == norm:
                    return item
        numbers = [int(e.token.rsplit('_', 1)[1][:-2]) for e in self.entities if e.type == kind]
        token = '{{' + kind + '_' + f'{max(numbers, default=0) + 1:03d}' + '}}'
        item = Entity(uid(), kind, value, norm, token)
        self.entities.append(item)
        return item

    def public(self):
        data = asdict(self)
        for document in data['documents']:
            for page in document['pages']:
                page.pop('ocr_words', None)
        for entity, item in zip(self.entities, data['entities']):
            item.pop('replacement_key')
            item['replacement'] = entity.replacement()
        entities = {e.id: e for e in self.entities}
        for item in data['detections']:
            item['replacement'] = entities[item['entity_id']].replacement(item['original_text']) if entities[item['entity_id']].value else ''
        return data
