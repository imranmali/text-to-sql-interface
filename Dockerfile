# syntax=docker/dockerfile:1

# ---- Build stage: produce a wheel of the text2sql package ----
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip wheel --no-cache-dir --no-deps -w /dist .

# ---- Runtime stage: slim image running as a non-root user ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TEXT2SQL_DB_PATH=/app/data/sample.db

COPY --from=builder /dist/*.whl /tmp/dist/
RUN pip install /tmp/dist/*.whl \
    && rm -rf /tmp/dist

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

RUN mkdir -p /app/data \
    && python -c "from text2sql.sample_db import create_sample_database; create_sample_database('/app/data/sample.db')" \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "text2sql.api:app", "--host", "0.0.0.0", "--port", "8000"]
