#!/usr/bin/env python3
"""Retain exact Debian source packages when publishing complete container images.

Run inside a disposable container built from the distribution-sources target.
Source indexes must match the binary versions; unavailable source is an error.
"""
import hashlib
import json
import subprocess
from pathlib import Path

root = Path('/app/legal')
manifest = json.loads((root / 'manifest.json').read_text())
output = root / 'debian-source'; output.mkdir(exist_ok=True)
packages = sorted({(p['source'], p['source_version']) for p in manifest['system']})
for package, version in packages:
    subprocess.run(['apt-get', 'source', '--download-only', '--only-source', f'{package}={version}'],
                   cwd=output, check=True)
manifest['debian_sources'] = []
for path in sorted(output.iterdir()):
    if path.is_file():
        manifest['debian_sources'].append({'file': 'debian-source/' + path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size})
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
