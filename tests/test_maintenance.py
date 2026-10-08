from datetime import datetime, timedelta, timezone

import pytest

from ledger.models import PointEntry, UnclaimedEvent
from ledger.services import claim_unclaimed
from shop.maintenance import erase_old_addresses
from shop.models import Redemption
from tests.factories import make_product, make_redemption, make_user

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)


def test_erases_addresses_delivered_more_than_30_days_ago(django_user_model):
    ana = make_user(django_user_model)
    old = make_redemption(ana, make_product(name="A"), status="delivered", delivered_at=NOW - timedelta(days=31), tracking_code="BR1")
    recent = make_redemption(ana, make_product(name="B"), status="delivered", delivered_at=NOW - timedelta(days=29))
    open_one = make_redemption(ana, make_product(name="C"), status="shipped")

    assert erase_old_addresses(NOW) == 1

    old.refresh_from_db()
    assert all(getattr(old, f) == "" for f in Redemption.ADDRESS_FIELDS)
    assert old.address_erased_at == NOW and old.tracking_code == "BR1"
    assert Redemption.objects.get(pk=recent.pk).full_name == "Ana Silva"
    assert Redemption.objects.get(pk=open_one.pk).full_name == "Ana Silva"
    assert erase_old_addresses(NOW) == 0


def test_claim_skips_events_already_credited(django_user_model):
    ana = make_user(django_user_model, approved=False)
    when = datetime(2026, 11, 2, tzinfo=timezone.utc)
    PointEntry.objects.create(user=ana, amount=20, kind=PointEntry.Kind.TRANSLATED, string_key="dup", words=10)
    UnclaimedEvent.objects.create(tx_username="alice", kind=PointEntry.Kind.TRANSLATED, string_key="dup", words=10, amount=20, occurred_at=when)
    UnclaimedEvent.objects.create(tx_username="alice", kind=PointEntry.Kind.TRANSLATED, string_key="new", words=5, amount=10, occurred_at=when)

    assert claim_unclaimed(ana, "alice") == 1
    assert PointEntry.objects.filter(string_key="dup").count() == 1
    assert not UnclaimedEvent.objects.exists()
