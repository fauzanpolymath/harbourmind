# ── Stage 1: base ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Set working directory
WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Stage 2: dependencies ──────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 3: runtime ───────────────────────────────────────────────────────────
FROM deps AS runtime

# Copy application source
COPY . .

# TODO: Add a non-root user for production hardening
# RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
# USER appuser

EXPOSE 8000

# Use production ASGI server config (uvicorn without --reload)
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
