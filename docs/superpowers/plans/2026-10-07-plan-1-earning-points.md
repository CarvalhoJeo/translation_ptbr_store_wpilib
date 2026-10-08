# Plan 1 — Earning Points (Django scaffold, accounts, ledger, Transifex sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Django app where translators log in with GitHub and link their Transifex username, admins approve the link, and a cron-run `sync_transifex` command turns new pt translations and reviews on `frc-docs` into points in an append-only ledger.

**Architecture:** Three Django apps. `accounts` holds the Profile and GitHub↔Transifex linking. `ledger` holds point rates, the append-only `PointEntry`, `UnclaimedEvent`, `record_progress` and `balance`. `transifex` holds the HTTP client, the `ResourceTranslationsSource` that turns API JSON into `ProgressEvent`s, `TrackedResource` cursors, `sync_all`, and the management commands. The ledger only ever sees `ProgressEvent`, never Transifex JSON.

**Tech Stack:** Python 3.12 (via `uv`), Django 5.2, django-allauth (GitHub), dj-database-url, python-dotenv, requests; pytest + pytest-django + responses. SQLite for dev and tests in this plan.

**Spec:** `docs/superpowers/specs/2026-10-07-translation-store-design.md`

**Out of scope (Plan 2):** catalog, redemptions, address wipe, leaderboard, Postgres + deploy.

## Global Constraints

- Transifex language id is `l:pt` (not `pt_BR`); project `o:wpilib:p:frc-docs`; API base `https://rest.api.transifex.com`.
- Only actions with timestamp ≥ `LAUNCH_AT` earn points (env var, ISO 8601 UTC, default `2026-11-01T00:00:00Z`).
- Default rates: 2 points per translated word, 1 point per reviewed word; admin-editable; TM-origin translations earn full points.
- Each Transifex translation id earns translation points at most once and review points at most once.
- `PointEntry` rows are never updated or deleted; corrections are new `adjustment` rows.
- The repo is **public**. Secrets come only from `.env` (gitignored). Test fixtures use fake usernames (`alice`, `bob`) and never real API responses. Before every commit, run `git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'`, which must print nothing.
- UI text and user-facing error messages are in pt-BR.
- Commit on the branch `plan-1-earning`, never directly on `main`.

## Review Focus

1. A user types their Transifex name as `u:Alice`, `@alice`, a profile URL, or in a different case, and it should still match the translator `u:alice` → tests in Task 2 (normalize) and Task 3 (case-insensitive match).
2. Two GitHub accounts claim the same Transifex username → the first approval wins, and the second approval fails with a clear message and no points move → test in Task 3.
3. A string translated during a sync, or right at the cursor time → credited on the next run, never lost or doubled → overlap test in Task 6 and idempotency test in Task 7.
4. The API fails halfway through a resource → no partial points for that resource, its cursor doesn't move, `last_error` is set, and other resources still sync → test in Task 7.
5. A string translated before launch but reviewed after it (or with a deleted translator account, `translator: null`) → only the post-launch review is credited, and nothing crashes → tests in Task 6.

---

### Task 1: Project scaffold, settings, home page

**Files:**
- Create: `pyproject.toml`, `manage.py` (generated), `store/settings.py`, `store/urls.py`, `accounts/views.py`, `accounts/urls.py`, `templates/base.html`, `accounts/templates/accounts/home.html`, `tests/__init__.py`, `tests/test_home.py`
- Create apps (generated): `accounts/`, `ledger/`, `transifex/`
- Modify: `.env.example`, local `.env` (DATABASE_URL line only)

**Interfaces:**
- Produces: settings `TRANSIFEX_API_TOKEN: str`, `TRANSIFEX_LANGUAGE = "l:pt"`, `TRANSIFEX_PROJECT = "o:wpilib:p:frc-docs"`, `LAUNCH_AT: datetime (aware, UTC)`; URL names `home`, `account_login`/`account_logout` (allauth); template `base.html` with `{% block content %}`.

- [ ] **Step 1: Create branch and pyproject**

```bash
git checkout -b plan-1-earning
```

`pyproject.toml`:
```toml
[project]
name = "translation-store"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "django>=5.2,<5.3",
    "django-allauth[socialaccount]>=65.0",
    "dj-database-url>=2.2",
    "python-dotenv>=1.0",
    "requests>=2.32",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-django>=4.9",
    "responses>=0.25",
]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "store.settings"
python_files = ["test_*.py"]
pythonpath = ["."]
```

Run: `uv sync`. It downloads Python 3.12 if needed and creates `.venv/`.

- [ ] **Step 2: Generate project and apps**

```bash
uv run django-admin startproject store .
uv run python manage.py startapp accounts
uv run python manage.py startapp ledger
uv run python manage.py startapp transifex
rm accounts/tests.py ledger/tests.py transifex/tests.py
mkdir -p tests templates accounts/templates/accounts && touch tests/__init__.py
```

- [ ] **Step 3: Point DATABASE_URL at SQLite for dev**

In both `.env.example` and `.env`, replace the `DATABASE_URL=...` line with:
```
# Leave empty for local SQLite. Production (Plan 2) uses Postgres.
DATABASE_URL=
```
Also append to `.env.example`, since the GitHub OAuth app is needed for login (the GitHub MCP is unrelated):
```
# GitHub OAuth App for translator login (github.com/settings/developers)
# Callback URL: http://localhost:8000/contas/github/login/callback/
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
```

- [ ] **Step 4: Write the failing test**

`tests/test_home.py`:
```python
import pytest


@pytest.mark.django_db
def test_home_renders_store_name(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Loja de Traduções WPILib" in response.content.decode()
```

- [ ] **Step 5: Run it to verify it fails**

Run: `uv run pytest tests/test_home.py -v`
Expected: FAIL (404, or an error because settings aren't configured yet).

- [ ] **Step 6: Replace `store/settings.py` entirely**

```python
from datetime import datetime
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
```

Note: `LOGIN_REDIRECT_URL = "account"` names a URL that Task 8 creates. Until then, the redirect only matters after a real login.

- [ ] **Step 7: URLs, view, templates**

`store/urls.py`:
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("contas/", include("allauth.urls")),
    path("", include("accounts.urls")),
]
```

`accounts/views.py`:
```python
from django.shortcuts import render


def home(request):
    return render(request, "accounts/home.html")
```

`accounts/urls.py`:
```python
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
]
```

`templates/base.html`:
```html
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Loja de Traduções WPILib{% endblock %}</title>
</head>
<body>
  <header>
    <a href="{% url 'home' %}">Loja de Traduções WPILib</a>
    <nav>
      {% if user.is_authenticated %}
        {% block account_link %}{% endblock %}
        <form method="post" action="{% url 'account_logout' %}" style="display:inline">
          {% csrf_token %}<button type="submit">Sair</button>
        </form>
      {% else %}
        <a href="{% url 'account_login' %}">Entrar com GitHub</a>
      {% endif %}
    </nav>
  </header>
  {% if messages %}
    <ul>{% for message in messages %}<li>{{ message }}</li>{% endfor %}</ul>
  {% endif %}
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`accounts/templates/accounts/home.html`:
```html
{% extends "base.html" %}
{% block content %}
  <h1>Loja de Traduções WPILib</h1>
  <p>Traduza a documentação da WPILib para o português no Transifex, ganhe pontos e troque por brindes.</p>
{% endblock %}
```

- [ ] **Step 8: Migrate and run test**

Run: `uv run python manage.py migrate && uv run pytest tests/test_home.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add -A
git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'   # must print nothing
git commit -m "feat: scaffold Django project with allauth GitHub login and home page"
```

---

### Task 2: Profile model and link requests

**Files:**
- Create: `accounts/models.py` (replace), `accounts/services.py`, `tests/test_accounts.py`

**Interfaces:**
- Produces:
  - `accounts.models.Profile` with fields `user` (OneToOne, `related_name="profile"`), `transifex_username: str`, `link_status: Profile.LinkStatus` (`NONE|PENDING|APPROVED|REJECTED`).
  - `accounts.services.LinkError(Exception)`
  - `accounts.services.normalize_username(raw: str) -> str` (raises `LinkError`)
  - `accounts.services.get_profile(user) -> Profile` (get_or_create)
  - `accounts.services.request_link(user, raw_username: str) -> Profile`

- [ ] **Step 1: Write the failing tests**

`tests/test_accounts.py`:
```python
import pytest

from accounts.models import Profile
from accounts.services import LinkError, get_profile, normalize_username, request_link


@pytest.mark.parametrize(
    "raw",
    ["alice", "  alice  ", "u:alice", "@alice", "https://app.transifex.com/user/profile/alice/"],
)
def test_normalize_username_accepts_common_forms(raw):
    assert normalize_username(raw) == "alice"


def test_normalize_username_keeps_dots_and_underscores():
    assert normalize_username("_.miguel_sr") == "_.miguel_sr"


@pytest.mark.parametrize("raw", ["", "   ", "two words", "a/b c"])
def test_normalize_username_rejects_invalid(raw):
    with pytest.raises(LinkError):
        normalize_username(raw)


@pytest.mark.django_db
def test_request_link_sets_pending(django_user_model):
    user = django_user_model.objects.create_user("ana")
    profile = request_link(user, "u:Alice")
    assert profile.transifex_username == "Alice"
    assert profile.link_status == Profile.LinkStatus.PENDING


@pytest.mark.django_db
def test_request_link_rejects_username_approved_for_someone_else(django_user_model):
    owner = django_user_model.objects.create_user("owner")
    owner_profile = get_profile(owner)
    owner_profile.transifex_username = "alice"
    owner_profile.link_status = Profile.LinkStatus.APPROVED
    owner_profile.save()

    other = django_user_model.objects.create_user("other")
    with pytest.raises(LinkError):
        request_link(other, "ALICE")


@pytest.mark.django_db
def test_request_link_refuses_to_change_an_approved_link(django_user_model):
    user = django_user_model.objects.create_user("ana")
    profile = get_profile(user)
    profile.transifex_username = "alice"
    profile.link_status = Profile.LinkStatus.APPROVED
    profile.save()
    with pytest.raises(LinkError):
        request_link(user, "bob")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: FAIL with `ImportError: cannot import name 'Profile'`

- [ ] **Step 3: Implement**

`accounts/models.py`:
```python
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class Profile(models.Model):
    class LinkStatus(models.TextChoices):
        NONE = "none", "Sem vínculo"
        PENDING = "pending", "Aguardando aprovação"
        APPROVED = "approved", "Aprovado"
        REJECTED = "rejected", "Recusado"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    transifex_username = models.CharField("usuário no Transifex", max_length=150, blank=True)
    link_status = models.CharField(max_length=10, choices=LinkStatus.choices, default=LinkStatus.NONE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("transifex_username"),
                condition=models.Q(link_status="approved"),
                name="unique_approved_transifex_username",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.transifex_username or '—'} ({self.get_link_status_display()})"
```

`accounts/services.py`:
```python
import re

from .models import Profile

_USERNAME_RE = re.compile(r"^[\w.@+-]+$")


class LinkError(Exception):
    pass


def normalize_username(raw: str) -> str:
    name = raw.strip().rstrip("/")
    if "/" in name:
        name = name.rsplit("/", 1)[1]
    name = name.removeprefix("u:").removeprefix("@")
    if not name or not _USERNAME_RE.match(name):
        raise LinkError("Nome de usuário do Transifex inválido.")
    return name


def get_profile(user) -> Profile:
    return Profile.objects.get_or_create(user=user)[0]


def request_link(user, raw_username: str) -> Profile:
    name = normalize_username(raw_username)
    profile = get_profile(user)
    if profile.link_status == Profile.LinkStatus.APPROVED:
        raise LinkError("Seu vínculo já foi aprovado; peça a um admin para alterar.")
    taken = (
        Profile.objects.filter(link_status=Profile.LinkStatus.APPROVED, transifex_username__iexact=name)
        .exclude(pk=profile.pk)
        .exists()
    )
    if taken:
        raise LinkError("Este usuário do Transifex já está vinculado a outra conta.")
    profile.transifex_username = name
    profile.link_status = Profile.LinkStatus.PENDING
    profile.save()
    return profile
```

Run: `uv run python manage.py makemigrations accounts`

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add -A
git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'   # must print nothing
git commit -m "feat: add Profile with Transifex username link requests"
```

---

### Task 3: Ledger, link approval, admin

**Files:**
- Create: `ledger/events.py`, `ledger/models.py` (replace), `ledger/services.py`, `ledger/admin.py` (replace), `accounts/admin.py` (replace), `tests/test_ledger.py`
- Modify: `accounts/services.py` (add `approve_link`, `reject_link`)

**Interfaces:**
- Consumes: `Profile`, `LinkError`, `get_profile` from Task 2.
- Produces:
  - `ledger.events.ProgressEvent(tx_username: str, kind: str, string_key: str, words: int, occurred_at: datetime)`, a frozen dataclass; `kind` is `PointEntry.Kind.TRANSLATED` or `PointEntry.Kind.REVIEWED`.
  - `ledger.models.PointRates.current() -> PointRates` (fields `translated_word`, `reviewed_word`)
  - `ledger.models.PointEntry` (fields `user`, `amount`, `kind`, `string_key`, `words`, `occurred_at`, `note`, `created_at`; `related_name="point_entries"`)
  - `ledger.models.UnclaimedEvent`
  - `ledger.services.points_for(kind: str, words: int) -> int`
  - `ledger.services.record_progress(event: ProgressEvent) -> bool` (True when newly recorded)
  - `ledger.services.claim_unclaimed(user, tx_username: str) -> int`
  - `ledger.services.balance(user) -> int`
  - `accounts.services.approve_link(profile) -> int` (number of claimed events), `accounts.services.reject_link(profile) -> None`

Note: the spec's idempotency key `(source, string_key, kind)` becomes `(string_key, kind)`. Transifex is the only source, and its translation ids are globally unique.

- [ ] **Step 1: Write the failing tests**

`tests/test_ledger.py`:
```python
from datetime import datetime, timezone

import pytest

from accounts.models import Profile
from accounts.services import LinkError, approve_link, get_profile, request_link
from ledger.events import ProgressEvent
from ledger.models import PointEntry, PointRates, UnclaimedEvent
from ledger.services import balance, record_progress

pytestmark = pytest.mark.django_db

T = datetime(2026, 11, 2, tzinfo=timezone.utc)


def event(user="alice", kind=PointEntry.Kind.TRANSLATED, key="r:x:s:1:l:pt", words=10):
    return ProgressEvent(tx_username=user, kind=kind, string_key=key, words=words, occurred_at=T)


def approved(django_user_model, login, tx_name):
    user = django_user_model.objects.create_user(login)
    profile = request_link(user, tx_name)
    approve_link(profile)
    return user


def test_translation_credits_approved_user_at_default_rate(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    assert record_progress(event(words=10)) is True
    assert balance(user) == 20


def test_review_uses_review_rate(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(kind=PointEntry.Kind.REVIEWED, words=10))
    assert balance(user) == 10


def test_matching_is_case_insensitive(django_user_model):
    user = approved(django_user_model, "ana", "Alice")
    record_progress(event(user="alice"))
    assert balance(user) == 20


def test_same_event_twice_is_ignored(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    assert record_progress(event()) is True
    assert record_progress(event()) is False
    assert balance(user) == 20


def test_translate_and_review_same_string_both_count(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event())
    record_progress(event(kind=PointEntry.Kind.REVIEWED))
    assert balance(user) == 30


def test_unknown_translator_is_parked_then_claimed_on_approval(django_user_model):
    assert record_progress(event(user="bob", words=5)) is True
    assert UnclaimedEvent.objects.count() == 1
    assert record_progress(event(user="bob", words=5)) is False  # idempotent while parked

    user = django_user_model.objects.create_user("bruno")
    profile = request_link(user, "Bob")
    assert approve_link(profile) == 1
    assert balance(user) == 10
    assert UnclaimedEvent.objects.count() == 0


def test_rate_change_only_affects_future_events(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(key="k1", words=10))
    rates = PointRates.current()
    rates.translated_word = 5
    rates.save()
    record_progress(event(key="k2", words=10))
    assert balance(user) == 20 + 50


def test_second_account_cannot_be_approved_for_same_username(django_user_model):
    approved(django_user_model, "ana", "alice")
    other = django_user_model.objects.create_user("intruso")
    profile = get_profile(other)
    profile.transifex_username = "ALICE"
    profile.link_status = Profile.LinkStatus.PENDING
    profile.save()
    with pytest.raises(LinkError):
        approve_link(profile)
    profile.refresh_from_db()
    assert profile.link_status == Profile.LinkStatus.PENDING


def test_balance_includes_negative_entries(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(words=10))
    PointEntry.objects.create(user=user, amount=-15, kind=PointEntry.Kind.ADJUSTMENT, note="teste")
    assert balance(user) == 5


def test_balance_of_user_without_entries_is_zero(django_user_model):
    assert balance(django_user_model.objects.create_user("nova")) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: FAIL with `ImportError` (`approve_link` / `ledger.events`)

- [ ] **Step 3: Implement ledger**

`ledger/events.py`:
```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ProgressEvent:
    """One credited action on Transifex, already normalized. The ledger never sees API JSON."""

    tx_username: str
    kind: str  # PointEntry.Kind.TRANSLATED or PointEntry.Kind.REVIEWED
    string_key: str  # Transifex resource_translation id
    words: int
    occurred_at: datetime
```

`ledger/models.py`:
```python
from django.conf import settings
from django.db import models


class PointRates(models.Model):
    translated_word = models.PositiveIntegerField("pontos por palavra traduzida", default=2)
    reviewed_word = models.PositiveIntegerField("pontos por palavra revisada", default=1)

    class Meta:
        verbose_name = verbose_name_plural = "taxas de pontos"

    @classmethod
    def current(cls) -> "PointRates":
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return f"{self.translated_word}/palavra traduzida, {self.reviewed_word}/palavra revisada"


class PointEntry(models.Model):
    """Append-only. Never update or delete rows; add an adjustment instead."""

    class Kind(models.TextChoices):
        TRANSLATED = "translated", "Tradução"
        REVIEWED = "reviewed", "Revisão"
        REDEMPTION = "redemption", "Resgate"
        REFUND = "refund", "Estorno"
        ADJUSTMENT = "adjustment", "Ajuste"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="point_entries")
    amount = models.IntegerField()
    kind = models.CharField(max_length=12, choices=Kind.choices)
    string_key = models.CharField(max_length=500, blank=True)
    words = models.PositiveIntegerField(default=0)
    occurred_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["string_key", "kind"],
                condition=~models.Q(string_key=""),
                name="unique_progress_per_string",
            )
        ]

    def __str__(self):
        return f"{self.user} {self.amount:+d} ({self.get_kind_display()})"


class UnclaimedEvent(models.Model):
    """Post-launch progress by a Transifex user with no approved link yet."""

    tx_username = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=PointEntry.Kind.choices)
    string_key = models.CharField(max_length=500)
    words = models.PositiveIntegerField()
    amount = models.IntegerField()
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["string_key", "kind"], name="unique_unclaimed_per_string")]
```

`ledger/services.py`:
```python
from django.db import transaction
from django.db.models import Sum

from accounts.models import Profile

from .events import ProgressEvent
from .models import PointEntry, PointRates, UnclaimedEvent


def points_for(kind: str, words: int) -> int:
    rates = PointRates.current()
    per_word = rates.translated_word if kind == PointEntry.Kind.TRANSLATED else rates.reviewed_word
    return per_word * words


def record_progress(event: ProgressEvent) -> bool:
    key = {"string_key": event.string_key, "kind": event.kind}
    if PointEntry.objects.filter(**key).exists() or UnclaimedEvent.objects.filter(**key).exists():
        return False
    amount = points_for(event.kind, event.words)
    profile = (
        Profile.objects.filter(link_status=Profile.LinkStatus.APPROVED, transifex_username__iexact=event.tx_username)
        .select_related("user")
        .first()
    )
    if profile:
        PointEntry.objects.create(
            user=profile.user, amount=amount, words=event.words, occurred_at=event.occurred_at, **key
        )
    else:
        UnclaimedEvent.objects.create(
            tx_username=event.tx_username, words=event.words, amount=amount, occurred_at=event.occurred_at, **key
        )
    return True


def claim_unclaimed(user, tx_username: str) -> int:
    with transaction.atomic():
        events = list(UnclaimedEvent.objects.select_for_update().filter(tx_username__iexact=tx_username))
        PointEntry.objects.bulk_create(
            PointEntry(
                user=user,
                amount=e.amount,
                kind=e.kind,
                string_key=e.string_key,
                words=e.words,
                occurred_at=e.occurred_at,
            )
            for e in events
        )
        UnclaimedEvent.objects.filter(pk__in=[e.pk for e in events]).delete()
    return len(events)


def balance(user) -> int:
    return PointEntry.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0
```

- [ ] **Step 4: Add approve/reject to `accounts/services.py`**

Add these imports at the top:
```python
from django.db import IntegrityError, transaction
```
Append:
```python
def approve_link(profile: Profile) -> int:
    from ledger.services import claim_unclaimed  # ledger imports accounts.models; avoid a cycle at import time

    if not profile.transifex_username:
        raise LinkError("Nenhum usuário do Transifex informado.")
    try:
        with transaction.atomic():
            profile.link_status = Profile.LinkStatus.APPROVED
            profile.save(update_fields=["link_status"])
            return claim_unclaimed(profile.user, profile.transifex_username)
    except IntegrityError:
        profile.link_status = Profile.LinkStatus.PENDING
        raise LinkError("Este usuário do Transifex já está vinculado a outra conta.")


def reject_link(profile: Profile) -> None:
    profile.link_status = Profile.LinkStatus.REJECTED
    profile.save(update_fields=["link_status"])
```

- [ ] **Step 5: Admin**

`accounts/admin.py`:
```python
from django.contrib import admin, messages

from .models import Profile
from .services import LinkError, approve_link, reject_link


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "transifex_username", "link_status"]
    list_filter = ["link_status"]
    search_fields = ["user__username", "transifex_username"]
    actions = ["approve", "reject"]

    @admin.action(description="Aprovar vínculo com o Transifex")
    def approve(self, request, queryset):
        for profile in queryset:
            try:
                claimed = approve_link(profile)
                self.message_user(request, f"{profile.user}: aprovado ({claimed} eventos creditados).")
            except LinkError as exc:
                self.message_user(request, f"{profile.user}: {exc}", level=messages.ERROR)

    @admin.action(description="Recusar vínculo")
    def reject(self, request, queryset):
        for profile in queryset:
            reject_link(profile)
```

`ledger/admin.py`:
```python
from django.contrib import admin

from .models import PointEntry, PointRates, UnclaimedEvent


@admin.register(PointRates)
class PointRatesAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PointRates.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PointEntry)
class PointEntryAdmin(admin.ModelAdmin):
    """Admins may only add manual adjustments; existing entries are immutable."""

    list_display = ["created_at", "user", "amount", "kind", "words", "note"]
    list_filter = ["kind"]
    search_fields = ["user__username", "string_key", "note"]
    fields = ["user", "amount", "note"]

    def save_model(self, request, obj, form, change):
        obj.kind = PointEntry.Kind.ADJUSTMENT
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UnclaimedEvent)
class UnclaimedEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "tx_username", "kind", "words", "amount"]
    search_fields = ["tx_username"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
```

Run: `uv run python manage.py makemigrations ledger`

- [ ] **Step 6: Run all tests**

Run: `uv run pytest -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add -A
git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'   # must print nothing
git commit -m "feat: add append-only points ledger, unclaimed events and link approval"
```

---

### Task 4: Word counting

**Files:**
- Create: `transifex/words.py`, `tests/test_words.py`

**Interfaces:**
- Produces: `transifex.words.count_words(strings: dict[str, str]) -> int`. `strings` is a Transifex `resource_string.attributes.strings` dict like `{"other": "..."}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_words.py`:
```python
import pytest

from transifex.words import count_words


@pytest.mark.parametrize(
    "strings, expected",
    [
        ({"other": "Build Instructions"}, 2),
        ({"other": "Hello {name}, you have %(count)d items"}, 4),
        ({"other": "<b>Bold</b> text"}, 2),
        ({"other": "Use %s or %d here"}, 3),
        ({"one": "1 file", "other": "{n} files"}, 1),
        ({"other": "   "}, 0),
        ({"other": "— , ."}, 0),
        ({}, 0),
    ],
)
def test_count_words(strings, expected):
    assert count_words(strings) == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_words.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transifex.words'`

- [ ] **Step 3: Implement**

`transifex/words.py`:
```python
import re

# printf-style (%s, %(name)d), brace placeholders ({name}) and HTML/XML tags
_PLACEHOLDER = re.compile(r"%\([^)]*\)[sdif]|%[sdif]|\{[^{}]*\}|<[^>]+>")


def count_words(strings: dict[str, str]) -> int:
    """Words in a source string, ignoring placeholders and markup. Plurals use the 'other' form."""
    text = strings.get("other") or next(iter(strings.values()), "")
    text = _PLACEHOLDER.sub(" ", text)
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_words.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: count source words ignoring placeholders and markup"
```

---

### Task 5: Transifex HTTP client

**Files:**
- Create: `transifex/client.py`, `tests/test_client.py`

**Interfaces:**
- Produces:
  - `transifex.client.TransifexError(Exception)`
  - `transifex.client.TransifexClient(token: str, *, session=None, sleep=time.sleep, max_retries: int = 3)`
  - `TransifexClient.iter_resources(project_id: str) -> Iterator[str]` (resource ids)
  - `TransifexClient.iter_translations(resource_id: str, language: str, filters: dict[str, str]) -> Iterator[tuple[dict, dict[str, str]]]`. Yields `(resource_translation_item, source_strings)`, where `source_strings` is the included `resource_string.attributes.strings` (or `{}`).

- [ ] **Step 1: Write the failing tests**

`tests/test_client.py`:
```python
import pytest
import responses
from responses import matchers

from transifex.client import TransifexClient, TransifexError

URL = "https://rest.api.transifex.com/resource_translations"
RES = "o:wpilib:p:frc-docs:r:demo"


def item(n, user="alice"):
    return {
        "id": f"{RES}:s:{n}:l:pt",
        "attributes": {"origin": "EDITOR", "datetime_translated": "2026-11-02T10:00:00Z"},
        "relationships": {
            "translator": {"data": {"type": "users", "id": f"u:{user}"}},
            "reviewer": None,
            "resource_string": {"data": {"type": "resource_strings", "id": f"{RES}:s:{n}"}},
        },
    }


def included(n, text):
    return {"type": "resource_strings", "id": f"{RES}:s:{n}", "attributes": {"strings": {"other": text}}}


@responses.activate
def test_iter_translations_follows_next_and_maps_source_strings():
    first_params = {"filter[resource]": RES, "filter[language]": "l:pt", "include": "resource_string", "filter[translated]": "true"}
    responses.get(
        URL,
        match=[matchers.query_param_matcher(first_params)],
        json={"data": [item(1)], "included": [included(1, "Hello world")], "links": {"next": f"{URL}?page=2"}},
    )
    responses.get(
        URL,
        match=[matchers.query_param_matcher({"page": "2"})],
        json={"data": [item(2, "bob")], "included": [included(2, "Bye")], "links": {"next": None}},
    )
    client = TransifexClient("tok", sleep=lambda s: None)

    rows = list(client.iter_translations(RES, "l:pt", {"filter[translated]": "true"}))

    assert [r[0]["id"] for r in rows] == [f"{RES}:s:1:l:pt", f"{RES}:s:2:l:pt"]
    assert rows[0][1] == {"other": "Hello world"}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_retries_on_429_then_succeeds():
    responses.get(URL, status=429)
    responses.get(URL, json={"data": [], "links": {"next": None}})
    sleeps = []
    client = TransifexClient("tok", sleep=sleeps.append)

    assert list(client.iter_translations(RES, "l:pt", {})) == []
    assert sleeps == [1]


@responses.activate
def test_403_raises_without_retry():
    responses.get(URL, status=403, json={"errors": [{"detail": "Your plan does not support"}]})
    client = TransifexClient("tok", sleep=lambda s: pytest.fail("should not retry"))

    with pytest.raises(TransifexError, match="403"):
        list(client.iter_translations(RES, "l:pt", {}))


@responses.activate
def test_gives_up_after_max_retries():
    for _ in range(4):
        responses.get(URL, status=503)
    client = TransifexClient("tok", sleep=lambda s: None, max_retries=3)

    with pytest.raises(TransifexError, match="503"):
        list(client.iter_translations(RES, "l:pt", {}))


@responses.activate
def test_iter_resources_yields_ids():
    responses.get(
        "https://rest.api.transifex.com/resources",
        match=[matchers.query_param_matcher({"filter[project]": "o:wpilib:p:frc-docs"})],
        json={"data": [{"id": f"{RES}1"}, {"id": f"{RES}2"}], "links": {"next": None}},
    )
    client = TransifexClient("tok")
    assert list(client.iter_resources("o:wpilib:p:frc-docs")) == [f"{RES}1", f"{RES}2"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transifex.client'`

- [ ] **Step 3: Implement**

`transifex/client.py`:
```python
import time
from collections.abc import Iterator

import requests

BASE_URL = "https://rest.api.transifex.com"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class TransifexError(Exception):
    pass


class TransifexClient:
    def __init__(self, token: str, *, session=None, sleep=time.sleep, max_retries: int = 3):
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json"}
        )
        self._sleep = sleep
        self._max_retries = max_retries

    def _get(self, url: str, params: dict | None) -> dict:
        for attempt in range(self._max_retries + 1):
            last_attempt = attempt == self._max_retries
            try:
                resp = self._session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                if last_attempt:
                    raise TransifexError(f"falha de rede em {url}: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and not last_attempt:
                self._sleep(2**attempt)
                continue
            if resp.status_code != 200:
                raise TransifexError(f"HTTP {resp.status_code} em {resp.url}: {resp.text[:300]}")
            return resp.json()
        raise AssertionError("unreachable")

    def _paginate(self, path: str, params: dict) -> Iterator[dict]:
        url, query = BASE_URL + path, params
        while url:
            body = self._get(url, query)
            yield body
            url, query = (body.get("links") or {}).get("next"), None

    def iter_resources(self, project_id: str) -> Iterator[str]:
        for body in self._paginate("/resources", {"filter[project]": project_id}):
            for item in body["data"]:
                yield item["id"]

    def iter_translations(
        self, resource_id: str, language: str, filters: dict[str, str]
    ) -> Iterator[tuple[dict, dict[str, str]]]:
        params = {
            "filter[resource]": resource_id,
            "filter[language]": language,
            "include": "resource_string",
            **filters,
        }
        for body in self._paginate("/resource_translations", params):
            sources = {
                inc["id"]: inc["attributes"]["strings"]
                for inc in body.get("included", [])
                if inc["type"] == "resource_strings"
            }
            for item in body["data"]:
                string_id = item["relationships"]["resource_string"]["data"]["id"]
                yield item, sources.get(string_id) or {}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Transifex API client with pagination and retries"
```

---

### Task 6: ProgressSource for resource_translations

**Files:**
- Create: `transifex/source.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: `ProgressEvent`, `PointEntry.Kind` (Task 3); `count_words` (Task 4); a client with `iter_translations(resource_id, language, filters)` (Task 5).
- Produces:
  - `transifex.source.ProgressSource` (Protocol): `events_for(resource_id: str, since: datetime) -> Iterator[ProgressEvent]`
  - `transifex.source.ResourceTranslationsSource(client, language: str, launch_at: datetime)`, with class attribute `OVERLAP = timedelta(hours=1)`

Behavior:
- Translations: query `filter[translated]=true&filter[date_translated][gt]=<max(since, launch_at) − OVERLAP, as UTC "%Y-%m-%dT%H:%M:%SZ">`.
- Reviews: query `filter[reviewed]=true`, with no date filter, because the API has none (confirmed in the spike).
- Emit only when the user relationship is non-null and the timestamp ≥ `launch_at`. Usernames drop the `u:` prefix. `string_key` = translation item id.

- [ ] **Step 1: Write the failing tests**

`tests/test_source.py`:
```python
from datetime import datetime, timezone

from ledger.models import PointEntry
from transifex.source import ResourceTranslationsSource

LAUNCH = datetime(2026, 11, 1, tzinfo=timezone.utc)
RES = "o:wpilib:p:frc-docs:r:demo"


def tx_item(n, translator="alice", translated="2026-11-02T10:00:00Z", reviewer=None, reviewed=None):
    def user(name):
        return {"data": {"type": "users", "id": f"u:{name}"}} if name else None

    return {
        "id": f"{RES}:s:{n}:l:pt",
        "attributes": {"datetime_translated": translated, "datetime_reviewed": reviewed},
        "relationships": {"translator": user(translator), "reviewer": user(reviewer)},
    }


class FakeClient:
    def __init__(self, translated=(), reviewed=()):
        self.translated, self.reviewed, self.calls = list(translated), list(reviewed), []

    def iter_translations(self, resource_id, language, filters):
        self.calls.append(filters)
        rows = self.reviewed if filters.get("filter[reviewed]") == "true" else self.translated
        return iter(rows)


def run(client, since=LAUNCH):
    return list(ResourceTranslationsSource(client, "l:pt", LAUNCH).events_for(RES, since))


def test_translation_after_launch_becomes_event():
    client = FakeClient(translated=[(tx_item(1), {"other": "Hello big world"})])
    [ev] = run(client)
    assert ev.tx_username == "alice"
    assert ev.kind == PointEntry.Kind.TRANSLATED
    assert ev.string_key == f"{RES}:s:1:l:pt"
    assert ev.words == 3
    assert ev.occurred_at == datetime(2026, 11, 2, 10, tzinfo=timezone.utc)


def test_translation_before_launch_is_skipped():
    client = FakeClient(translated=[(tx_item(1, translated="2026-10-31T23:59:59Z"), {"other": "Hi"})])
    assert run(client) == []


def test_null_translator_is_skipped():
    client = FakeClient(translated=[(tx_item(1, translator=None), {"other": "Hi"})])
    assert run(client) == []


def test_pre_launch_translation_reviewed_after_launch_credits_only_review():
    row = tx_item(1, translated="2026-10-01T00:00:00Z", reviewer="bob", reviewed="2026-11-03T00:00:00Z")
    client = FakeClient(translated=[(row, {"other": "Hi there"})], reviewed=[(row, {"other": "Hi there"})])
    events = run(client)
    assert [(e.tx_username, e.kind, e.words) for e in events] == [("bob", PointEntry.Kind.REVIEWED, 2)]


def test_review_before_launch_is_skipped():
    row = tx_item(1, reviewer="bob", reviewed="2026-10-15T00:00:00Z")
    client = FakeClient(reviewed=[(row, {"other": "Hi"})])
    assert [e.kind for e in run(client)] == []


def test_translated_query_overlaps_cursor_by_one_hour():
    client = FakeClient()
    run(client, since=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc))
    assert client.calls[0] == {"filter[translated]": "true", "filter[date_translated][gt]": "2026-11-05T11:00:00Z"}
    assert client.calls[1] == {"filter[reviewed]": "true"}


def test_since_before_launch_is_clamped_to_launch():
    client = FakeClient()
    run(client, since=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert client.calls[0]["filter[date_translated][gt]"] == "2026-10-31T23:00:00Z"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'transifex.source'`

- [ ] **Step 3: Implement**

`transifex/source.py`:
```python
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ledger.events import ProgressEvent
from ledger.models import PointEntry

from .words import count_words


class ProgressSource(Protocol):
    def events_for(self, resource_id: str, since: datetime) -> Iterator[ProgressEvent]: ...


def _username(relationship: dict | None) -> str | None:
    data = (relationship or {}).get("data")
    return data["id"].removeprefix("u:") if data else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class ResourceTranslationsSource:
    """Builds events from GET /resource_translations (free plan). See spec: Sync."""

    # Re-read a margin before the cursor so strings saved mid-sync are not missed; the ledger dedupes.
    OVERLAP = timedelta(hours=1)

    def __init__(self, client, language: str, launch_at: datetime):
        self._client = client
        self._language = language
        self._launch_at = launch_at

    def events_for(self, resource_id: str, since: datetime) -> Iterator[ProgressEvent]:
        after = (max(since, self._launch_at) - self.OVERLAP).astimezone(timezone.utc)
        translated = {"filter[translated]": "true", "filter[date_translated][gt]": after.strftime("%Y-%m-%dT%H:%M:%SZ")}
        for item, strings in self._client.iter_translations(resource_id, self._language, translated):
            event = self._event(item, strings, PointEntry.Kind.TRANSLATED, "translator", "datetime_translated")
            if event:
                yield event
        # No review-date filter exists in the API, so scan all reviewed strings; the ledger dedupes.
        for item, strings in self._client.iter_translations(resource_id, self._language, {"filter[reviewed]": "true"}):
            event = self._event(item, strings, PointEntry.Kind.REVIEWED, "reviewer", "datetime_reviewed")
            if event:
                yield event

    def _event(self, item, strings, kind, user_field, time_field) -> ProgressEvent | None:
        username = _username(item["relationships"].get(user_field))
        occurred_at = _parse(item["attributes"].get(time_field))
        if not username or occurred_at is None or occurred_at < self._launch_at:
            return None
        return ProgressEvent(
            tx_username=username,
            kind=kind,
            string_key=item["id"],
            words=count_words(strings),
            occurred_at=occurred_at,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_source.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: turn Transifex resource translations into progress events"
```

---

### Task 7: Tracked resources, sync, management commands

**Files:**
- Create: `transifex/models.py` (replace), `transifex/admin.py` (replace), `transifex/sync.py`, `transifex/management/__init__.py`, `transifex/management/commands/__init__.py`, `transifex/management/commands/sync_transifex.py`, `transifex/management/commands/track_resources.py`, `tests/test_sync.py`

**Interfaces:**
- Consumes: `record_progress`, `balance` (Task 3); `TransifexClient`, `TransifexError` (Task 5); `ResourceTranslationsSource`, `ProgressSource` (Task 6).
- Produces:
  - `transifex.models.TrackedResource(resource_id, active, cursor, last_synced_at, last_error)`
  - `transifex.sync.SyncResult(resources_ok: int, resources_failed: int, new_events: int)`
  - `transifex.sync.sync_resource(tracked, source, now, launch_at) -> int`
  - `transifex.sync.sync_all(source, now, launch_at) -> SyncResult`
  - Commands `manage.py sync_transifex` and `manage.py track_resources [--project ID]`

- [ ] **Step 1: Write the failing tests**

`tests/test_sync.py`:
```python
from datetime import datetime, timezone

import pytest
import responses
from django.core.management import CommandError, call_command
from responses import matchers

from accounts.services import approve_link, request_link
from ledger.events import ProgressEvent
from ledger.models import PointEntry
from ledger.services import balance
from transifex.client import TransifexError
from transifex.models import TrackedResource
from transifex.sync import sync_all

pytestmark = pytest.mark.django_db

LAUNCH = datetime(2026, 11, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def ev(key, user="alice", words=10):
    return ProgressEvent(user, PointEntry.Kind.TRANSLATED, key, words, datetime(2026, 11, 2, tzinfo=timezone.utc))


class FakeSource:
    def __init__(self, by_resource, fail=()):
        self.by_resource, self.fail, self.since = by_resource, set(fail), {}

    def events_for(self, resource_id, since):
        self.since[resource_id] = since
        yield from self.by_resource.get(resource_id, [])
        if resource_id in self.fail:
            raise TransifexError("HTTP 503 no meio da paginação")


@pytest.fixture
def ana(django_user_model):
    user = django_user_model.objects.create_user("ana")
    approve_link(request_link(user, "alice"))
    return user


def test_sync_credits_points_and_sets_cursor(ana):
    TrackedResource.objects.create(resource_id="r1")
    result = sync_all(FakeSource({"r1": [ev("k1"), ev("k2")]}), NOW, LAUNCH)

    assert (result.resources_ok, result.resources_failed, result.new_events) == (1, 0, 2)
    assert balance(ana) == 40
    tracked = TrackedResource.objects.get()
    assert tracked.cursor == NOW and tracked.last_synced_at == NOW and tracked.last_error == ""


def test_running_twice_does_not_double_count(ana):
    TrackedResource.objects.create(resource_id="r1")
    source = FakeSource({"r1": [ev("k1")]})
    sync_all(source, NOW, LAUNCH)
    second = sync_all(source, NOW, LAUNCH)
    assert second.new_events == 0
    assert balance(ana) == 20


def test_first_sync_starts_at_launch_then_uses_cursor(ana):
    TrackedResource.objects.create(resource_id="r1")
    source = FakeSource({})
    sync_all(source, NOW, LAUNCH)
    assert source.since["r1"] == LAUNCH
    sync_all(source, NOW, LAUNCH)
    assert source.since["r1"] == NOW


def test_failure_mid_resource_rolls_back_and_other_resources_continue(ana):
    TrackedResource.objects.create(resource_id="bad")
    TrackedResource.objects.create(resource_id="good")
    source = FakeSource({"bad": [ev("k1")], "good": [ev("k2")]}, fail={"bad"})

    result = sync_all(source, NOW, LAUNCH)

    assert (result.resources_ok, result.resources_failed) == (1, 1)
    assert balance(ana) == 20  # only k2
    bad = TrackedResource.objects.get(resource_id="bad")
    assert bad.cursor is None
    assert "503" in bad.last_error


def test_inactive_resources_are_skipped(ana):
    TrackedResource.objects.create(resource_id="r1", active=False)
    source = FakeSource({"r1": [ev("k1")]})
    assert sync_all(source, NOW, LAUNCH).resources_ok == 0
    assert balance(ana) == 0


def test_sync_command_requires_token(settings):
    settings.TRANSIFEX_API_TOKEN = ""
    with pytest.raises(CommandError, match="TRANSIFEX_API_TOKEN"):
        call_command("sync_transifex")


@responses.activate
def test_track_resources_command_adds_new_ids_once(settings):
    settings.TRANSIFEX_API_TOKEN = "tok"
    responses.get(
        "https://rest.api.transifex.com/resources",
        match=[matchers.query_param_matcher({"filter[project]": "o:wpilib:p:frc-docs"})],
        json={"data": [{"id": "r1"}, {"id": "r2"}], "links": {"next": None}},
    )
    call_command("track_resources")
    call_command("track_resources")
    assert sorted(TrackedResource.objects.values_list("resource_id", flat=True)) == ["r1", "r2"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL with `ImportError: cannot import name 'TrackedResource'`

- [ ] **Step 3: Implement model, sync and admin**

`transifex/models.py`:
```python
from django.db import models


class TrackedResource(models.Model):
    resource_id = models.CharField(max_length=300, unique=True)
    active = models.BooleanField(default=True)
    cursor = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["resource_id"]
        verbose_name = "recurso monitorado"
        verbose_name_plural = "recursos monitorados"

    def __str__(self):
        return self.resource_id
```

`transifex/sync.py`:
```python
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from ledger.services import record_progress

from .client import TransifexError
from .models import TrackedResource
from .source import ProgressSource


@dataclass
class SyncResult:
    resources_ok: int = 0
    resources_failed: int = 0
    new_events: int = 0


def sync_resource(tracked: TrackedResource, source: ProgressSource, now: datetime, launch_at: datetime) -> int:
    since = tracked.cursor or launch_at
    with transaction.atomic():
        new = sum(1 for event in source.events_for(tracked.resource_id, since) if record_progress(event))
        tracked.cursor = now
        tracked.last_synced_at = now
        tracked.last_error = ""
        tracked.save()
    return new


def sync_all(source: ProgressSource, now: datetime, launch_at: datetime) -> SyncResult:
    result = SyncResult()
    for tracked in TrackedResource.objects.filter(active=True):
        try:
            result.new_events += sync_resource(tracked, source, now, launch_at)
            result.resources_ok += 1
        except TransifexError as exc:
            # The atomic block rolled back points and cursor; only record the error.
            TrackedResource.objects.filter(pk=tracked.pk).update(last_error=str(exc)[:2000])
            result.resources_failed += 1
    return result
```

`transifex/admin.py`:
```python
from django.contrib import admin

from .models import TrackedResource


@admin.register(TrackedResource)
class TrackedResourceAdmin(admin.ModelAdmin):
    list_display = ["resource_id", "active", "last_synced_at", "cursor", "last_error"]
    list_filter = ["active"]
    search_fields = ["resource_id"]
    readonly_fields = ["cursor", "last_synced_at", "last_error"]
```

Run: `uv run python manage.py makemigrations transifex`

- [ ] **Step 4: Implement commands**

```bash
mkdir -p transifex/management/commands
touch transifex/management/__init__.py transifex/management/commands/__init__.py
```

`transifex/management/commands/sync_transifex.py`:
```python
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from transifex.client import TransifexClient
from transifex.source import ResourceTranslationsSource
from transifex.sync import sync_all


class Command(BaseCommand):
    help = "Busca traduções e revisões novas no Transifex e credita pontos. Rode via cron a cada 30–60 min."

    def handle(self, *args, **options):
        if not settings.TRANSIFEX_API_TOKEN:
            raise CommandError("TRANSIFEX_API_TOKEN não configurado no .env.")
        client = TransifexClient(settings.TRANSIFEX_API_TOKEN)
        source = ResourceTranslationsSource(client, settings.TRANSIFEX_LANGUAGE, settings.LAUNCH_AT)
        result = sync_all(source, timezone.now(), settings.LAUNCH_AT)
        summary = f"{result.resources_ok} recursos ok, {result.resources_failed} com erro, {result.new_events} eventos novos"
        if result.resources_failed:
            raise CommandError(summary + " (veja last_error no admin)")
        self.stdout.write(self.style.SUCCESS(summary))
```

`transifex/management/commands/track_resources.py`:
```python
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from transifex.client import TransifexClient
from transifex.models import TrackedResource


class Command(BaseCommand):
    help = "Cadastra todos os recursos de um projeto do Transifex como monitorados (não altera os existentes)."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=settings.TRANSIFEX_PROJECT)

    def handle(self, *args, project, **options):
        if not settings.TRANSIFEX_API_TOKEN:
            raise CommandError("TRANSIFEX_API_TOKEN não configurado no .env.")
        client = TransifexClient(settings.TRANSIFEX_API_TOKEN)
        added = sum(
            TrackedResource.objects.get_or_create(resource_id=rid)[1] for rid in client.iter_resources(project)
        )
        self.stdout.write(self.style.SUCCESS(f"{added} recursos novos cadastrados."))
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest -v`
Expected: PASS (all)

- [ ] **Step 6: Live check against the real API (reads only, uses `.env` token)**

```bash
uv run python manage.py migrate
uv run python manage.py track_resources
uv run python manage.py sync_transifex
```
Expected: `track_resources` reports 150+ new resources. `sync_transifex` prints `N recursos ok, 0 com erro, 0 eventos novos`, with 0 events because `LAUNCH_AT` is in the future. To see real events, run once with `LAUNCH_AT=2026-08-01T00:00:00Z uv run python manage.py sync_transifex` against a throwaway DB (`DATABASE_URL=sqlite:////tmp/tx-check.sqlite3`, after migrating it) and confirm `UnclaimedEvent` rows appear. Don't commit that DB, and delete it afterwards.

- [ ] **Step 7: Commit**

```bash
git add -A
git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'   # must print nothing
git commit -m "feat: sync Transifex progress into the ledger with per-resource cursors"
```

---

### Task 8: Account page with link form and points history

**Files:**
- Create: `accounts/forms.py`, `accounts/templates/accounts/account.html`, `tests/test_account_view.py`
- Modify: `accounts/views.py`, `accounts/urls.py`, `accounts/templates/accounts/home.html`, `templates/base.html`

**Interfaces:**
- Consumes: `get_profile`, `request_link`, `LinkError` (Task 2); `balance`, `PointEntry` (Task 3).
- Produces: URL name `account` at `/conta/`.

- [ ] **Step 1: Write the failing tests**

`tests/test_account_view.py`:
```python
import pytest

from accounts.models import Profile
from accounts.services import approve_link, get_profile, request_link
from ledger.models import PointEntry

pytestmark = pytest.mark.django_db


def test_anonymous_is_redirected_to_login(client):
    response = client.get("/conta/")
    assert response.status_code == 302
    assert "/contas/login/" in response["Location"]


def test_shows_balance_and_history(client, django_user_model):
    user = django_user_model.objects.create_user("ana")
    approve_link(request_link(user, "alice"))
    PointEntry.objects.create(user=user, amount=42, kind=PointEntry.Kind.ADJUSTMENT, note="Bônus de boas-vindas")
    client.force_login(user)

    html = client.get("/conta/").content.decode()

    assert "42 pontos" in html
    assert "Bônus de boas-vindas" in html
    assert "alice" in html


def test_post_requests_link(client, django_user_model):
    user = django_user_model.objects.create_user("ana")
    client.force_login(user)

    response = client.post("/conta/", {"transifex_username": "u:alice"})

    assert response.status_code == 302
    profile = get_profile(user)
    assert (profile.transifex_username, profile.link_status) == ("alice", Profile.LinkStatus.PENDING)


def test_post_shows_error_for_taken_username(client, django_user_model):
    owner = django_user_model.objects.create_user("owner")
    approve_link(request_link(owner, "alice"))
    user = django_user_model.objects.create_user("ana")
    client.force_login(user)

    response = client.post("/conta/", {"transifex_username": "alice"})

    assert response.status_code == 200
    assert "já está vinculado a outra conta" in response.content.decode()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_account_view.py -v`
Expected: FAIL (404 on `/conta/`)

- [ ] **Step 3: Implement**

`accounts/forms.py`:
```python
from django import forms


class LinkForm(forms.Form):
    transifex_username = forms.CharField(
        label="Seu usuário no Transifex",
        max_length=200,
        help_text="Ex.: joaosilva (o nome que aparece no seu perfil do Transifex).",
    )
```

`accounts/views.py`, replaced entirely:
```python
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from ledger.services import balance

from .forms import LinkForm
from .models import Profile
from .services import LinkError, get_profile, request_link


def home(request):
    return render(request, "accounts/home.html")


@login_required
def account(request):
    profile = get_profile(request.user)
    form = LinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request_link(request.user, form.cleaned_data["transifex_username"])
        except LinkError as exc:
            form.add_error("transifex_username", str(exc))
        else:
            messages.success(request, "Pedido de vínculo enviado. Um admin vai aprovar em breve.")
            return redirect("account")
    return render(
        request,
        "accounts/account.html",
        {
            "profile": profile,
            "can_request": profile.link_status in (Profile.LinkStatus.NONE, Profile.LinkStatus.REJECTED),
            "form": form,
            "balance": balance(request.user),
            "entries": request.user.point_entries.all()[:50],
        },
    )
```

`accounts/urls.py`:
```python
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("conta/", views.account, name="account"),
]
```

`accounts/templates/accounts/account.html`:
```html
{% extends "base.html" %}
{% block title %}Minha conta — Loja de Traduções WPILib{% endblock %}
{% block content %}
  <h1>Minha conta</h1>
  <p><strong>{{ balance }} pontos</strong> disponíveis</p>

  <h2>Transifex</h2>
  {% if profile.link_status == "approved" %}
    <p>Vinculado a <strong>{{ profile.transifex_username }}</strong>.</p>
  {% elif profile.link_status == "pending" %}
    <p>Pedido de vínculo com <strong>{{ profile.transifex_username }}</strong> aguardando aprovação.</p>
  {% endif %}
  {% if can_request %}
    {% if profile.link_status == "rejected" %}<p>Seu pedido anterior foi recusado. Confira o nome e tente de novo.</p>{% endif %}
    <form method="post">
      {% csrf_token %}
      {{ form.as_p }}
      <button type="submit">Pedir vínculo</button>
    </form>
  {% endif %}

  <h2>Histórico</h2>
  {% if entries %}
    <table>
      <thead><tr><th>Data</th><th>Tipo</th><th>Palavras</th><th>Pontos</th><th>Obs.</th></tr></thead>
      <tbody>
      {% for entry in entries %}
        <tr>
          <td>{{ entry.occurred_at|default:entry.created_at|date:"d/m/Y H:i" }}</td>
          <td>{{ entry.get_kind_display }}</td>
          <td>{{ entry.words|default:"" }}</td>
          <td>{{ entry.amount }}</td>
          <td>{{ entry.note }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p>Nenhum ponto ainda. Traduções e revisões feitas no Transifex depois do lançamento aparecem aqui.</p>
  {% endif %}
{% endblock %}
```

In `templates/base.html`, replace `{% block account_link %}{% endblock %}` with:
```html
<a href="{% url 'account' %}">Minha conta</a>
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add account page with Transifex link form and points history"
```

---

### Task 9: Docs and wrap-up

**Files:**
- Modify: `CLAUDE.md`
- Create: `README.md`

- [ ] **Step 1: Add a Commands section to `CLAUDE.md` (after "Project status") and update status**

Replace the "Project status" paragraph with:
```markdown
## Project status

Plan 1 (earning points: accounts, ledger, Transifex sync) is implemented. Plan 2 (catalog, redemptions, leaderboard, deploy) is not built yet. Design: `docs/superpowers/specs/2026-10-07-translation-store-design.md`. Plans: `docs/superpowers/plans/`.

## Commands

- Install: `uv sync`
- Run tests: `uv run pytest` · one test: `uv run pytest tests/test_ledger.py::test_same_event_twice_is_ignored -v`
- Dev server: `uv run python manage.py migrate && uv run python manage.py runserver`
- Admin user: `uv run python manage.py createsuperuser`
- Track all frc-docs resources: `uv run python manage.py track_resources`
- Sync points (cron, every 30–60 min): `uv run python manage.py sync_transifex` (exits non-zero if any resource failed)
```

- [ ] **Step 2: Write `README.md`**

```markdown
# Loja de Traduções WPILib (pt-BR)

Loja de recompensas para quem traduz a documentação da WPILib (`frc-docs`) para o português no Transifex. Traduções e revisões feitas depois do lançamento viram pontos, que podem ser trocados por brindes.

## Rodando localmente

1. Instale o [uv](https://docs.astral.sh/uv/).
2. `cp .env.example .env` e preencha:
   - `TRANSIFEX_API_TOKEN`: crie em https://app.transifex.com/user/settings/api/
   - `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`: crie um OAuth App em https://github.com/settings/developers com callback `http://localhost:8000/contas/github/login/callback/`
3. `uv sync && uv run python manage.py migrate && uv run python manage.py createsuperuser`
4. `uv run python manage.py track_resources` para cadastrar os recursos do frc-docs
5. `uv run python manage.py runserver` e acesse http://localhost:8000

## Como os pontos funcionam

- 2 pontos por palavra traduzida e 1 por palavra revisada (ajustável no admin em "Taxas de pontos").
- Cada string conta uma vez para tradução e uma vez para revisão; retraduzir não gera pontos novos.
- Só conta o que foi feito a partir de `LAUNCH_AT`.
- Quem traduziu antes de vincular a conta não perde nada: os pontos ficam guardados e são creditados quando o admin aprova o vínculo.

Nunca commite o `.env`: este repositório é público.
```

- [ ] **Step 3: Full test run and secret scan, then commit**

```bash
uv run pytest -v
git add -A
git diff --cached | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'   # must print nothing
git commit -m "docs: add README and development commands"
```
