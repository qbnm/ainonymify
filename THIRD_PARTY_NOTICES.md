# Licenses and third-party notices

ainonymify is licensed under **AGPL-3.0-only**; see LICENSE. You may use,
modify and redistribute it under that license. It is supplied WITHOUT ANY
WARRANTY, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
Copyright (c) 2026 ainonymify contributors.

The application offers its current source at `/legal/source`. This snapshot
includes build/install scripts and dependency manifests, including local code
changes, but excludes credentials, documents, databases and runtime data.
The Docker build also retains dependency sources and notices at `/legal/files/`.

## PDF engine

PyMuPDF and MuPDF are Copyright Artifex Software, Inc. and contributors.
This distribution uses their **GNU AGPLv3** option, not a commercial license.
The complete AGPL text is supplied in LICENSE. Generated PDFs identify the
engine and Artifex in their Producer field; private input metadata is discarded.

- https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- https://pymupdf.io/licensing
- https://github.com/pymupdf/PyMuPDF
- https://mupdf.com/downloads/archive/

The source collection script downloads the installed PyMuPDF version's sdist
and the matching full MuPDF source release (including its bundled third-party
sources). The PyMuPDF sdist alone does not contain MuPDF.

## Browser PDF renderer and fonts

PDF.js is Copyright Mozilla Foundation and contributors, **Apache-2.0**.
Its original LICENSE, CMaps notices, standard font licenses, and WASM component
licenses are retained in `frontend/vendor/pdfjs` and copied into the notices
bundle. These include Adobe CMaps, Foxit/PDFium fonts, Liberation fonts
(**SIL OFL 1.1**), OpenJPEG, qcms and the PDFium JBIG2 decoder. Each component
retains its own license; the application's AGPL does not replace those terms.

- https://github.com/mozilla/pdf.js
- https://openfontlicense.org/open-font-license-official-text/

## Python and operating-system dependencies

The generated bundle contains installed Python package metadata, original
license/notice files, and upstream source distributions, with versions and
SHA-256 hashes in `manifest.json`. It includes permissively licensed dependencies
such as FastAPI, Starlette, Uvicorn, Pillow, Pydantic and pytesseract. License
files embedded in wheels are retained, including notices for bundled libraries.

The runtime uses Python, Debian packages, Tesseract, OCR language data and
DejaVu fonts. Installed Debian package versions and their copyright/license
files are retained in the bundle. System components remain under their own
licenses. See `docs/RELEASING.md` for image redistribution and source retention.

## User documents

AGPL does not automatically license a user's documents under AGPL merely
because the application processes them. Original document rights remain with
their owners. Software attribution in PDF metadata contains no source-document
metadata and does not impose a license on document content.
