# Liner Notes: one image with the API, the frontend and the demo data.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/linernotes.db \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Demo exports are generated at build time so the image works offline.
RUN python scripts/make_sample_export.py && python scripts/make_sample_export.py --friend \
    && chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data

ARG VERSION=dev
ENV APP_VERSION=${VERSION}
LABEL org.opencontainers.image.title="Liner Notes" \
      org.opencontainers.image.source="https://github.com/EthanC306/liner-notes-2" \
      org.opencontainers.image.version=${VERSION}

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
