from io import BytesIO
from zipfile import ZipFile
from backend.app.services import legal


def test_legal_disclosure_and_source_exclude_private_files(client, tmp_path, monkeypatch):
    html = client.get('/').text
    assert 'Source &amp; licenses' in html
    assert 'Source / AGPL' in html.split('</header>')[0]
    assert 'WITHOUT ANY WARRANTY' in client.get('/legal/').text
    assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in client.get('/legal/license').text
    assert 'Artifex' in client.get('/legal/notices').text
    # Plant credentials/documents in plausible locations; none may escape.
    root = tmp_path / 'checkout'
    (root / 'backend/app').mkdir(parents=True)
    (root / 'backend/app/service.py').write_text('changed = True')
    (root / 'backend/app/secret.pdf').write_text('PRIVATE PDF')
    (root / '.env').write_text('PASSWORD=SECRET')
    (root / 'backend/app/.hidden.py').write_text('SECRET')
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'index.html').write_text('PRIVATE')
    (root / 'frontend').symlink_to(outside, target_is_directory=True)
    (root / 'backend/app/link.py').symlink_to(root / '.env')
    (root / 'backend/app/linked').symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(legal, 'ROOT', root)
    response = client.get('/legal/source')
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ['ainonymify/backend/app/service.py']
        assert archive.read(archive.namelist()[0]) == b'changed = True'
    assert client.get('/legal/source', headers={'Authorization': 'Basic bad'}).status_code == 401


def test_dependency_download_cannot_escape_bundle(client, tmp_path, monkeypatch):
    bundle = tmp_path / 'legal'; bundle.mkdir()
    (bundle / 'manifest.json').write_text('{"test":true}')
    secret = tmp_path / 'secret.txt'; secret.write_text('PRIVATE')
    monkeypatch.setattr(legal, 'BUNDLE', bundle)
    assert 'manifest.json' in client.get('/legal/files/').text
    assert client.get('/legal/files/manifest.json').json() == {'test': True}
    assert client.get('/legal/files/%2e%2e/secret.txt').status_code == 404
    assert client.get('/legal/files/.env').status_code == 404
    (bundle / 'escape').symlink_to(secret)
    assert client.get('/legal/files/escape').status_code == 404
