"""Local source disclosure. Never archive runtime directories or user data."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / 'legal'
ROOT_FILES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'SECURITY.md',
              'Dockerfile', 'compose.yml', 'composer.json', 'composer.lock',
              'requirements.txt', 'requirements-dev.txt', '.env.example',
              '.dockerignore', '.gitignore')
SOURCE_DIRS = {'backend': {'.py'}, 'scripts': {'.py', '.php', '.mjs', '.json'},
               'frontend/js': {'.js', '.mjs'}, 'frontend/css': {'.css'},
               'docs': {'.md', '.svg'}}


def safe_source(path):
    return path.is_file() and not any(
        parent.is_symlink() for parent in (path, *path.parents) if parent != ROOT)


def source_files():
    for name in (*ROOT_FILES, 'frontend/index.html', 'frontend/legal.html'):
        path = ROOT / name
        if safe_source(path):
            yield path
    for folder, suffixes in SOURCE_DIRS.items():
        base = ROOT / folder
        for path in sorted(base.rglob('*')):
            relative = path.relative_to(ROOT)
            if (safe_source(path) and path.suffix in suffixes
                    and not any(part.startswith('.') or part == '__pycache__' for part in relative.parts)):
                yield path


def source_archive():
    data = BytesIO()
    with ZipFile(data, 'w', ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, 'ainonymify/' + path.relative_to(ROOT).as_posix())
    return data.getvalue()


def pdf_metadata():
    # Only software attribution, never any input-document metadata or identity.
    # Artifex requests preservation of producer/copyright notices in AGPL output.
    return {'creator': 'ainonymify',
            'producer': f'PyMuPDF {fitz.VersionBind} / MuPDF {fitz.VersionFitz} (Copyright Artifex Software, Inc.; AGPLv3)'}
