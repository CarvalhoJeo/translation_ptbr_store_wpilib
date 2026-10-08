from datetime import datetime, timezone
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Vercel sets VERCEL=1 at build and runtime; there, config comes only from project env vars.
ON_VERCEL = bool(os.environ.get("VERCEL"))
if not ON_VERCEL:
    load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if ON_VERCEL:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set on Vercel.")
    # Dev-only fallback; `manage.py check --deploy` flags the django-insecure- prefix.
    SECRET_KEY = "django-insecure-dev-only"

_vercel_hosts = [
    os.environ[name]
    for name in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")
    if os.environ.get(name)
]
ALLOWED_HOSTS = [
    h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h
] + _vercel_hosts
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
] + [f"https://{h}" for h in _vercel_hosts]

if ON_VERCEL:
    # Vercel terminates TLS and forwards the original scheme.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

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
    "shop",
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
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    # Neon's DATABASE_URL goes through PgBouncer (transaction mode), which breaks server-side cursors.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

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
# Vercel sends this as "Authorization: Bearer <CRON_SECRET>" when invoking /cron/sync/.
CRON_SECRET = os.environ.get("CRON_SECRET", "")
# Stay under Vercel Hobby's 300 s function limit; unreached resources go first next run.
SYNC_TIME_BUDGET_SECONDS = int(os.environ.get("SYNC_TIME_BUDGET_SECONDS", "240"))
LAUNCH_AT = datetime.fromisoformat(os.environ.get("LAUNCH_AT") or "2026-11-01T00:00:00Z")
if LAUNCH_AT.tzinfo is None:
    LAUNCH_AT = LAUNCH_AT.replace(tzinfo=timezone.utc)

# E-mail: Gmail SMTP when EMAIL_HOST_USER is set (production), console otherwise.
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
if EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL") or (
    f"Loja de Traduções WPILib <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Loja de Traduções WPILib <noreply@localhost>"
)
# Absolute links in e-mails.
SITE_URL = os.environ.get("SITE_URL") or (
    f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
    if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    else "http://localhost:8000"
)
