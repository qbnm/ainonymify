import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
from pathlib import Path
import os
import shutil
import tempfile
import secrets
from http.cookies import SimpleCookie
import pymupdf as fitz
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .models import Document, Detection, TYPES, uid
from .services.sessions import Store, invalidate
from .services import pdf, pipeline, repeats, personal_rules
from .services.accounts import Accounts, owner, text_key

FRONTEND = Path(__file__).resolve().parents[2] / 'frontend'
if not FRONTEND.exists():
    FRONTEND = Path(__file__).resolve().parents[3] / 'frontend'
MAX_FILE = int(os.getenv('MAX_FILE_MB', '20')) * 1024 * 1024
MAX_BATCH = int(os.getenv('MAX_BATCH_MB', '80')) * 1024 * 1024
MAX_DOCUMENTS = int(os.getenv('MAX_DOCUMENTS', '10'))
store = None
accounts = None


@asynccontextmanager
async def lifespan(app):
    global store, accounts
    if not os.getenv('APP_USERNAME') or not os.getenv('APP_PASSWORD') or os.environ['APP_PASSWORD'] == 'change-me':
        raise RuntimeError('Set APP_USERNAME and a non-default APP_PASSWORD before starting')
    if not (FRONTEND / 'vendor/pdfjs/pdf.mjs').exists():
        raise RuntimeError('Local PDF.js assets missing; run composer install or rebuild the Docker image')
    languages = set(pdf.pytesseract.get_languages())
    if not set(pdf.OCR_LANG.split('+')) <= languages:
        raise RuntimeError('Configured local OCR language data is missing')
    if not Path(pdf.FONT_PATH).exists():
        raise RuntimeError('Local Unicode font is missing')
    parent = Path(os.getenv('APP_TEMP_DIR', str(Path(tempfile.gettempdir()) / 'pdf-privacy-gateway')))
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    # Dedicated single-process directory. Remove data left by a previous crash.
    for path in parent.glob('privacy-*'):
        if path.is_dir():
            shutil.rmtree(path)
    store = Store(ttl=int(os.getenv('SESSION_TTL_SECONDS', '1800')), root=parent)
    accounts = Accounts(os.getenv('APP_DATA_DIR', str(Path.cwd() / '.local/account-data')))
    async def sweeper():
        while True:
            await asyncio.sleep(15)
            await asyncio.to_thread(store.cleanup)
    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        store.close()
        accounts.close()


app = FastAPI(title='ainonymify', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class Guard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        async def reject(code, message, extra=None):
            response = JSONResponse({'detail': message}, status_code=code, headers=extra or {})
            await response(scope, receive, safe_send)
        async def safe_send(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', []).extend([
                    (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
            await send(message)
        try:
            scheme, token = headers.get(b'authorization', b'').decode('ascii').split(' ', 1)
            user, password = base64.b64decode(token, validate=True).decode().split(':', 1)
            expected_user = hashlib.sha256(os.getenv('APP_USERNAME', '').encode()).digest()
            expected_pass = hashlib.sha256(os.getenv('APP_PASSWORD', '').encode()).digest()
            allowed = scheme.lower() == 'basic' and hmac.compare_digest(hashlib.sha256(user.encode()).digest(), expected_user) & hmac.compare_digest(hashlib.sha256(password.encode()).digest(), expected_pass)
        except (ValueError, UnicodeError):
            allowed = False
        if not allowed:
            return await reject(401, 'Authentication required', {'WWW-Authenticate': 'Basic realm="ainonymify", charset="UTF-8"'})
        if scope['method'] in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if headers.get(b'x-privacy-gateway') != b'1' or headers.get(b'sec-fetch-site') == b'cross-site':
                return await reject(403, 'Same-origin application request required')
            try:
                size = int(headers.get(b'content-length', b'0')) if scope['method'] == 'DELETE' else int(headers[b'content-length'])
            except (KeyError, ValueError):
                return await reject(411, 'Content-Length is required')
            if size < 0 or size > MAX_BATCH + 1024 * 1024:
                return await reject(413, 'Request exceeds the batch upload limit')
        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > MAX_BATCH + 1024 * 1024:
                raise ValueError('Request exceeds the upload limit')
            return message
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get(b'cookie', b'').decode('latin1'))
        except Exception:
            cookies = SimpleCookie()
        login = cookies.get('ainonymify_login')
        token = login.value if login else ''
        identity = accounts.identity(token) if token else None
        guest = cookies.get('ainonymify_guest')
        valid_guest = guest and len(guest.value) == 64 and all(c in '0123456789abcdef' for c in guest.value)
        guest_token = guest.value if valid_guest else secrets.token_hex(32)
        scope['state'] = {'account': identity, 'login_token': token}
        context = owner.set('u:' + identity['id'] if identity else 'g:' + hashlib.sha256(guest_token.encode()).hexdigest())
        async def identity_send(message):
            if message['type'] == 'http.response.start' and not valid_guest and scope['path'].startswith('/api/'):
                secure = '; Secure' if scope['scheme'] == 'https' else ''
                message.setdefault('headers', []).append((b'set-cookie',
                    f'ainonymify_guest={guest_token}; Path=/; HttpOnly; SameSite=Strict{secure}'.encode()))
            await safe_send(message)
        try:
            await self.app(scope, limited_receive, identity_send)
        finally:
            owner.reset(context)


app.add_middleware(Guard)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    # Only our own validation messages; PDF/OCR library failures are caught below.
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.exception_handler(KeyError)
async def absent(request, exc):
    return JSONResponse({'detail': 'Session or item not found, or session expired'}, status_code=404)


def run_job(function, *args):
    try:
        return function(*args)
    except ValueError:
        raise
    except Exception:
        # Library errors can include document text or filenames. Never return/log them.
        raise HTTPException(422, 'Local PDF/OCR processing failed. No output released. Check the PDF and local OCR installation.') from None


def read_pdf(upload):
    data = upload.file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, 'A PDF exceeds the per-file limit')
    if not data.startswith(b'%PDF-'):
        raise ValueError('Only PDF files are accepted')
    return data


def document(session, did):
    return next((d for d in session.documents if d.id == did), None) or missing()


def missing():
    raise KeyError()


def account_required(request):
    account = request.state.account
    if not account:
        raise HTTPException(401, 'Sign in to use saved personal rules')
    return account['id']


def login_cookie(response, request, token):
    response.set_cookie('ainonymify_login', token, max_age=30*86400,
                        httponly=True, samesite='strict', secure=request.url.scheme == 'https')


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)


@app.get('/api/account')
def account_status(request: Request):
    return {'account': request.state.account}


@app.post('/api/account/register')
@app.post('/api/account/login')
def account_login(data: Credentials, request: Request, response: Response):
    action = request.url.path.rsplit('/', 1)[-1]
    accounts.throttle(request.client.host if request.client else 'local')
    identity, token = accounts.authenticate(data.username, data.password, register=action == 'register')
    accounts.logout(request.state.login_token)
    login_cookie(response, request, token)
    return {'account': identity}


@app.delete('/api/account/login')
def account_logout(request: Request, response: Response):
    accounts.logout(request.state.login_token)
    response.delete_cookie('ainonymify_login', httponly=True, samesite='strict')
    return {'account': None}


@app.delete('/api/account')
def account_delete(request: Request, response: Response):
    user_id = account_required(request)
    accounts.delete_account(user_id)
    store.delete_owned('u:' + user_id)
    response.delete_cookie('ainonymify_login', httponly=True, samesite='strict')
    return {'account': None}


class PersonalRule(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    type: str
    label: str = Field(default='', max_length=80)
    action: str


@app.get('/api/account/rules')
def account_rules(request: Request):
    return {'rules': accounts.list_rules(account_required(request))}


@app.post('/api/account/rules')
def save_rule(data: PersonalRule, request: Request):
    user_id = account_required(request)
    if data.type not in TYPES or data.action not in ('redact', 'ignore'):
        raise ValueError('Choose a valid type and action')
    accounts.save_rule(user_id, data.text, data.type, data.label or data.type, data.action)
    return {'rules': accounts.list_rules(user_id)}


@app.delete('/api/account/rules/{rule_id}')
def forget_rule(rule_id: str, request: Request):
    user_id = account_required(request)
    accounts.delete_rule(user_id, rule_id)
    return {'rules': accounts.list_rules(user_id)}


def remember(session, detections, label=''):
    identity = owner.get()
    if not identity or not identity.startswith('u:'):
        return
    entities = {e.id: e for e in session.entities}
    values = []
    for detection in detections:
        entity = entities[detection.entity_id]
        text = detection.original_text or entity.value
        if detection.status not in ('confirmed', 'ignored') or not entity.value or not any(c.isalnum() for c in text):
            continue
        action = 'ignore' if detection.status == 'ignored' else 'redact'
        values.append((text, entity.type, label, action))
        if text_key(entity.value) != text_key(text):
            values.append((entity.value, entity.type, label, action))
    accounts.save_rules(identity[2:], values)


@app.post('/api/sessions')
def create():
    return store.create().public()


@app.get('/api/sessions/{sid}')
def get_session(sid: str):
    with store.access(sid) as (session, root):
        return session.public()


@app.delete('/api/sessions/{sid}')
def delete(sid: str):
    store.delete(sid)
    return {'deleted': True}


@app.post('/api/sessions/{sid}/documents')
def upload(sid: str, files: list[UploadFile] = File(...)):
    with store.access(sid) as (session, root):
        if session.status != 'upload':
            raise ValueError('Create a new session to upload another batch')
        if len(files) + len(session.documents) > MAX_DOCUMENTS:
            raise ValueError('Too many documents in this batch')
        staged, total, page_total = [], 0, sum(len(d.pages) for d in session.documents)
        try:
            for file in files:
                raw = read_pdf(file)
                total += len(raw)
                if total > MAX_BATCH:
                    raise ValueError('Batch exceeds upload limit')
                did = uid()
                destination = root / f'{did}.pdf'
                pages = run_job(pdf.canonicalize, raw, destination)
                page_total += len(pages)
                if page_total > pdf.MAX_PAGES:
                    destination.unlink(missing_ok=True)
                    raise ValueError('Batch exceeds the total page limit')
                name = (file.filename or 'document.pdf').replace('\\', '/').split('/')[-1][:160]
                staged.append(Document(did, name, pages))
            session.documents.extend(staged)
        except Exception:
            for item in staged:
                (root / f'{item.id}.pdf').unlink(missing_ok=True)
            raise
        return session.public()


@app.post('/api/sessions/{sid}/analyze')
def analyze(sid: str):
    with store.access(sid) as (session, root):
        if not session.documents:
            raise ValueError('Upload at least one PDF')
        invalidate(session, root)
        session.status = 'analyzing'
        try:
            run_job(pipeline.analyze, session, root)
            identity = owner.get()
            if identity and identity.startswith('u:'):
                personal_rules.apply(session, accounts.list_rules(identity[2:]))
        except Exception:
            session.status = 'analysis_failed'
            raise
        return session.public()


@app.get('/api/sessions/{sid}/documents/{did}/pdf')
def preview(sid: str, did: str):
    with store.access(sid) as (session, root):
        document(session, did)
        return Response((root / f'{did}.pdf').read_bytes(), media_type='application/pdf')


class Edit(BaseModel):
    status: str = 'confirmed'
    type: str
    value: str = Field(max_length=4096)
    separate: bool = False
    personal_label: str = Field(default='', max_length=80)
    remember: bool = True


class BulkEdit(BaseModel):
    detection_ids: list[str] = Field(min_length=1, max_length=10000)
    status: str
    remember: bool = True


@app.post('/api/sessions/{sid}/detections/bulk')
def bulk_edit(sid: str, data: BulkEdit):
    with store.access(sid) as (session, root):
        if session.status not in ('review', 'verification_warning', 'complete'):
            raise ValueError('Complete analysis before editing detections')
        if data.status not in ('confirmed', 'ignored'):
            raise ValueError('Bulk action must be confirmed or ignored')
        by_id = {d.id: d for d in session.detections}
        ids = set(data.detection_ids)
        # Validate the entire selection before changing anything.
        if not ids <= by_id.keys():
            raise KeyError('Detection not found in this session')
        for detection_id in ids:
            by_id[detection_id].status = data.status
        if data.status == 'confirmed':
            expanded = set()
            for detection_id in sorted(ids):
                seed = by_id[detection_id]
                key = (seed.document_id, repeats.text_key(seed.original_text), seed.entity_id)
                if key in expanded:
                    continue
                expanded.add(key)
                repeats.mark_repeats(session, document(session, seed.document_id), seed)
        invalidate(session, root)
        if data.remember:
            remember(session, [by_id[i] for i in sorted(ids)])
        return session.public()


class TextRegion(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    character_ids: list[int] = Field(min_length=1, max_length=20000)
    type: str
    value: str = Field(default='', max_length=4096)
    personal_label: str = Field(default='', max_length=80)
    remember: bool = True


@app.post('/api/sessions/{sid}/detections/from-text')
def native_text_region(sid: str, data: TextRegion):
    with store.access(sid) as (session, root):
        doc = document(session, data.document_id)
        if data.type not in TYPES or data.page > len(doc.pages) or session.status not in ('review', 'verification_warning', 'complete'):
            raise ValueError('Complete analysis and select text on a valid page')
        bbox, rects, original = pdf.select_native_characters(doc.pages[data.page-1], data.character_ids)
        value = data.value.strip() or original
        if len(value) > 4096:
            raise ValueError('Select a shorter text passage')
        entity = session.entity(data.type, value)
        seed = repeats.add_selection(session, Detection(uid(), doc.id, data.page, bbox, 'native_text', 1,
            entity.id, original, entity.normalized_value, 'confirmed', 'Selected from original visible PDF text', rects=rects))
        repeats.mark_repeats(session, doc, seed)
        invalidate(session, root)
        if data.remember:
            remember(session, [seed], data.personal_label)
        return session.public()


@app.post('/api/sessions/{sid}/detections/{detection_id}')
def edit(sid: str, detection_id: str, data: Edit):
    with store.access(sid) as (session, root):
        if data.status not in ('confirmed', 'ignored', 'pending') or data.type not in TYPES:
            raise ValueError('Invalid review state or entity type')
        if not data.value.strip() and data.status != 'ignored':
            raise ValueError('Enter a value for the private token reference, or use the visual-only option')
        detection = next((d for d in session.detections if d.id == detection_id), None) or missing()
        entity = session.entity(data.type, data.value.strip(), separate=data.separate)
        detection.entity_id, detection.status = entity.id, data.status
        detection.normalized_value = entity.normalized_value
        if data.status == 'confirmed' and not data.separate:
            repeats.mark_repeats(session, document(session, detection.document_id), detection)
        invalidate(session, root)
        if not data.separate and data.remember:
            remember(session, [detection], data.personal_label)
        return session.public()


class Region(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    type: str
    value: str = Field(default='', max_length=4096)
    personal_label: str = Field(default='', max_length=80)
    remember: bool = True
    visual_only: bool = False


@app.post('/api/sessions/{sid}/detections')
def manual(sid: str, data: Region):
    with store.access(sid) as (session, root):
        doc = document(session, data.document_id)
        if data.type not in TYPES or data.page > len(doc.pages) or session.status not in ('review', 'verification_warning', 'complete'):
            raise ValueError('Invalid region')
        dimensions = doc.pages[data.page-1]
        import math
        if not all(math.isfinite(v) for v in data.bbox):
            raise ValueError('Invalid coordinates')
        x0, y0, x1, y1 = data.bbox
        if not (0 <= x0 < x1 <= dimensions['width'] and 0 <= y0 < y1 <= dimensions['height']) or min(x1-x0, y1-y0) < 3:
            raise ValueError('Draw a region within the page, at least 3 PDF points wide and high')
        with fitz.open(root / f'{doc.id}.pdf') as source:
            page = source[data.page-1]
            extracted = page.get_textbox(fitz.Rect(data.bbox)).strip()
            evidence = run_job(pdf.secondary, page, data.bbox)
        value = '' if data.visual_only else (data.value.strip() or extracted or evidence.split('\n')[0].strip())
        if not value and not data.visual_only:
            raise ValueError('No text could be read. Enter a value or select visual-only redaction')
        entity = session.entity(data.type, value, separate=data.visual_only)
        seed = repeats.add_selection(session, Detection(uid(), doc.id, data.page, data.bbox, 'manual', 1,
            entity.id, extracted or value, entity.normalized_value, 'confirmed',
            'Visual-only region has no text value' if data.visual_only else 'Manually added', evidence))
        if not data.visual_only:
            repeats.mark_repeats(session, doc, seed)
        invalidate(session, root)
        if not data.visual_only and data.remember:
            remember(session, [seed], data.personal_label)
        return session.public()


class Merge(BaseModel):
    source: str
    target: str


@app.post('/api/sessions/{sid}/entities/merge')
def merge(sid: str, data: Merge):
    with store.access(sid) as (session, root):
        entities = {e.id: e for e in session.entities}
        if data.source == data.target or data.source not in entities or data.target not in entities:
            raise ValueError('Choose two different entities')
        if entities[data.source].type != entities[data.target].type:
            raise ValueError('Only entities of the same type may be merged')
        for detection in session.detections:
            if detection.entity_id == data.source:
                detection.entity_id = data.target
        invalidate(session, root)
        return session.public()


class GenerateOptions(BaseModel):
    include_mapping: bool = False


@app.post('/api/sessions/{sid}/generate')
def generate(sid: str, data: GenerateOptions | None = None):
    with store.access(sid) as (session, root):
        if session.status not in ('review', 'verification_warning', 'complete'):
            raise ValueError('Complete analysis before generating')
        run_job(pipeline.generate, session, root, data.include_mapping if data else False)
        return session.public()


@app.get('/api/sessions/{sid}/download')
def download(sid: str):
    with store.access(sid) as (session, root):
        single = len(session.documents) == 1
        path = root / (f'output-{session.documents[0].id}.pdf' if single else 'result.zip')
        if session.status != 'complete' or not path.exists():
            raise ValueError('Generate and selected-region checks must complete before download')
        name = 'document-01.anonymized.pdf' if single else 'privacy-session.zip'
        return Response(path.read_bytes(), media_type='application/pdf' if single else 'application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})


@app.get('/api/sessions/{sid}/download/mapping')
def download_mapping(sid: str):
    with store.access(sid) as (session, root):
        path = root / 'mapping.json'
        if session.status != 'complete' or not session.includes_mapping or not path.exists():
            raise ValueError('Generate with the private reference option before downloading it')
        return Response(path.read_bytes(), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="mapping.json"'})


app.mount('/', StaticFiles(directory=FRONTEND, html=True), name='frontend')
