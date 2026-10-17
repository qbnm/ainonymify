#!/usr/bin/env python3
"""Build-time source/notices collection; no user data and no runtime networking."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from importlib.metadata import distributions
from pathlib import Path
from urllib.request import Request, urlopen
import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    with urlopen(Request(url, headers={'User-Agent': 'ainonymify-license-bundle/1'}), timeout=120) as response:
        return response.read()


def download(url, path, expected=None):
    # Retain verified cached downloads across native runs; never trust an
    # unverified cache if an upstream digest is available.
    if path.exists() and expected and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        data = path.read_bytes()
    else:
        data = get(url)
    digest = hashlib.sha256(data).hexdigest()
    if expected and digest != expected:
        raise RuntimeError(f'Source checksum mismatch: {path.name}')
    path.write_bytes(data)
    return {'file': path.name, 'url': url, 'sha256': digest, 'bytes': len(data)}


def collect(destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {'python': [], 'sources': [], 'system': []}
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / name, destination / name)
    for dist in sorted(distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = dist.metadata['Name'], dist.version
        slug = re.sub(r'[^a-z0-9.-]', '-', name.lower())
        folder = destination / 'notices' / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'METADATA.txt').write_text(dist.read_text('METADATA') or json.dumps(dict(dist.metadata)))
        for file in dist.files or []:
            # Include wheel-vendored library licenses as well as dist-info.
            if re.search(r'license|copying|copyright|notice', file.name, re.I):
                path = Path(dist.locate_file(file))
                if path.is_file():
                    safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(file))
                    shutil.copyfile(path, folder / safe_name)
        release = json.loads(get(f'https://pypi.org/pypi/{slug}/{version}/json'))
        source = next((item for item in release['urls'] if item['packagetype'] == 'sdist'), None)
        if source is None:
            raise RuntimeError(f'No upstream source distribution for {name} {version}')
        artifact = download(source['url'], destination / source['filename'], source['digests']['sha256'])
        manifest['sources'].append(artifact)
        manifest['python'].append({'name': name, 'version': version,
            'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
            'source': artifact['file']})
        print(f'Collected {name} {version}', flush=True)
    source_lock = json.loads((ROOT / 'scripts/source-lock.json').read_text())
    version = pymupdf.VersionFitz
    filename = f'mupdf-{version}-source.tar.gz'
    pinned = source_lock[filename]
    artifact = download(pinned['url'], destination / filename, pinned['sha256'])
    # Official full source tarball, not a GitHub snapshot lacking submodules.
    import tarfile
    with tarfile.open(destination / artifact['file']) as archive:
        names = archive.getnames()
        if not any('/thirdparty/' in name for name in names) or not any(name.endswith('/COPYING') for name in names):
            raise RuntimeError('MuPDF full source archive lacks third-party sources or license')
    manifest['sources'].append(artifact)
    pdfjs = json.loads((ROOT / 'composer.json').read_text())['require']['npm-asset/pdfjs-dist']
    filename = f'pdfjs-{pdfjs}-source.tar.gz'
    pinned = source_lock[filename]
    manifest['sources'].append(download(pinned['url'], destination / filename, pinned['sha256']))
    assets = ROOT / 'frontend/vendor/pdfjs'
    for path in assets.rglob('*'):
        if path.is_file() and re.search(r'license|notice|copying', path.name, re.I):
            target = destination / 'notices/pdfjs' / path.relative_to(assets)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    if shutil.which('dpkg-query'):
        data = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'], text=True)
        for line in data.splitlines():
            name, binary, source, version = line.split('\t')
            manifest['system'].append({'name': name, 'version': binary, 'source': source, 'source_version': version})
            copyright = Path('/usr/share/doc') / name.split(':')[0] / 'copyright'
            if copyright.is_file():
                target = destination / 'notices/debian' / (name.replace(':', '_') + '.copyright')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(copyright, target)
        # Referenced common license texts must accompany Debian notices too.
        shutil.copytree('/usr/share/common-licenses', destination / 'notices/debian/common-licenses', dirs_exist_ok=True)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'License/source bundle ready: {destination}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'legal')
    collect(parser.parse_args().output)
