import pytest
from django.contrib.auth.models import Permission

from ledger.models import PointEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_logged(client, django_user_model):
    admin = django_user_model.objects.create_superuser("boss", "boss@example.com", "x")
    client.force_login(admin)
    return client, admin


def test_adjustment_requires_a_note(admin_client_logged, django_user_model):
    client, _ = admin_client_logged
    target = django_user_model.objects.create_user("ana")
    response = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": 50, "note": ""})
    assert response.status_code == 200
    assert not PointEntry.objects.exists()


def test_adjustment_with_note_is_saved_as_adjustment(admin_client_logged, django_user_model):
    client, _ = admin_client_logged
    target = django_user_model.objects.create_user("ana")
    response = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": 50, "note": "Bônus"})
    assert response.status_code == 302
    entry = PointEntry.objects.get()
    assert (entry.kind, entry.amount, entry.note) == (PointEntry.Kind.ADJUSTMENT, 50, "Bônus")


def test_staff_without_ledger_permissions_cannot_see_point_entries(client, django_user_model):
    staff = django_user_model.objects.create_user("staff", is_staff=True)
    client.force_login(staff)
    assert client.get("/admin/ledger/pointentry/").status_code == 403


def test_staff_with_view_permission_can_list_point_entries(client, django_user_model):
    staff = django_user_model.objects.create_user("staff", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_pointentry"))
    client.force_login(staff)
    assert client.get("/admin/ledger/pointentry/").status_code == 200


def test_admin_index_uses_portuguese_names(admin_client_logged):
    client, _ = admin_client_logged
    html = client.get("/admin/").content.decode()
    for label in ["Perfis", "Lançamentos de pontos", "Eventos pendentes", "Tradutores", "Pontos"]:
        assert label in html
    assert "Point entrys" not in html


def test_negative_adjustment_cannot_make_balance_negative(admin_client_logged, django_user_model):
    from ledger.services import balance

    client, _ = admin_client_logged
    target = django_user_model.objects.create_user("ana")
    PointEntry.objects.create(user=target, amount=30, kind=PointEntry.Kind.ADJUSTMENT, note="x")
    response = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": -50, "note": "Correção"})
    assert response.status_code == 200
    assert "O ajuste deixaria o saldo negativo (saldo atual: 30)." in response.content.decode()
    assert balance(target) == 30

    ok = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": -30, "note": "Correção"})
    assert ok.status_code == 302 and balance(target) == 0
