from django.conf import settings


def test_github_login_requests_email_scope():
    assert settings.SOCIALACCOUNT_PROVIDERS["github"]["SCOPE"] == ["read:user", "user:email"]


def test_github_email_is_actually_queried():
    from allauth.socialaccount import app_settings

    assert app_settings.QUERY_EMAIL is True
