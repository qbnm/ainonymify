"""Local, conservative rules. Invalid identifier-like strings stay reviewable."""
from dataclasses import dataclass
import re
from ..models import normalize

# Lengths from the ISO 13616 IBAN registry. Unknown countries are never confirmed.
IBAN_LENGTHS = dict(AD=24, AE=23, AL=28, AT=20, AZ=28, BA=20, BE=16, BG=22,
    BH=22, BI=27, BR=29, BY=28, CH=21, CR=22, CY=28, CZ=24, DE=22, DJ=27,
    DK=18, DO=28, EE=20, EG=29, ES=24, FI=18, FK=18, FO=18, FR=27, GB=22,
    GE=22, GI=23, GL=18, GR=27, GT=28, HR=21, HU=28, IE=22, IL=23, IQ=23,
    IS=26, IT=27, JO=30, KZ=20, KW=30, LB=28, LC=32, LI=21, LT=20, LU=20,
    LV=21, LY=25, MC=27, MD=24, ME=22, MK=19, MN=20, MR=27, MT=31, MU=30,
    NI=28, NL=18, NO=15, OM=23, PK=24, PL=28, PS=29, PT=25, QA=29, RO=24,
    RS=22, RU=33, SA=24, SC=31, SD=18, SE=24, SI=19, SK=24, SM=27, SO=23,
    ST=25, SV=28, TL=23, TN=24, TR=26, UA=29, VA=22, VG=24, XK=20)


def valid_iban(value):
    value = normalize(value, 'IBAN')
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]+', value):
        return False
    if IBAN_LENGTHS.get(value[:2]) != len(value):
        return False
    number = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in value[4:] + value[:4])
    return int(number) % 97 == 1


def ocr_candidates(value):
    """Bounded deterministic repair for German numeric BBANs; never auto-accept."""
    value = normalize(value, 'IBAN')
    if value.startswith('DE'):
        repaired = 'DE' + value[2:].translate(str.maketrans({'O':'0', 'I':'1', 'L':'1', 'S':'5', 'B':'8'}))
        return [repaired] if repaired != value and valid_iban(repaired) else []
    return []


def valid_german_tax_id(value):
    digits = re.sub(r'\D', '', value)
    if len(digits) != 11 or digits[0] == '0':
        return False
    product = 10
    for digit in digits[:10]:
        total = (int(digit) + product) % 10 or 10
        product = (2 * total) % 11
    return (11 - product) % 10 == int(digits[-1])


def valid_vat(value):
    digits = re.sub(r'\D', '', value)
    if len(digits) != 9:
        return False
    product = 10
    for digit in digits[:8]:
        product = 2 * ((int(digit) + product) % 10 or 10) % 11
    return (11 - product) % 10 == int(digits[-1])


@dataclass
class Match:
    start: int
    end: int
    type: str
    value: str
    suspicious: bool = False
    reason: str = ''


class RuleDetector:
    def detect(self, text):
        result = []
        def add(match, kind, group=0, suspicious=False, reason=''):
            result.append(Match(match.start(group), match.end(group), kind,
                                match.group(group), suspicious, reason))
        # Country-specific length prevents consuming the next word after an IBAN.
        for m in re.finditer(r'(?<!\w)([A-Z]{2})[ \t]?[0-9OIlSB]{2}', text, re.I):
            country = m.group(1).upper()
            length = IBAN_LENGTHS.get(country)
            if not length:
                continue
            tail = re.match(r'(?:[ \t]?[A-Z0-9]){' + str(length - 4) + r'}', text[m.end():], re.I)
            if not tail:
                continue
            end = m.end() + tail.end()
            if end < len(text) and text[end].isalnum():
                continue
            value = text[m.start():end]
            valid = valid_iban(value)
            candidates = ocr_candidates(value)
            result.append(Match(m.start(), end, 'IBAN', value, not valid,
                '' if valid else 'IBAN-like value failed validation; OCR repair candidate available' if candidates
                else 'IBAN-like value failed validation'))
        patterns = [
            ('EMAIL', r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', 0),
            ('URL', r'https?://[^\s<>]+', 0),
            ('PHONE', r'(?<!\w)(?:\+\d{1,3}|00\d{1,3}|0\d{2,5})[ /().-]*(?:\d[ /().-]*){6,13}(?!\w)', 0),
            ('BIC', r'\b(?:BIC|SWIFT)(?:[ /-]Code)?\s*[:=]?\s*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', 1),
            ('VAT_ID', r'\bDE\s?\d{9}\b', 0),
            ('TAX_ID', r'(?:Steuer-ID|Identifikationsnummer|Steueridentifikationsnummer|Tax ID)\s*[:=]?\s*(\d[\d ]{9,16}\d)', 1),
            ('TAX_ID', r'(?:Steuernummer|St[.-]?Nr\.?)\s*[:=]?\s*(\d[\d /-]{6,16}\d)', 1),
            ('ACCOUNT', r'(?:Kontonummer|Account number)\s*[:=]?\s*([\d -]{5,24})', 1),
            ('COMPANY', r'\b[\wÄÖÜäöüß][\wÄÖÜäöüß &.,-]{1,75}\s(?:GmbH(?:\s*&\s*Co\.\s*KG)?|AG|UG|e\.K\.|OHG|GbR)\b', 0),
            ('PERSON', r'(?:Name|Kunde|Customer|Herr|Frau|Ansprechpartner)\s*:?\s+([A-ZÄÖÜ][\wäöüß-]+(?:\s+[A-ZÄÖÜ][\wäöüß-]+){1,3})', 1),
            ('ADDRESS', r'\b[A-ZÄÖÜ][\wäöüß-]*(?:straße|strasse|str\.|weg|platz|allee)\s+\d+[a-z]?(?:,?\s+\d{5}\s+[A-ZÄÖÜ][\wäöüß-]+)?', 0),
            ('PROPERTY', r'(?:Objekt|Liegenschaft|Flurstück|Property)\s*(?:Nr\.?)?\s*[:=]\s*([^\n;]{2,60})', 1),
        ]
        for kind, pattern, group in patterns:
            for m in re.finditer(pattern, text):
                value = m.group(group)
                suspicious = kind in ('PERSON', 'COMPANY', 'ADDRESS', 'PROPERTY', 'ACCOUNT')
                reason = 'Rule-based entity; confirm value and boundaries' if suspicious else ''
                if kind == 'VAT_ID' and not valid_vat(value):
                    suspicious, reason = True, 'German VAT checksum failed'
                if kind == 'TAX_ID':
                    suspicious = not valid_german_tax_id(value)
                    reason = 'Tax-related identifier requires review' if suspicious else ''
                add(m, kind, group, suspicious, reason)
        # Prefer identifiers over phone patterns contained within them.
        result = [m for m in result if m.type != 'PHONE' or not any(
            n.type in ('IBAN', 'VAT_ID', 'TAX_ID', 'ACCOUNT') and n.start <= m.start and n.end >= m.end
            for n in result)]
        return sorted(result, key=lambda m: (m.start, -(m.end - m.start)))
