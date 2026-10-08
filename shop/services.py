import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Profile
from accounts.services import get_profile
from ledger.models import PointEntry
from ledger.services import balance

from . import notify
from .models import Redemption, RedemptionEvent, Variant

Status = Redemption.Status
Delivery = Redemption.Delivery


class RedemptionError(Exception):
    pass


def request_redemption(
    user, variant_id: int, delivery: str, address: dict | None = None, token: uuid.UUID | None = None
) -> Redemption:
    if token is not None:
        existing = Redemption.objects.filter(request_token=token, user=user).first()
        if existing:
            return existing
    if get_profile(user).link_status != Profile.LinkStatus.APPROVED:
        raise RedemptionError("Vincule sua conta do Transifex antes de resgatar.")

    with transaction.atomic():
        # Lock the user row so concurrent requests by the same person see each other's debits.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        try:
            variant = Variant.objects.select_for_update().select_related("product").get(pk=variant_id)
        except Variant.DoesNotExist:
            raise RedemptionError("Produto não encontrado.")
        product = variant.product
        if not (product.active and variant.active):
            raise RedemptionError("Este produto não está disponível.")
        if delivery not in dict(product.delivery_choices()):
            raise RedemptionError("Forma de entrega não disponível para este produto.")

        fields = {}
        if delivery == Delivery.MAIL:
            fields = {name: str((address or {}).get(name, "")).strip() for name in Redemption.ADDRESS_FIELDS}
            if any(not fields[name] for name in Redemption.REQUIRED_ADDRESS_FIELDS):
                raise RedemptionError("Preencha o endereço completo para envio pelos Correios.")
        if variant.stock < 1:
            raise RedemptionError("Esgotou enquanto você pedia.")
        if balance(user) < product.cost:
            raise RedemptionError("Saldo insuficiente.")

        Variant.objects.filter(pk=variant.pk).update(stock=F("stock") - 1)
        entry = PointEntry.objects.create(
            user=user, amount=-product.cost, kind=PointEntry.Kind.REDEMPTION, note=f"Resgate: {variant}"[:200]
        )
        redemption = Redemption.objects.create(
            user=user,
            variant=variant,
            cost=product.cost,
            delivery=delivery,
            ledger_entry=entry,
            request_token=token,
            **fields,
        )
        RedemptionEvent.objects.create(redemption=redemption, status_to=Status.REQUESTED, actor=user)
        notify.after_commit(notify.new_redemption, redemption.pk)
    return redemption


def _transition(
    redemption: Redemption,
    *,
    allowed_from: set[str],
    to: str,
    actor,
    note: str = "",
    only_delivery: str | None = None,
    updates: dict | None = None,
    refund: bool = False,
    owner=None,
) -> Redemption:
    with transaction.atomic():
        r = Redemption.objects.select_for_update().get(pk=redemption.pk)
        if owner is not None and r.user_id != owner.pk:
            raise RedemptionError("Este resgate não é seu.")
        if r.status not in allowed_from or (only_delivery and r.delivery != only_delivery):
            raise RedemptionError(f"Não é possível passar de “{r.get_status_display()}” para “{Status(to).label}”.")
        previous = r.status
        r.status = to
        for name, value in (updates or {}).items():
            setattr(r, name, value)
        r.save()
        if refund:
            PointEntry.objects.create(
                user_id=r.user_id, amount=r.cost, kind=PointEntry.Kind.REFUND, note=f"Estorno do resgate #{r.pk}"
            )
            Variant.objects.filter(pk=r.variant_id).update(stock=F("stock") + 1)
        RedemptionEvent.objects.create(redemption=r, status_from=previous, status_to=to, actor=actor, note=note)
    return r


def approve(redemption, actor) -> Redemption:
    r = _transition(redemption, allowed_from={Status.REQUESTED}, to=Status.APPROVED, actor=actor)
    notify.after_commit(notify.approved, r.pk)
    return r


def reject(redemption, actor, note: str) -> Redemption:
    note = (note or "").strip()
    if not note:
        raise RedemptionError("Informe o motivo da recusa.")
    r = _transition(
        redemption,
        allowed_from={Status.REQUESTED},
        to=Status.REJECTED,
        actor=actor,
        note=note,
        updates={"admin_note": note},
        refund=True,
    )
    notify.after_commit(notify.rejected, r.pk)
    return r


def cancel(redemption, user) -> Redemption:
    return _transition(
        redemption, allowed_from={Status.REQUESTED}, to=Status.CANCELLED, actor=user, refund=True, owner=user
    )


def mark_shipping_paid(redemption, actor) -> Redemption:
    return _transition(
        redemption, allowed_from={Status.APPROVED}, to=Status.SHIPPING_PAID, actor=actor, only_delivery=Delivery.MAIL
    )


def mark_shipped(redemption, actor, tracking_code: str) -> Redemption:
    tracking_code = (tracking_code or "").strip().upper()
    if not tracking_code:
        raise RedemptionError("Informe o código de rastreio.")
    r = _transition(
        redemption,
        allowed_from={Status.SHIPPING_PAID},
        to=Status.SHIPPED,
        actor=actor,
        note=tracking_code,
        updates={"tracking_code": tracking_code},
    )
    notify.after_commit(notify.shipped, r.pk)
    return r


def mark_ready_for_pickup(redemption, actor, note: str) -> Redemption:
    note = (note or "").strip()
    r = _transition(
        redemption,
        allowed_from={Status.APPROVED},
        to=Status.READY_FOR_PICKUP,
        actor=actor,
        note=note,
        only_delivery=Delivery.PICKUP,
        updates={"admin_note": note},
    )
    notify.after_commit(notify.ready_for_pickup, r.pk)
    return r


def mark_delivered(redemption, actor) -> Redemption:
    return _transition(
        redemption,
        allowed_from={Status.SHIPPED, Status.READY_FOR_PICKUP},
        to=Status.DELIVERED,
        actor=actor,
        updates={"delivered_at": timezone.now()},
    )
