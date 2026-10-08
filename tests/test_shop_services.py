import uuid

import pytest
from django.core import mail

from ledger.models import PointEntry
from ledger.services import balance
from shop import services
from shop.models import Redemption, RedemptionEvent, Variant
from shop.services import RedemptionError
from tests.factories import MAIL_ADDRESS, give_points, make_product, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(django_user_model):
    user = make_user(django_user_model)
    give_points(user, 500)
    return user


@pytest.fixture
def boss(django_user_model):
    return django_user_model.objects.create_user("boss", email="boss@example.com", is_staff=True)


def variant_of(product):
    return product.variants.get()


def test_request_by_mail_debits_points_and_stock(ana, boss, django_capture_on_commit_callbacks):
    product = make_product(cost=120, variants=(("M", 2),))
    with django_capture_on_commit_callbacks(execute=True):
        r = services.request_redemption(ana, variant_of(product).pk, "mail", MAIL_ADDRESS)
    assert r.status == "requested" and r.cost == 120 and r.city == "São Paulo"
    assert balance(ana) == 380
    assert Variant.objects.get(pk=r.variant_id).stock == 1
    assert r.ledger_entry.kind == PointEntry.Kind.REDEMPTION and r.ledger_entry.amount == -120
    assert r.events.get().status_to == "requested"
    assert mail.outbox[0].to == ["boss@example.com"]


def test_request_pickup_needs_no_address(ana):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    assert r.delivery == "pickup" and r.full_name == ""


@pytest.mark.parametrize(
    "setup, delivery, address, message",
    [
        ("unlinked", "pickup", None, "Vincule"),
        ("inactive_product", "pickup", None, "não está disponível"),
        ("inactive_variant", "pickup", None, "não está disponível"),
        ("pickup_only", "mail", MAIL_ADDRESS, "Forma de entrega"),
        ("ok", "mail", {**MAIL_ADDRESS, "cep": ""}, "endereço completo"),
        ("no_stock", "pickup", None, "Esgotou"),
        ("expensive", "pickup", None, "Saldo insuficiente"),
        ("ok", "drone", None, "Forma de entrega"),
    ],
)
def test_request_rejections_change_nothing(django_user_model, setup, delivery, address, message):
    user = make_user(django_user_model, approved=setup != "unlinked")
    give_points(user, 200)
    product = make_product(
        cost=1000 if setup == "expensive" else 100,
        variants=(("M", 0 if setup == "no_stock" else 3),),
        active=setup != "inactive_product",
        allows_mail=setup != "pickup_only",
    )
    variant = variant_of(product)
    if setup == "inactive_variant":
        Variant.objects.filter(pk=variant.pk).update(active=False)

    with pytest.raises(RedemptionError, match=message):
        services.request_redemption(user, variant.pk, delivery, address)

    assert balance(user) == 200
    assert Variant.objects.get(pk=variant.pk).stock == (0 if setup == "no_stock" else 3)
    assert not Redemption.objects.exists()


def test_complement_is_optional_and_cep_is_kept(ana):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", {**MAIL_ADDRESS, "complement": ""})
    assert r.cep == "01001-000"


def test_same_token_returns_the_same_redemption(ana):
    token = uuid.uuid4()
    variant = variant_of(make_product(cost=100, variants=(("M", 5),)))
    first = services.request_redemption(ana, variant.pk, "pickup", token=token)
    second = services.request_redemption(ana, variant.pk, "pickup", token=token)
    assert first.pk == second.pk
    assert balance(ana) == 400
    assert Variant.objects.get(pk=variant.pk).stock == 4


def test_full_mail_flow(ana, boss, django_capture_on_commit_callbacks):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", MAIL_ADDRESS)
    with django_capture_on_commit_callbacks(execute=True):
        services.approve(r, boss)
        services.mark_shipping_paid(r, boss)
        services.mark_shipped(r, boss, "AA123456789BR")
        r = services.mark_delivered(r, boss)
    assert r.status == "delivered" and r.delivered_at is not None and r.tracking_code == "AA123456789BR"
    steps = list(r.events.values_list("status_from", "status_to"))
    assert steps == [
        ("", "requested"),
        ("requested", "approved"),
        ("approved", "shipping_paid"),
        ("shipping_paid", "shipped"),
        ("shipped", "delivered"),
    ]
    assert r.events.last().actor == boss
    subjects = [m.subject for m in mail.outbox]
    assert any("aprovado" in s for s in subjects) and any("enviado" in s for s in subjects)


def test_full_pickup_flow(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    services.approve(r, boss)
    services.mark_ready_for_pickup(r, boss, "Regional SP")
    r = services.mark_delivered(r, boss)
    assert r.status == "delivered" and r.admin_note == "Regional SP"


@pytest.mark.parametrize(
    "delivery, steps, bad",
    [
        ("mail", [], lambda r, b: services.mark_shipped(r, b, "X")),
        ("mail", ["approve"], lambda r, b: services.mark_ready_for_pickup(r, b, "x")),
        ("pickup", ["approve"], lambda r, b: services.mark_shipping_paid(r, b)),
        ("mail", ["approve"], lambda r, b: services.mark_delivered(r, b)),
        ("mail", ["approve"], lambda r, b: services.reject(r, b, "x")),
        ("mail", ["approve"], lambda r, b: services.approve(r, b)),
    ],
)
def test_disallowed_transitions(ana, boss, delivery, steps, bad):
    r = services.request_redemption(ana, variant_of(make_product()).pk, delivery, MAIL_ADDRESS if delivery == "mail" else None)
    for step in steps:
        getattr(services, step)(r, boss)
    before = Redemption.objects.get(pk=r.pk).status
    with pytest.raises(RedemptionError):
        bad(r, boss)
    assert Redemption.objects.get(pk=r.pk).status == before


def test_ship_requires_tracking_and_reject_requires_note(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", MAIL_ADDRESS)
    with pytest.raises(RedemptionError, match="motivo"):
        services.reject(r, boss, "  ")
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    with pytest.raises(RedemptionError, match="rastreio"):
        services.mark_shipped(r, boss, "")


def test_reject_refunds_frozen_cost_and_restores_stock(ana, boss, django_capture_on_commit_callbacks):
    product = make_product(cost=100, variants=(("M", 1),))
    r = services.request_redemption(ana, variant_of(product).pk, "pickup")
    product.cost = 999
    product.save()
    with django_capture_on_commit_callbacks(execute=True):
        r = services.reject(r, boss, "Item danificado")
    assert r.status == "rejected" and r.admin_note == "Item danificado"
    assert balance(ana) == 500
    assert variant_of(product).stock == 1
    refund = PointEntry.objects.get(kind=PointEntry.Kind.REFUND)
    assert refund.amount == 100
    assert any("recusado" in m.subject for m in mail.outbox)


def test_owner_can_cancel_only_while_requested(ana, boss, django_user_model):
    other = make_user(django_user_model, username="bia", tx="bob", email="bia@example.com")
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    with pytest.raises(RedemptionError):
        services.cancel(r, other)
    r = services.cancel(r, ana)
    assert r.status == "cancelled" and balance(ana) == 500

    r2 = services.request_redemption(ana, variant_of(make_product(name="B")).pk, "pickup")
    services.approve(r2, boss)
    with pytest.raises(RedemptionError):
        services.cancel(r2, ana)


def test_events_record_actor(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    services.approve(r, boss)
    assert RedemptionEvent.objects.filter(redemption=r, status_to="approved", actor=boss).exists()
