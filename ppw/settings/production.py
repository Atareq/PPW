"""
Production settings for ppw project.
"""

from ppw.settings.base import *

DEBUG = False

if not SECRET_KEY:
    raise ValueError("SECRET_KEY is not set")

if not ALLOWED_HOSTS:
    raise ValueError("ALLOWED_HOSTS is not set")

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + APPS

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = False

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# HSTS: enable only when HTTPS is fully working
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
