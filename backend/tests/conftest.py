from pathlib import Path
import pytest
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from backend.app.services import pdf

IBAN = 'DE02120300000000202051'
EMAIL = 'alex@example.test'


def native_pdf(lines=None, rotation=0):
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=420)
        for index, text in enumerate(lines or [f'IBAN: {IBAN}', f'Email: {EMAIL}']):
            page.insert_text((45, 65+index*35), text, fontsize=13)
        page.set_rotation(rotation)
        return doc.tobytes()


def scanned_pdf(hybrid=False, broken=False):
    image = Image.new('RGB', (1653, 1167), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(pdf.FONT_PATH, 38)
    draw.text((125, 145), f'IBAN: {IBAN}', font=font, fill='black')
    draw.text((125, 245), f'Email: {EMAIL}', font=font, fill='black')
    buf = BytesIO(); image.save(buf, format='PNG')
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=420)
        page.insert_image(page.rect, stream=buf.getvalue())
        if hybrid:
            value = 'DEO2I2O3OOOOOOOO2O2O5I' if broken else IBAN
            page.insert_text((45, 65), f'IBAN: {value}', fontsize=13, render_mode=3)
            page.insert_text((45, 101), f'Email: {EMAIL}', fontsize=13, render_mode=3)
        return doc.tobytes()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_USERNAME', 'tester')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('APP_TEMP_DIR', str(tmp_path / 'runtime'))
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with TestClient(app, headers={'X-Privacy-Gateway':'1', 'Authorization':'Basic dGVzdGVyOnRlc3QtcGFzc3dvcmQtMTIz'}) as client:
        yield client
