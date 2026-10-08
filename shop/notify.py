import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from .models import Redemption, RedemptionEvent, StoreSettings

logger = logging.getLogger(__name__)


def after_commit(func, redemption_id: int) -> None:
    """Send only if the surrounding transaction commits."""
    transaction.on_commit(lambda: func(redemption_id))


def _load(redemption_id: int) -> Redemption:
    return Redemption.objects.select_related("user", "variant__product").get(pk=redemption_id)


def _send(redemption: Redemption, template: str, recipients, subject: str, extra: dict | None = None) -> None:
    recipients = [address for address in recipients if address]
    if not recipients:
        return
    try:
        body = render_to_string(f"emails/{template}.txt", {"r": redemption, "site_url": settings.SITE_URL, **(extra or {})})
        send_mail(subject, body, None, recipients)
    except Exception as exc:  # e-mail must never undo a redemption step
        logger.exception("falha ao enviar e-mail %s do resgate %s", template, redemption.pk)
        try:
            RedemptionEvent.objects.create(
                redemption=redemption, note=f"falha ao enviar e-mail ({template}): {type(exc).__name__}: {exc}"[:1000]
            )
        except Exception:
            logger.exception("não foi possível registrar a falha de e-mail do resgate %s", redemption.pk)


def new_redemption(redemption_id: int) -> None:
    r = _load(redemption_id)
    staff = (
        get_user_model()
        .objects.filter(is_staff=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    # Fixed path (not reverse()): the Redemption admin is registered in a later task.
    admin_url = f"{settings.SITE_URL}/admin/shop/redemption/{r.pk}/change/"
    _send(r, "new_redemption", list(staff), f"Novo resgate #{r.pk}: {r.variant}", {"admin_url": admin_url})


def approved(redemption_id: int) -> None:
    r = _load(redemption_id)
    extra = {"pix_instructions": StoreSettings.current().pix_instructions}
    _send(r, "approved", [r.user.email], f"Seu resgate #{r.pk} foi aprovado", extra)


def shipped(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "shipped", [r.user.email], f"Seu resgate #{r.pk} foi enviado")


def ready_for_pickup(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "ready_for_pickup", [r.user.email], f"Seu resgate #{r.pk} está pronto para retirar")


def rejected(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "rejected", [r.user.email], f"Seu resgate #{r.pk} foi recusado")
