import os
import subprocess
import sys

import pytest

SNIPPET = (
    "import django, json; from django.conf import settings; django.setup(); "
    "print(json.dumps({'hosts': settings.ALLOWED_HOSTS, 'csrf': settings.CSRF_TRUSTED_ORIGINS, "
    "'secure': settings.SESSION_COOKIE_SECURE}))"
)


def load_settings(**env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "DJANGO_"))}
    base.update(DJANGO_SETTINGS_MODULE="store.settings", **env)
    return subprocess.run([sys.executable, "-c", SNIPPET], env=base, capture_output=True, text=True)


def test_on_vercel_a_missing_secret_key_refuses_to_start():
    result = load_settings(VERCEL="1")
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_on_vercel_trusts_its_own_urls_and_secures_cookies():
    import json

    result = load_settings(
        VERCEL="1",
        DJANGO_SECRET_KEY="x" * 50,
        VERCEL_URL="store-abc123.vercel.app",
        VERCEL_PROJECT_PRODUCTION_URL="store.vercel.app",
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert {"store-abc123.vercel.app", "store.vercel.app"} <= set(loaded["hosts"])
    assert "https://store.vercel.app" in loaded["csrf"]
    assert loaded["secure"] is True


@pytest.mark.parametrize("env", [{}, {"DJANGO_SECRET_KEY": ""}])
def test_locally_the_dev_fallback_key_still_works(env):
    assert load_settings(**env).returncode == 0
