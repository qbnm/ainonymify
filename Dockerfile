FROM composer:2 AS frontend-assets
WORKDIR /build
ENV COMPOSER_ALLOW_SUPERUSER=1
COPY composer.json composer.lock ./
COPY scripts/install_frontend_assets.php scripts/install_frontend_assets.php
RUN composer install --no-dev --prefer-dist --no-interaction --no-progress --no-plugins

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OCR_LANGUAGES=deu+eng APP_TEMP_DIR=/tmp/pdf-privacy-gateway OMP_THREAD_LIMIT=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY --from=frontend-assets /build/frontend/vendor ./frontend/vendor
COPY scripts ./scripts
COPY Dockerfile LICENSE THIRD_PARTY_NOTICES.md README.md SECURITY.md compose.yml composer.json composer.lock requirements-dev.txt .env.example .dockerignore .gitignore ./
COPY docs ./docs
# Fail the build if exact dependency sources/notices cannot be retained.
RUN python scripts/prepare_legal.py
# Composer runs in the build stage; the runtime needs no PHP or dependency downloads.
RUN useradd --uid 10001 --create-home gateway \
    && mkdir -p /data && chown 10001:10001 /data && chmod 700 /data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,urllib.error; r=urllib.request.Request('http://127.0.0.1:8000/'); exec('try:\n urllib.request.urlopen(r)\nexcept urllib.error.HTTPError as e:\n assert e.code == 401')"
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--log-level", "critical"]

# Optional release target: retain exact Debian sources as well as app/dependency
# sources. It deliberately fails if a matching source version is unavailable.
FROM runtime AS distribution-sources
USER root
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && python scripts/collect_system_sources.py \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
