import pytest
from django.core import mail

from shop import notify
from shop.models import RedemptionEvent, StoreSettings
from tests.factories import make_product, make_redemption, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(django_user_model):
    return make_user(django_user_model)


def test_new_redemption_goes_to_staff_with_email(ana, django_user_model):
    django_user_model.objects.create_user("boss", email="boss@example.com", is_staff=True)
    django_user_model.objects.create_user("silent", email="", is_staff=True)
    r = make_redemption(ana, make_product(name="Caneca"))
    notify.new_redemption(r.pk)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["boss@example.com"]
    assert "Caneca" in mail.outbox[0].subject
    assert f"/admin/shop/redemption/{r.pk}/change/" in mail.outbox[0].body


def test_approved_mail_includes_pix_instructions(ana):
    settings_row = StoreSettings.current()
    settings_row.pix_instructions = "Pix: frete@example.com — R$ 25"
    settings_row.save()
    r = make_redemption(ana, make_product(), delivery="mail", status="approved")
    notify.approved(r.pk)
    assert mail.outbox[0].to == ["ana@example.com"]
    assert "Pix: frete@example.com" in mail.outbox[0].body


def test_approved_pickup_mail_talks_about_combining(ana):
    r = make_redemption(ana, make_product(), delivery="pickup", status="approved")
    notify.approved(r.pk)
    assert "combinar" in mail.outbox[0].body
    assert "Pix" not in mail.outbox[0].body


def test_shipped_mail_has_tracking(ana):
    r = make_redemption(ana, make_product(), status="shipped", tracking_code="AA123456789BR")
    notify.shipped(r.pk)
    assert "AA123456789BR" in mail.outbox[0].body


def test_ready_and_rejected_mails_include_admin_note(ana):
    r = make_redemption(ana, make_product(), delivery="pickup", status="ready_for_pickup", admin_note="Regional SP")
    notify.ready_for_pickup(r.pk)
    r2 = make_redemption(ana, make_product(name="B"), status="rejected", admin_note="Sem estoque real")
    notify.rejected(r2.pk)
    assert "Regional SP" in mail.outbox[0].body
    assert "Sem estoque real" in mail.outbox[1].body
    assert "devolvidos" in mail.outbox[1].body


def test_user_without_email_gets_nothing(django_user_model):
    user = make_user(django_user_model, email="")
    notify.approved(make_redemption(user, make_product(), status="approved").pk)
    assert mail.outbox == []


def test_send_failure_is_recorded_not_raised(ana, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(notify, "send_mail", boom)
    r = make_redemption(ana, make_product(), status="approved")
    notify.approved(r.pk)
    event = RedemptionEvent.objects.get(redemption=r)
    assert event.note.startswith("falha ao enviar e-mail")
    assert "smtp down" in event.note


def test_after_commit_defers_until_commit(ana, django_capture_on_commit_callbacks):
    r = make_redemption(ana, make_product(), status="approved")
    with django_capture_on_commit_callbacks(execute=True):
        notify.after_commit(notify.approved, r.pk)
        assert mail.outbox == []
    assert len(mail.outbox) == 1


def test_plain_text_mail_is_not_html_escaped(ana):
    settings_row = StoreSettings.current()
    settings_row.pix_instructions = "Chave: a&b <frete> 'R$ 25'"
    settings_row.save()
    r = make_redemption(ana, make_product(), delivery="mail", status="approved")
    notify.approved(r.pk)
    assert "Chave: a&b <frete> 'R$ 25'" in mail.outbox[0].body
    assert "&amp;" not in mail.outbox[0].body


def test_failure_while_recording_failure_does_not_raise(ana, monkeypatch):
    monkeypatch.setattr(notify, "send_mail", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    monkeypatch.setattr(notify.RedemptionEvent.objects, "create", lambda **k: (_ for _ in ()).throw(RuntimeError("db down")))
    r = make_redemption(ana, make_product(), status="approved")
    notify.approved(r.pk)  # must not raise


def test_no_recipients_records_a_note(ana):
    r = make_redemption(ana, make_product(name="Caneca"))
    notify.new_redemption(r.pk)
    assert len(mail.outbox) == 0
    note = RedemptionEvent.objects.get(redemption=r).note
    assert "e-mail não enviado (new_redemption)" in note and "nenhum destinatário" in note


def test_notification_errors_never_propagate(ana, monkeypatch):
    def boom(_):
        raise RuntimeError("db down")

    monkeypatch.setattr(notify, "_load", boom)
    notify.approved(1)
    notify.new_redemption(1)
    notify.shipped(1)
    notify.ready_for_pickup(1)
    notify.rejected(1)
