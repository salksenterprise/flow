# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ISRP_DB_PATH=/data/isrp.db \
    ISRP_HOST=0.0.0.0 \
    ISRP_PORT=8000 \
    ISRP_STATIC_DIR=/app/frontend/dist

WORKDIR /app

RUN groupadd --system isrp \
    && useradd --system --gid isrp --home-dir /app isrp \
    && mkdir -p /data \
    && chown isrp:isrp /data

COPY --chown=isrp:isrp isrp/ ./isrp/
COPY --from=frontend-build --chown=isrp:isrp /build/frontend/dist/ ./frontend/dist/

USER isrp

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2); assert response.status == 200 and json.load(response)['status'] == 'ok'"]

CMD ["python", "-m", "isrp.api"]
