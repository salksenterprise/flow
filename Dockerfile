FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKFLOW_DB_PATH=/data/workflow.db \
    WORKFLOW_EXAMPLES_PATH=/app/examples \
    PYTHONPATH=/app/packages/workflow-core/src:/app/packages/workflow-sqlite/src:/app/services/workflow-api:/app/services/workflow-worker

WORKDIR /app

COPY services/workflow-api/requirements.txt /app/services/workflow-api/requirements.txt
RUN pip install --no-cache-dir -r /app/services/workflow-api/requirements.txt

COPY packages /app/packages
RUN pip install --no-cache-dir /app/packages/workflow-core /app/packages/workflow-sqlite
COPY services /app/services
COPY examples /app/examples
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 workflow \
    && mkdir -p /data \
    && chown -R workflow:workflow /app /data

USER workflow
WORKDIR /app/services/workflow-api

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
