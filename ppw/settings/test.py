"""
Test Django settings for ppw project.
Used by pytest.
"""

from ppw.settings.base import *  # noqa: F401, F403

# ============================================================================
# TEST-SPECIFIC SETTINGS
# ============================================================================

# Use in-memory SQLite for tests (faster)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable password hashing for faster tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


# Disable migrations for tests
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

# Speed up tests
SECRET_KEY = "test-secret-key-insecure-do-not-use-in-production"

# Disable CSRF for tests
CSRF_TRUSTED_ORIGINS = ["*"]

# Email backend for testing
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Timezone
USE_TZ = True

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In-memory channel layer for fast and isolated testing
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

LANGUAGE_CODE = "en"
