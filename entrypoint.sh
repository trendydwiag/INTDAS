#!/bin/bash
set -e

echo "SPSE Crawler — Entry point"

# --- Auto-detect CSRF_TRUSTED_ORIGINS from reverse proxy env vars ---
# Caddy sets SERVER_NAME, Nginx can set SERVER_NAME, or use explicit env var.
if [ -z "$CSRF_TRUSTED_ORIGINS" ]; then
    for _var in SERVER_NAME CADDY_HOST NGINX_SERVER_NAME; do
        eval _val="\${${_var}}"
        if [ -n "$_val" ]; then
            # SERVER_NAME can contain spaces (e.g. "example.com www.example.com")
            # Take the first one
            _first=$(echo "$_val" | awk '{print $1}')
            export CSRF_TRUSTED_ORIGINS="https://${_first}"
            echo "[entrypoint] Auto-detected CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS}"
            break
        fi
    done
fi

# 1. Run migrations
echo "[entrypoint] Running migrations ..."
python manage.py migrate --noinput

# 2. Seed production data (idempotent — skips if data exists)
echo "[entrypoint] Seeding data ..."
python manage.py seed_data

# 3. Collect static files
echo "[entrypoint] Collecting static files ..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo "[entrypoint] Starting server ..."
exec "$@"
