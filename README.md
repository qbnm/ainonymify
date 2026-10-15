# PDF Privacy Gateway

A self-hosted, local PDF anonymization service for a Synology NAS or a Docker host. Upload PDFs, choose sensitive regions, and download anonymized copies for external AI analysis. Your original PDFs remain unchanged. Optionally export a private token reference to interpret names and identifiers in analysis results. PDF restoration and automatic processing of AI results are outside the MVP scope.

No document content is sent to external services. The application has no AI integration, telemetry, CDN dependencies, or runtime downloads. **Keep `mapping.json` private; never upload it to an external AI.**

## Start with Docker

```sh
cp .env.example .env
# Edit .env: set APP_USERNAME and a strong APP_PASSWORD.
docker compose up -d --build
```

Open http://127.0.0.1:8765 and sign in using those credentials. The app refuses to start with missing credentials or the example password `change-me`. Build and dependency installation require Internet access; processing does not. PDF.js and its resources are already bundled in `frontend/vendor/pdfjs`.

The default Compose configuration uses a non-root user, a read-only filesystem, a temporary RAM-backed `/tmp`, dropped capabilities, and a loopback-only published application port. It binds only `127.0.0.1:8765`. No document volume is mounted. The default container limits are 3 GiB RAM, two CPUs, and 1.5 GiB temporary storage; large scans may need more. Processing is intentionally serialized in one application process.

### Synology DSM / Container Manager

1. Copy the repository to a NAS folder, such as `/volume1/docker/pdf-privacy-gateway`.
2. Copy `.env.example` to `.env` there and set credentials.
3. In Container Manager, create a **Project** from that folder and its `compose.yml`, then build/start it. Older DSM Docker versions without Compose project support can use `docker compose` through SSH.
4. Configure Synology Reverse Proxy with an HTTPS hostname pointing to `http://127.0.0.1:8765`. Keep application authentication enabled. A reverse proxy in another container needs access to the private network or a suitable host endpoint.
5. Use the HTTPS hostname from your browser. Do not expose this service to the public Internet.

For direct trusted-LAN access, change the port binding in `compose.yml` to your NAS LAN IP, for example `192.168.1.20:8765:8000`. Use HTTPS through a reverse proxy when credentials or documents traverse the LAN: HTTP Basic authentication does not encrypt transport. Restrict access with the NAS firewall.

The base image, Tesseract and PyMuPDF support common Linux amd64 and arm64 hosts. An arm64 image build and an HTTP upload → OCR → select → anonymize workflow in the read-only container are tested; an actual Synology installation and amd64 build still need validation. This is an initial MVP, not an audited privacy product.

## Workflow

1. Drop one or more PDFs. Text, scanned and hybrid PDFs are accepted. PDFs with encrypted content or embedded attachments are rejected.
2. Analysis reads the text layer **and independently OCRs the rendered pixels on every page**, including PDFs that already contain OCR.
3. Review all pages in the PDF.js preview. Solid boxes are confirmed detections; dashed boxes require a decision; ignored regions remain visible. The sidebar also names each state.
4. Confirm or ignore uncertain regions. For mass actions, select occurrence checkboxes in **Detections**, use **Select all on page**, or activate **Select regions** and draw a rectangle across PDF boxes. Any intersecting detection is selected. A new rectangle replaces the current page selection; Shift adds to it. Other pages keep their selection. **Confirm selected** / **Ignore selected** apply to all checked occurrences across documents and pages; confirming also selects matching text elsewhere in each affected PDF; ignoring affects only the checked occurrences. **Clear selection** clears the selection without changing detection states. Ctrl/Command-click a PDF box also toggles its selection; Escape leaves rectangle mode. Correct the original reference value, change its type, split an occurrence, or explicitly merge matching entities. A merge uses the target entity's reference value for all merged occurrences. No fuzzy merges happen automatically: use the entity selectors to compare spelling variants and merge them explicitly.
5. For electronic PDFs, choose **Select text**, highlight characters directly on the page, then click **Redact selected text** and confirm the entity type/value. This uses original visible glyph geometry, not OCR. Multi-line selections keep separate rectangles so intervening text and table columns are not erased; each selected line receives its corresponding replacement text. The server resolves selected character IDs to its own stored glyph coordinates. Confirming text or a readable manual region also selects matching occurrences on every page of that PDF, using the same entity and replacement. The search is case-sensitive, tolerates whitespace/line-break differences, and avoids matching a word/number inside a longer word/number. Native glyph coordinates are preferred; local OCR word boxes cover scanned content (OCR mistakes can cause missed matches). Other PDFs in the batch are not changed. Existing matching suggestions are confirmed instead of duplicated. Ignore remains local; confirming the text again selects its repeats again. Splitting an occurrence and visual-only regions do not expand to repeats. Pages containing only scanned pixels or hidden OCR have this mode disabled; mixed pages expose only visible native glyphs. Native text provenance cannot be proven from PDF structure alone, and unusual clipping/ligatures may require rectangle selection. Very dense pages over 20,000 visible glyphs use rectangle review. **Select regions** still selects existing detections for mass actions, while **Draw sensitive region** creates a new visual region. Use **Draw sensitive region** for missed content. Enter the original value or let local extraction/OCR try to read it. Visual-only redaction works without readable text; its optional token reference has an empty value and the region is left blank. To adjust a region, ignore the old one and draw a new one.
6. Click **Generate anonymized PDFs** when the selection is ready. There is no per-page review checkbox. Confirmed detections and manually added regions are redacted; pending suggestions are automatically marked ignored, and all unselected content remains visible. Editing detections invalidates previous downloads.
7. The application checks text extraction and visual OCR **inside the selected regions**. A failure to remove selected content blocks the download and identifies the affected region. Unselected content, including another occurrence of the same original value elsewhere, does not block generation.
8. After successful generation and verification, the browser immediately downloads the result: one uploaded document produces `document-01.anonymized.pdf`; multiple documents produce `privacy-session.zip` containing `documents/document-01.anonymized.pdf`, etc. Generic output names prevent sensitive source filenames from leaving with the PDFs. A download-again link remains available. Check **Export private token reference (mapping.json)** before generating if you also want a private lookup: for one PDF it is available as a separate download link; for multiple PDFs it is included in the ZIP. Keep that reference locally; do not upload it to the AI.
9. Inspect the exported PDFs before sharing **only the anonymized PDFs** manually with your external AI.

### Private token reference (optional)

`mapping.json` is a versioned, plaintext reference containing each original value, its replacement, and occurrence-specific formatting variants. Typed IDs such as `{{COMPANY_001}}` identify entities in this reference; they are no longer printed in the PDF. Visible replacements have the same character count as the source: uppercase/lowercase letters and digits are replaced in place, while punctuation, whitespace and line breaks stay unchanged. Letters use the same script for Latin, Cyrillic and Greek; other scripts use the same Unicode character category. Repeated values share stable replacements within a session, including case/spacing variants. A fresh session generates new replacements. These are random surrogates, not plausible names or checksum-valid account numbers. Short replacements can coincide with other values; use the entity IDs and occurrence records to disambiguate the private reference. Preserving structure exposes the original length and format. It is excluded from the download unless explicitly selected. The reference is not needed to retain the original PDF: the original file remains unchanged on your computer. No PDF restoration, sprite archive or automated substitution in AI reports is implemented.

## Privacy design and limits

- **Actual pixel removal and fresh PDF construction:** the source is rendered locally, sensitive pixels are erased, and a new PDF is constructed from the sanitized pixels. Fresh local OCR supplies searchable text for the remaining page content; replacements are inserted as visible, extractable text. No source PDF object is copied into an export. This deliberately trades vector fidelity, original typography, links, signatures, forms, layers and perfect text extraction for a smaller hidden-content attack surface. Only the exported anonymized copy is rebuilt; your original local file is never overwritten. Pages are rasterized once at 200 dpi; export reuses the exact canonical bitmap instead of re-rendering it. Verification OCR uses 250 dpi. Token layout inherits the source text baseline, size, color and bold/italic style where visible native glyphs are available. Common sans-serif fonts use bundled complete Liberation Sans fonts; other families may use a local fallback. Replacement text is reduced only when its width exceeds the selected width; equal character counts do not imply equal widths in proportional fonts. Duplicate/contained regions print once. Partially overlapping regions on the same text line must be combined into one selection or one must be ignored before export. Distinct text rows stay separate even if their font bounding boxes overlap. The raster background is still not a vector-preserving export and can look softer than the source at high zoom.
- Source native text coordinates are preserved as invisible hints in a temporary canonical preview. Original visible character boxes and text are also kept in temporary session metadata for direct text selection; hidden or fully transparent text is excluded from that selection layer. Independent OCR checks visual appearance; disagreements are flagged. Those hints are never copied into exports. High-resolution cropped secondary OCR uses contrast and threshold variants for suspicious regions.
- Exports discard original metadata, comments, annotations, actions, links and embedded content. Annotation appearances visible in the source rendering become page pixels and must be reviewed like other content. Attachments are rejected rather than retained. Content outside the visible page crop is not copied.
- Exact normalized identities are shared across a session. Case and whitespace are normalized; accents are not transliterated and approximate names are never silently merged. Entity counters are typed and monotonic. Split creates a new identity; merge is explicit.
- Local rules detect IBANs (registered lengths and mod-97 checksum), German VAT IDs (checksum), labeled tax IDs (checksum where applicable), email, phones, labeled BIC/SWIFT, URLs, and practical German name/company/address/property patterns. Invalid identifier-like values and OCR-confusion repairs require review. BIC has syntax validation, not an external bank-registry lookup. No local LLM or NER model is installed. Rules are intentionally limited: unlabeled names, locations, unusual addresses, other scripts, handwriting, tiny text and OCR errors can be missed. Add regions manually. Checks cover selected regions only and are **not a guarantee of anonymity**.
- **Generate is the final selection decision.** Unconfirmed suggestions are automatically ignored and left visible, including sensitive identifiers. Only confirmed regions are redacted and, if requested, included in the private token reference. Verification is spatial: it checks selected rectangles for original values, not the entire page or batch for every occurrence. Replacement strings intentionally resemble sensitive patterns, so their matching an email/phone detector is not a leak by itself. A value may intentionally remain visible elsewhere. The result describes selected-region redaction rather than whole-document anonymity.
- The versioned mapping includes a SHA-256 digest of canonical JSON. It detects accidental corruption, malformed/duplicate tokens and unsupported versions. **It is plaintext and is not encrypted or authenticated against a malicious editor** who can recompute the digest. Store it securely; do not treat an untrusted mapping as authoritative. Empty reference values represent visual-only regions without a text transcription.
- Sessions expire after 30 minutes of inactivity by default; a sweeper removes data within an additional 15 seconds when idle. In-progress operations hold a lock and count as activity. **Delete session now** removes all its files immediately. Restart/shutdown removes session data. Startup removes crash leftovers from the dedicated temporary root. Downloaded files and browser downloads are your responsibility. Browser memory may retain previews until the tab closes; sessions are not stored in localStorage.
- One configured account protects the whole application. Session IDs prevent accidental cross-session references; this is not multi-user isolation. All authenticated users of the shared account are trusted. Use one Uvicorn worker: temporary state and cleanup are intentionally process-local.
- All routes/assets require HTTP Basic authentication. Mutations require a custom same-origin header, cross-site requests are rejected, CORS is disabled, CSP limits browser connections to this origin, and responses use `Cache-Control: no-store`. Access logging is disabled; processing failures return generic errors without document content. Do not enable request-body logs or verbose PDF/OCR debug output in a reverse proxy.
- The application makes no outbound network requests, but the default Docker bridge does not enforce outbound isolation. If you require host-enforced egress blocking, apply a NAS/host firewall policy or put the application on an internal network behind a local reverse proxy connected to that network. Publishing ports directly from an internal network did not work on the tested Docker Desktop host. The container is not a sandbox against malicious PDF parsers. Keep Docker, dependencies and DSM updated. Build-time package downloads are separate from runtime processing.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `APP_USERNAME`, `APP_PASSWORD` | required | Shared application credentials |
| `SESSION_TTL_SECONDS` | `1800` | Inactivity timeout |
| `OCR_LANGUAGES` | `deu+eng` | Installed Tesseract language identifiers |
| `MAX_FILE_MB` | `20` | Per-PDF upload cap |
| `MAX_BATCH_MB` | `80` | Total upload cap per request |
| `MAX_DOCUMENTS` | `10` | PDFs per anonymization session |
| `MAX_PAGES` | `60` | Per-PDF and anonymization batch page cap |
| `APP_TEMP_DIR` | `/tmp/pdf-privacy-gateway` | Dedicated single-process temporary parent; crash leftovers are deleted on startup |
| `FONT_PATH` | system DejaVu Sans | Local Unicode font (Arial fallback on macOS) |

Rendering is capped at 24 million pixels per page/crop and OCR has timeouts. Request bodies are capped before parsing; uploaded files are bounded again on read. Native PDFs are rendered too, so batch time depends on pixels and page count. Tesseract German and English data are included in the image. Additional languages must be installed at image build time.

## Local development and tests

Python 3.12+, Tesseract with German/English data, and a Unicode font are required.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
export APP_USERNAME=local
export APP_PASSWORD='replace-with-a-local-development-password'
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
.venv/bin/python -m pytest backend/tests -q
```

Do not run with multiple workers or overlapping reload processes: startup cleanup owns the dedicated temporary directory. Run tests with their separate temporary directory. Dependency versions are pinned in `requirements.txt`. The bundled PDF.js version is 5.4.624 (Apache-2.0); `scripts/vendor_pdfjs.sh` refreshes that exact version from npm at development time. Its license and bundled font licenses are retained. PyMuPDF is AGPL/commercial; this repository's AGPL license applies to the application.

The unit/integration suite includes tests for replacement structure and session consistency. `scripts/smoke_running.py --username USER --password PASSWORD` additionally tests a running localhost deployment with synthetic documents. Tests generate synthetic PDFs at runtime; no real documents are committed. Coverage includes normalization, IBAN checksums, OCR confusion, entity ID uniqueness, replacement character groups and formatting, cross-document deduplication, mapping corruption/schema validation, session expiry/isolation, auth/CSRF, manual regions, merge/split, automatic ignores at generation, repeated values outside selected regions, native/scanned/hybrid/broken-OCR pixel redaction, metadata/attachment handling, rotated pages, precise native-character selection, adjacent-line token layout, inherited baseline/color/bold style, byte-identical canonical background pixels, multi-line selection without erasing intervening content, OCR-only selection rejection, optional Unicode token references, default PDF-only downloads, removal of the restore endpoint and download blocking after verification findings.

### API

All endpoints require Basic auth. POST/DELETE requests also need `X-Privacy-Gateway: 1`; uploads must declare `Content-Length`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions` | Create temporary session |
| POST | `/api/sessions/{id}/documents` | Multipart `files` batch |
| POST | `/api/sessions/{id}/analyze` | Extract and independently OCR |
| GET / DELETE | `/api/sessions/{id}` | Read / delete session |
| GET | `/api/sessions/{id}/documents/{doc}/pdf` | Temporary canonical preview |
| POST | `/api/sessions/{id}/detections/from-text` | Add a native text selection using page-local `character_ids` |
| POST | `/api/sessions/{id}/detections/bulk` | Confirm / ignore selected detection IDs atomically |
| POST | `/api/sessions/{id}/detections/{detection}` | Confirm / ignore / edit / split |
| POST | `/api/sessions/{id}/detections` | Add manual region |
| POST | `/api/sessions/{id}/entities/merge` | Explicitly merge identities |
| POST | `/api/sessions/{id}/generate` | Redact and verify selected regions; optional JSON `include_mapping` (default false) |
| GET | `/api/sessions/{id}/download` | Verified ZIP only |
