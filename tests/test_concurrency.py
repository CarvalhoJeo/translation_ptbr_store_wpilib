import threading
import uuid
from datetime import datetime, timezone

import pytest
from django.db import connection, transaction

from ledger.services import balance
from shop import services
from shop.models import Redemption, Variant
from tests.factories import give_points, make_product, make_user
from transifex.models import TrackedResource
from transifex.sync import ResourceBusy, sync_resource

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def require_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("row-lock behaviour needs PostgreSQL (set DATABASE_URL to a Postgres)")


def run_together(*callables):
    barrier = threading.Barrier(len(callables))
    outcomes = [None] * len(callables)

    def worker(index, fn):
        try:
            barrier.wait()
            outcomes[index] = ("ok", fn())
        except Exception as exc:
            outcomes[index] = ("error", exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(callables)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def test_last_unit_goes_to_exactly_one(django_user_model):
    ana = make_user(django_user_model, "ana", "alice", "a@example.com")
    bia = make_user(django_user_model, "bia", "bob", "b@example.com")
    give_points(ana, 500)
    give_points(bia, 500)
    variant = make_product(cost=100, variants=(("M", 1),)).variants.get()

    outcomes = run_together(
        lambda: services.request_redemption(ana, variant.pk, "pickup"),
        lambda: services.request_redemption(bia, variant.pk, "pickup"),
    )

    assert sorted(kind for kind, _ in outcomes) == ["error", "ok"]
    error = next(value for kind, value in outcomes if kind == "error")
    assert "Esgotou" in str(error)
    assert Variant.objects.get(pk=variant.pk).stock == 0
    assert Redemption.objects.count() == 1


def test_parallel_requests_cannot_overspend(django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 150)
    product_a = make_product(name="A", cost=100, variants=(("M", 5),)).variants.get()
    product_b = make_product(name="B", cost=100, variants=(("M", 5),)).variants.get()

    outcomes = run_together(
        lambda: services.request_redemption(ana, product_a.pk, "pickup"),
        lambda: services.request_redemption(ana, product_b.pk, "pickup"),
    )

    assert sorted(kind for kind, _ in outcomes) == ["error", "ok"]
    assert balance(ana) == 50


def test_concurrent_double_submit_with_same_token_creates_one(django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 500)
    variant = make_product(cost=100, variants=(("M", 5),)).variants.get()
    token = uuid.uuid4()

    outcomes = run_together(
        lambda: services.request_redemption(ana, variant.pk, "pickup", token=token),
        lambda: services.request_redemption(ana, variant.pk, "pickup", token=token),
    )

    assert [kind for kind, _ in outcomes] == ["ok", "ok"]
    assert outcomes[0][1].pk == outcomes[1][1].pk
    assert Redemption.objects.count() == 1
    assert balance(ana) == 400


def test_sync_skips_a_resource_locked_by_another_run():
    tracked = TrackedResource.objects.create(resource_id="r1")
    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with transaction.atomic():
            list(TrackedResource.objects.select_for_update().filter(pk=tracked.pk))
            locked.set()
            release.wait(10)
        connection.close()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    locked.wait(10)

    class EmptySource:
        def events_for(self, resource_id, since):
            return iter(())

    try:
        with pytest.raises(ResourceBusy):
            sync_resource(tracked, EmptySource(), datetime(2026, 11, 10, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))
    finally:
        release.set()
        holder.join(10)
