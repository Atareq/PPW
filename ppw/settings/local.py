"""
Local development settings for ppw project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

from .base import *  # noqa

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