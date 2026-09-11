"""
Base Django settings for ppw project.
Shared across all environments.
"""

from datetime import timedelta
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("SECRET_KEY")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "channels",
    "django_extensions",
    "safedelete",
    "rest_framework",
    "drf_spectacular",
    "rest_framework_simplejwt",
    "django_filters",
    "phonenumber_field",
]

APPS = [
    "apps.assets",
    "apps.users",
    "apps.general",
    "apps.authentication",
    "apps.auctions",
    "apps.ratings",
    "apps.races",
    "apps.packages",
    "apps.loyalty",
    "apps.lucky_wheel",
    "apps.notifications",
    "apps.payments",
    "apps.promotions",
    "apps.referrals",
    "apps.complaints",
    "apps.chat",
    "apps.feed",
    "apps.insights",
    "apps.pedigrees",
    "apps.loft_subscriptions",
    "apps.seasons",
]

DEBUG_APPS = []

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + APPS + DEBUG_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.general.middleware.ApiLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ppw.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ppw.wsgi.application"
ASGI_APPLICATION = "ppw.asgi.application"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.authentication.authentication.ProfileSyncJWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.PageNumberPagination"
    ),
    "PAGE_SIZE": 20,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "PPW API",
    "DESCRIPTION": "Internal development API schema for the PPW backend.",
    "VERSION": "1.0.0",
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
    "PREPROCESSING_HOOKS": [
        "drf_spectacular.hooks.preprocess_exclude_path_format",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=400),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT"),
        "OPTIONS": {
            "sslmode": os.getenv("DB_SSLMODE", "require"),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",  # noqa
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "ar"

LANGUAGES = [
    ("ar", "Arabic"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://localhost:6379/0",
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    CELERY_BROKER_URL,
)
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = USE_TZ
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = env_bool(
    "CELERY_TASK_EAGER_PROPAGATES",
    CELERY_TASK_ALWAYS_EAGER,
)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("CHANNEL_REDIS_URL", CELERY_BROKER_URL)],
        },
    },
}
ORDER_ITEM_SELLER_DECISION_TIMEOUT_DAYS = int(
    os.getenv("ORDER_ITEM_SELLER_DECISION_TIMEOUT_DAYS", "3")
)
PUBLIC_FRONTEND_BASE_URL = os.getenv(
    "PUBLIC_FRONTEND_BASE_URL",
    "http://localhost:3000",
).rstrip("/")
PEDIGREE_REPORT_BASE_URL = os.getenv("PEDIGREE_REPORT_BASE_URL", "").rstrip(
    "/"
)
PEDIGREE_REPORT_API_KEY = os.getenv("PEDIGREE_REPORT_API_KEY", "")
PEDIGREE_REPORT_TIMEOUT = int(os.getenv("PEDIGREE_REPORT_TIMEOUT", "120"))
CELERY_BEAT_SCHEDULE = {
    "close-expired-auctions-every-minute": {
        "task": "auctions.tasks.close_expired_auctions",
        "schedule": timedelta(minutes=1),
    },
    "cancel-expired-pending-order-items-hourly": {
        "task": "orders.tasks.cancel_expired_pending_order_items",
        "schedule": timedelta(hours=12),
    },
}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "authentication.UserAccount"

APPEND_SLASH = False
