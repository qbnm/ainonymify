import hashlib
import hmac
import json
from ..models import TOKEN_RE, TYPES


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def serialize(session):
    used = {d.entity_id for d in session.detections if d.status == 'confirmed'}
    data = dict(format='pdf-privacy-gateway', version=1, session_id=session.id,
                created_at=session.created_at, entities=[dict(token=e.token, type=e.type.lower(), value=e.value, replacement=e.replacement(),
                    occurrences=[dict(original=d.original_text if e.value else '', replacement=e.replacement(d.original_text) if e.value else '')
                                 for d in session.detections if d.entity_id == e.id and d.status == 'confirmed'])
                for e in session.entities if e.id in used])
    data['sha256'] = digest(data)
    return json.dumps(data, ensure_ascii=False, indent=2).encode()


def load(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('format') != 'pdf-privacy-gateway' or type(data.get('version')) is not int or data['version'] != 1:
            raise ValueError('Unsupported mapping format or version')
        checksum = data.pop('sha256', None)
        if not isinstance(checksum, str) or not hmac.compare_digest(checksum, digest(data)):
            raise ValueError('Mapping integrity check failed')
        entries = data['entities']
        if not isinstance(entries, list) or len(entries) > 10000:
            raise ValueError('Invalid mapping entities')
        mapping = {}
        for item in entries:
            token, value, kind = item['token'], item['value'], item['type']
            if not isinstance(token, str) or not TOKEN_RE.fullmatch(token) or kind.upper() not in TYPES or not token.startswith('{{' + kind.upper() + '_'):
                raise ValueError('Malformed mapping token')
            if token in mapping:
                raise ValueError('Duplicate token definition')
            if not isinstance(value, str) or len(value) > 4096 or TOKEN_RE.search(value):
                raise ValueError('Invalid mapping value')
            mapping[token] = value
        return mapping
    except (TypeError, KeyError, AttributeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Malformed mapping file') from exc
