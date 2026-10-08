import pytest
from django.contrib.auth.models import Permission
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core import mail

from ledger.services import balance
from shop import notify, services
from shop.models import Redemption
from tests.factories import MAIL_ADDRESS, give_points, make_product, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def boss(django_user_model):
    return django_user_model.objects.create_superuser("boss", "boss@example.com", "x")


@pytest.fixture
def boss_client(client, boss):
    client.force_login(boss)
    return client


@pytest.fixture
def ana(django_user_model):
    user = make_user(django_user_model)
    give_points(user, 1000)
    return user


def new(ana, delivery="mail", name="Camiseta"):
    variant = make_product(name=name).variants.get()
    return services.request_redemption(ana, variant.pk, delivery, MAIL_ADDRESS if delivery == "mail" else None)


def act(client, action, ids, **extra):
    data = {"action": action, ACTION_CHECKBOX_NAME: [str(i) for i in ids], **extra}
    return client.post("/admin/shop/redemption/", data, follow=True)


def test_approve_action_advances_and_emails(boss_client, ana, django_capture_on_commit_callbacks):
    r = new(ana)
    with django_capture_on_commit_callbacks(execute=True):
        response = act(boss_client, "approve_selected", [r.pk])
    assert Redemption.objects.get(pk=r.pk).status == "approved"
    assert "1 resgate(s) aprovado(s)" in response.content.decode()
    assert any("aprovado" in m.subject for m in mail.outbox)


def test_reject_shows_form_then_refunds(boss_client, ana):
    r = new(ana)
    first = act(boss_client, "reject_selected", [r.pk])
    assert "Motivo da recusa" in first.content.decode()
    assert Redemption.objects.get(pk=r.pk).status == "requested"

    act(boss_client, "reject_selected", [r.pk], apply="1", text="Sem estoque real")
    r.refresh_from_db()
    assert r.status == "rejected" and r.admin_note == "Sem estoque real"
    assert balance(ana) == 1000


def test_reject_without_text_keeps_form(boss_client, ana):
    r = new(ana)
    response = act(boss_client, "reject_selected", [r.pk], apply="1", text="")
    assert "Motivo da recusa" in response.content.decode()
    assert Redemption.objects.get(pk=r.pk).status == "requested"


def test_ship_with_tracking(boss_client, ana, boss):
    r = new(ana)
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    act(boss_client, "ship_selected", [r.pk], apply="1", text="aa123456789br")
    r.refresh_from_db()
    assert (r.status, r.tracking_code) == ("shipped", "AA123456789BR")


def test_ship_refuses_selection_spanning_several_users(boss_client, ana, boss, django_user_model):
    bia = make_user(django_user_model, username="bia", tx="bia", email="bia@example.com")
    give_points(bia, 1000)
    a, b = new(ana, name="A"), new(bia, name="B")
    for r in (a, b):
        services.approve(r, boss)
        services.mark_shipping_paid(r, boss)

    response = act(boss_client, "ship_selected", [a.pk, b.pk], apply="1", text="BR1")

    assert "Selecione resgates de um único tradutor" in response.content.decode()
    assert {Redemption.objects.get(pk=r.pk).status for r in (a, b)} == {"shipping_paid"}


def test_mixed_selection_reports_per_row_errors(boss_client, ana, boss):
    paid = new(ana, name="A")
    services.approve(paid, boss)
    services.mark_shipping_paid(paid, boss)
    still_requested = new(ana, name="B")

    response = act(boss_client, "ship_selected", [paid.pk, still_requested.pk], apply="1", text="BR1")
    html = response.content.decode()
    assert Redemption.objects.get(pk=paid.pk).status == "shipped"
    assert Redemption.objects.get(pk=still_requested.pk).status == "requested"
    assert f"#{still_requested.pk}:" in html


def test_pickup_ready_then_delivered(boss_client, ana, boss):
    r = new(ana, delivery="pickup")
    services.approve(r, boss)
    act(boss_client, "ready_selected", [r.pk], apply="1", text="Regional SP")
    act(boss_client, "deliver_selected", [r.pk])
    r.refresh_from_db()
    assert r.status == "delivered" and r.admin_note == "Regional SP"


def test_approve_commits_even_if_email_fails(boss_client, ana, monkeypatch, django_capture_on_commit_callbacks):
    r = new(ana)
    monkeypatch.setattr(notify, "send_mail", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    with django_capture_on_commit_callbacks(execute=True):
        act(boss_client, "approve_selected", [r.pk])
    r.refresh_from_db()
    assert r.status == "approved"
    assert r.events.filter(note__startswith="falha ao enviar e-mail").exists()


def test_change_page_shows_history_and_is_read_only(boss_client, ana):
    r = new(ana)
    response = boss_client.get(f"/admin/shop/redemption/{r.pk}/change/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Histórico" in html
    assert 'name="cost"' not in html


def test_admin_cannot_add_or_delete_redemptions(boss_client):
    assert boss_client.get("/admin/shop/redemption/add/").status_code == 403


def test_view_only_staff_cannot_run_actions(client, ana, django_user_model):
    r = new(ana)
    viewer = django_user_model.objects.create_user("viewer", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_redemption"))
    client.force_login(viewer)
    act(client, "approve_selected", [r.pk])
    assert Redemption.objects.get(pk=r.pk).status == "requested"
    page = client.get("/admin/shop/redemption/")
    assert page.status_code == 200
    assert 'value="approve_selected"' not in page.content.decode()


def test_detail_page_has_no_save_button(boss_client, ana):
    r = new(ana)
    html = boss_client.get(f"/admin/shop/redemption/{r.pk}/change/").content.decode()
    assert 'name="_save"' not in html
