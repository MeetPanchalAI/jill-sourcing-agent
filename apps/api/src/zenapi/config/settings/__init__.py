"""Django settings — single-file for the baseline.

The production zenafide repo splits this across ``base.py``, ``db_conf.py``,
``aws.py``, ``third_party_conf.py``, ``app_conf.py``, and a ``django/``
subpackage. For the take-home we collapse them into one file — the layout
still matches (settings module path = ``zenapi.config.settings``), so when
you merge this back into the production repo, only this file needs to be
split.
"""

import os
from pathlib import Path

# apps/api/src/zenapi/config/settings/__init__.py → apps/api/
BASE_DIR = Path(__file__).resolve().parents[4]

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-do-not-use-in-prod")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"] if DEBUG else os.environ.get("ALLOWED_HOSTS", "").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "knox",
    "corsheaders",
    "polymorphic",
    # Internal library — Tenant + RLS plumbing
    "zenlib.reusable_apps.multitenant.apps.MultitenantConfig",
    # Your app
    "zenlib_agentos.zenlib.reusable_apps.email_pipeline.apps.EmailPipelineConfig",
    # Jill sourcing agent
    "zenlib_agentos.zenlib.reusable_apps.sourcing.apps.SourcingConfig",
]

# Order matters: context middleware sets the ContextVar that RLS middleware reads.
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "zenlib.reusable_apps.multitenant.middleware.MultitenantContextMiddleware",
    "zenlib.reusable_apps.multitenant.middleware.MultitenantRLSMiddleware",
]

ROOT_URLCONF = "zenapi.config.urls"
WSGI_APPLICATION = "zenapi.wsgi.application"
ASGI_APPLICATION = "zenapi.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "zenapi"),
        "USER": os.environ.get("DB_USER", "zen"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "zen"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        # RLS needs every request to run inside a transaction so SET LOCAL
        # scopes correctly.
        "ATOMIC_REQUESTS": True,
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "knox.auth.TokenAuthentication",
        "zenlib_agentos.zenlib.reusable_apps.email_pipeline.authentication.ServiceTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.PageNumberPagination"
    ),
    "PAGE_SIZE": 50,
}

REST_KNOX = {"TOKEN_TTL": None}

# Shared by ServiceTokenAuthentication. Rotate per environment.
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "dev-service-token-change-me")

# Spend estimation price table (cents). Env-overridable; the sourcing app's
# usage helper multiplies run counters by these to visualize cost.
SOURCING_PRICES = {
    "scrape_cents": float(os.environ.get("PRICE_SCRAPE_CENTS", "0.5")),
    "llm_cents": float(os.environ.get("PRICE_LLM_CENTS", "1.0")),
    "invite_cents": float(os.environ.get("PRICE_INVITE_CENTS", "0")),
    "email_cents": float(os.environ.get("PRICE_EMAIL_CENTS", "0.1")),
}

CORS_ALLOWED_ORIGINS = [os.environ.get("UI_URL", "http://localhost:3000")]
CORS_ALLOW_CREDENTIALS = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
