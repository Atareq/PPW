"""
Local development settings for ppw project.
"""

import os
from dotenv import load_dotenv

from .base import *

load_dotenv(BASE_DIR / ".env")

DEBUG = True

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

DEBUG_APPS = []

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + APPS + DEBUG_APPS

# Optional local email backend example later:
# EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"