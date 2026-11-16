# syntax=docker/dockerfile:1

# ---- 1. build the dashboard ------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime: python only, non-root --------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
# scripts/backup.py, so `docker compose exec keel python -m scripts.backup` works (docs/DEPLOY.md)
COPY scripts ./scripts
COPY alembic.ini ./
COPY --from=web /web/dist ./frontend/dist

# Everything the app writes lives under /app/data (mount a volume there). The rest of the image is read-only-safe.
RUN useradd --create-home --uid 10001 keel && mkdir -p /app/data && chown -R keel:keel /app/data
USER keel
ENV DATABASE_URL=sqlite:////app/data/agent.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# ONE worker on purpose: the scheduler and the in-memory rate limiter live inside the process.
CMD ["uvicorn", "app.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
