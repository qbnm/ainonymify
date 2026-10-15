"""Exercise a running localhost deployment with synthetic data only."""
import argparse
import httpx
import pymupdf as fitz

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8765')
parser.add_argument('--username', required=True)
parser.add_argument('--password', required=True)
args = parser.parse_args()
if httpx.URL(args.base_url).host not in ('127.0.0.1', 'localhost', '::1'):
    parser.error('This synthetic smoke test only targets localhost')
with fitz.open() as pdf:
    page=pdf.new_page(width=595,height=420)
    page.insert_text((45,65),'IBAN: DE02120300000000202051',fontsize=13)
    page.insert_text((45,100),'Email: synthetic@example.test',fontsize=13)
    raw=pdf.tobytes()
with httpx.Client(base_url=args.base_url,auth=(args.username,args.password),
                  headers={'X-Privacy-Gateway':'1'},timeout=180,trust_env=False) as client:
    def checked(response):
        response.raise_for_status()
        return response
    assert client.get('/',headers={'Authorization':'Basic bad'},auth=None).status_code==401
    checked(client.get('/vendor/pdfjs/pdf.mjs'))
    sid=checked(client.post('/api/sessions')).json()['id']
    try:
        data=checked(client.post(f'/api/sessions/{sid}/documents',files={'files':('synthetic.pdf',raw,'application/pdf')})).json()
        did=data['documents'][0]['id']
        data=checked(client.post(f'/api/sessions/{sid}/analyze')).json()
        assert {e['type'] for e in data['entities']}=={'IBAN','EMAIL'}
        data=checked(client.post(f'/api/sessions/{sid}/generate')).json()
        assert data['status']=='complete',data['warnings']
        download=checked(client.get(f'/api/sessions/{sid}/download'))
        assert download.headers['content-type']=='application/pdf'
        output=download.content
        with fitz.open(stream=output,filetype='pdf') as pdf:
            assert 'DE02120300000000202051' not in pdf[0].get_text()
            assert next(d['replacement'] for d in data['detections'] if d['original_text']=='DE02120300000000202051') in pdf[0].get_text()
        assert client.get(f'/api/sessions/{sid}/download/mapping').status_code==400
        data=checked(client.post(f'/api/sessions/{sid}/generate',json={'include_mapping':True})).json()
        assert data['includes_mapping'] is True
        assert checked(client.get(f'/api/sessions/{sid}/download')).headers['content-type']=='application/pdf'
        reference=checked(client.get(f'/api/sessions/{sid}/download/mapping')).json()
        assert {e['value'] for e in reference['entities']}=={'DE02120300000000202051','synthetic@example.test'}
        print('PASS: authentication, offline assets, upload, OCR, selection-based export, pixel redaction, verification, optional private token reference')
    finally:
        checked(client.delete(f'/api/sessions/{sid}'))
