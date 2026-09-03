# ============================================
# SPSE Inaproc Crawler — Production Dockerfile
# Multi-stage build for minimal image size
# ============================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for compiling Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY spse_crawler/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Playwright browsers (Chromium only)
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

LABEL maintainer="SPSE Crawler Team"
LABEL description="SPSE Inaproc Multi-Tenant Scraper & Intelligence Dashboard"

# Runtime system deps (Playwright needs these)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libwayland-client0 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages
COPY --from=builder /install /usr/local

# Copy Playwright browsers
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

# Create non-root user
RUN groupadd -r crawler && useradd -r -g crawler -d /app -s /sbin/nologin crawler

WORKDIR /app
COPY . .

# Create data directories
RUN mkdir -p /app/data/exports /app/data/logs \
    && chown -R crawler:crawler /app

# Make entrypoint executable while still root
RUN chmod +x /app/entrypoint.sh

# Environment variables
ENV DJANGO_SETTINGS_MODULE=web_ui.settings \
    DJANGO_DEBUG=0 \
    DJANGO_SECRET_KEY="" \
    SPSE_LOG_LEVEL=INFO \
    SPSE_HEADLESS=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER crawler

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/crawl-progress/')"

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "web_ui.wsgi:application", \
     "--config", "gunicorn.conf.py"]
