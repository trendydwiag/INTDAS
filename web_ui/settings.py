"""Django settings for the SPSE Crawler Web UI."""

import os
import re
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# BLOCKER 2 — SECRET_KEY: fail-fast in production
# ---------------------------------------------------------------------------
# Development: provide a default so `manage.py runserver` works without env.
# Production (DEBUG=0): require an explicit key or crash immediately.
_env_secret = os.environ.get("DJANGO_SECRET_KEY", "")
_debug_raw = os.environ.get("DJANGO_DEBUG", "0")
_is_debug = _debug_raw == "1"

if _is_debug:
    SECRET_KEY = _env_secret or "django-insecure-spse-crawler-dev-key-change-in-production"
else:
    if not _env_secret:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    SECRET_KEY = _env_secret

# ---------------------------------------------------------------------------
# BLOCKER 1 — DEBUG: safe default is False (production-safe)
# ---------------------------------------------------------------------------
DEBUG = _is_debug

# ---------------------------------------------------------------------------
# BLOCKER 4 — ALLOWED_HOSTS: explicit required in production
# ---------------------------------------------------------------------------
_raw_hosts = os.environ.get("ALLOWED_HOSTS", "")
if _raw_hosts:
    ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(",") if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
else:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be set in production. "
        "Example: ALLOWED_HOSTS=monitor-lpse.khansia.co.id"
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # v0.0.1
    "spse_crawler.web",
    # v0.0.2 — new modules (spse_crawler.accounts provides custom User model)
    "spse_crawler.accounts",
    "spse_crawler.companies",
    "spse_crawler.ai_match",
    "spse_crawler.submissions",
    "spse_crawler.audit",
]

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "spse_crawler.accounts.backends.EmailBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "spse_crawler.accounts.middleware.RoleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

ROOT_URLCONF = "web_ui.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "spse_crawler" / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "web_ui.wsgi.application"


# ---------------------------------------------------------------------------
# BLOCKER 3 — DATABASE: PostgreSQL required in production
# ---------------------------------------------------------------------------
# Development: SQLite fallback when DJANGO_DEBUG=1 and DATABASE_URL missing.
# Production (DEBUG=0): DATABASE_URL is mandatory; startup crashes otherwise.
def _parse_database_url(url: str) -> dict:
    """Parse postgres://USER:PASS@HOST:PORT/DBNAME into Django DATABASE dict."""
    match = re.match(
        r"postgres(?:ql)?://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<name>[^\s?]+)",
        url,
    )
    if not match:
        return {}
    g = match.groupdict()
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": g["name"],
        "USER": g["user"],
        "PASSWORD": g["password"],
        "HOST": g["host"],
        "PORT": g["port"] or "5432",
    }


_database_url = os.environ.get("DATABASE_URL", "")
if _database_url and _parse_database_url(_database_url):
    DATABASES = {"default": _parse_database_url(_database_url)}
elif DEBUG:
    # SQLite fallback for local development only
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    raise ImproperlyConfigured(
        "DATABASE_URL must be set in production. "
        "Example: DATABASE_URL=postgresql://user:pass@host:5432/dbname"
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# BLOCKER 7 — STATIC_ROOT
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "spse_crawler" / "web" / "static",
]
# Serve compressed static files with WhiteNoise
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
WHITENOISE_MANIFEST_STRICT = False

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# BLOCKER 5 — CSRF_TRUSTED_ORIGINS
# ---------------------------------------------------------------------------
# Explicit env var is preferred. Auto-derive only when DEBUG.
_csrf_trusted_raw = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in _csrf_trusted_raw.split(",")
    if origin.strip()
]
# In DEBUG mode only: auto-derive from ALLOWED_HOSTS.
if DEBUG and not CSRF_TRUSTED_ORIGINS:
    for _host in ALLOWED_HOSTS:
        if _host and _host not in ("*", "localhost", "127.0.0.1", "0.0.0.0"):
            CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")

# When behind a reverse proxy terminating TLS, Django sees plain HTTP internally.
# This header tells Django the original request was HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# BLOCKERS 9 & 10 — SESSION & CSRF COOKIE SECURITY
# ---------------------------------------------------------------------------
# In production (HTTPS), cookies must be marked Secure.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# BLOCKER 8 — PASSWORD VALIDATORS
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
