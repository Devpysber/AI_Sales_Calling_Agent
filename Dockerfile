# ---------- frontend build ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- python deps ----------
FROM python:3.12-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" TZ=Asia/Kolkata
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=deps /venv /venv
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
COPY --from=web --chown=app:app /web/dist ./frontend/dist
RUN mkdir -p data && chown app:app data
USER app
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=4s --retries=3 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"
# WEB_CONCURRENCY workers per container (forced to 1 without Redis, since call state would not be shared)
CMD ["sh", "-c", "W=${WEB_CONCURRENCY:-2}; [ -z \"$REDIS_URL\" ] && W=1; exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w $W -b 0.0.0.0:8000 --timeout 60 --graceful-timeout 30 --keep-alive 5 --forwarded-allow-ips='*' --access-logfile -"]
