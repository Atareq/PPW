"""
Vercel serverless deployment settings.
Extends production: adds WhiteNoise for static files, parses Neon DATABASE_URL,
and runs Celery tasks eagerly.
"""

import os
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import dj_database_url
from ppw.settings.production import *  # noqa: F401,F403

# Parse DATABASE_URL set by the Neon Vercel integration.
# Falls back to individual DB_* vars from base.py if DATABASE_URL is absent.
# Strip channel_binding because psycopg2 does not recognize Neon's parameter.
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    _parsed = urlparse(_db_url)
    _query = {
        key: value
        for key, value in parse_qs(_parsed.query).items()
        if key != "channel_binding"
    }
    _clean_url = urlunparse(_parsed._replace(query=urlencode(_query, doseq=True)))
    DATABASES = {"default": dj_database_url.parse(_clean_url, conn_max_age=600)}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
WHITENOISE_AUTOREFRESH = True

# Vercel is serverless, so there is no separate Celery worker.
# Tasks triggered during a request execute synchronously in-process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CORS_ALLOW_ALL_ORIGINS = True
