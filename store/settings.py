from datetime import datetime, timezone
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
# Dev-only fallback; `manage.py check --deploy` flags the django-insecure- prefix.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or "django-insecure-dev-only"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.github",
    "accounts",
    "ledger",
    "transifex",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "store.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
WSGI_APPLICATION = "store.wsgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "account"
ACCOUNT_LOGOUT_REDIRECT_URL = "home"
SOCIALACCOUNT_ONLY = True
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APPS": [
            {
                "client_id": os.environ.get("GITHUB_CLIENT_ID", ""),
                "secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
                "key": "",
            }
        ],
        "SCOPE": ["read:user"],
    }
}

TRANSIFEX_API_TOKEN = os.environ.get("TRANSIFEX_API_TOKEN", "")
TRANSIFEX_PROJECT = "o:wpilib:p:frc-docs"
TRANSIFEX_LANGUAGE = "l:pt"
LAUNCH_AT = datetime.fromisoformat(os.environ.get("LAUNCH_AT") or "2026-11-01T00:00:00Z")
if LAUNCH_AT.tzinfo is None:
    LAUNCH_AT = LAUNCH_AT.replace(tzinfo=timezone.utc)
