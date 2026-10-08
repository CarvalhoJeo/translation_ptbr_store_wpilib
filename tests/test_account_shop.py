from datetime import datetime, timezone

import pytest

from accounts.models import Profile
from accounts.services import get_profile, request_link
from ledger.models import PointEntry, UnclaimedEvent
from shop import services
from tests.factories import give_points, make_product, make_user

pytestmark = pytest.mark.django_db


def test_lists_my_redemptions_with_cancel_only_when_requested(client, django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 500)
    boss = django_user_model.objects.create_user("boss", is_staff=True)
    open_r = services.request_redemption(ana, make_product(name="Caneca").variants.get().pk, "pickup")
    done_r = services.request_redemption(ana, make_product(name="Boné").variants.get().pk, "pickup")
    services.approve(done_r, boss)
    client.force_login(ana)
    html = client.get("/conta/").content.decode()
    assert "Meus resgates" in html and "Caneca" in html and "Boné" in html
    assert f"/loja/resgates/{open_r.pk}/cancelar/" in html
    assert f"/loja/resgates/{done_r.pk}/cancelar/" not in html
    assert "Aprovado" in html


def test_shows_tracking_link(client, django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 500)
    boss = django_user_model.objects.create_user("boss", is_staff=True)
    r = services.request_redemption(
        ana,
        make_product().variants.get().pk,
        "mail",
        {"full_name": "Ana Silva", "cep": "01001-000", "street": "R", "number": "1", "district": "D",
         "city": "São Paulo", "uf": "SP", "phone": "11"},
    )
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    services.mark_shipped(r, boss, "AA123456789BR")
    client.force_login(ana)
    assert "AA123456789BR" in client.get("/conta/").content.decode()


def test_update_email(client, django_user_model):
    ana = make_user(django_user_model, email="")
    client.force_login(ana)
    response = client.post("/conta/", {"form": "email", "email": "ana.nova@example.com"})
    assert response.status_code == 302
    ana.refresh_from_db()
    assert ana.email == "ana.nova@example.com"


def test_invalid_email_is_rejected(client, django_user_model):
    ana = make_user(django_user_model)
    client.force_login(ana)
    response = client.post("/conta/", {"form": "email", "email": "not-an-email"})
    assert response.status_code == 200
    ana.refresh_from_db()
    assert ana.email == "ana@example.com"


def test_pending_user_sees_parked_points_and_can_fix_username(client, django_user_model):
    user = make_user(django_user_model, approved=False)
    request_link(user, "alise")
    UnclaimedEvent.objects.create(
        tx_username="alise", kind=PointEntry.Kind.TRANSLATED, string_key="k1", words=10, amount=20,
        occurred_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    client.force_login(user)
    html = client.get("/conta/").content.decode()
    assert "20 pontos guardados" in html
    assert 'value="alise"' in html

    client.post("/conta/", {"form": "link", "transifex_username": "alice"})
    profile = get_profile(user)
    assert (profile.transifex_username, profile.link_status) == ("alice", Profile.LinkStatus.PENDING)
