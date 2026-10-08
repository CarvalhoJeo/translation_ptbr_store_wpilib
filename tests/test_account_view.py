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
