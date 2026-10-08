from django.conf import settings


def test_github_login_requests_email_scope():
    assert settings.SOCIALACCOUNT_PROVIDERS["github"]["SCOPE"] == ["read:user", "user:email"]
