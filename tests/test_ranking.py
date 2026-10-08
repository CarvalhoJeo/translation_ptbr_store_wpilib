from datetime import datetime, timezone

import pytest

from ledger.models import PointEntry
from ledger.services import leaderboard
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def add(user, amount, kind=PointEntry.Kind.ADJUSTMENT, when=None, key=""):
    PointEntry.objects.create(user=user, amount=amount, kind=kind, occurred_at=when, string_key=key, note="x")


def test_ranks_by_points_earned_not_balance(django_user_model):
    ana = make_user(django_user_model, "ana", "alice", "a@example.com")
    bia = make_user(django_user_model, "bia", "bob", "b@example.com")
    add(ana, 300, PointEntry.Kind.TRANSLATED, key="k1")
    add(ana, -250, PointEntry.Kind.REDEMPTION)
    add(bia, 200, PointEntry.Kind.REVIEWED, key="k2")
    add(bia, -50)  # negative adjustment does not count as earned
    add(bia, 50, PointEntry.Kind.REFUND)  # refunds are not earnings
    assert leaderboard() == [("ana", 300), ("bia", 200)]


def test_excludes_unapproved(django_user_model):
    ana = make_user(django_user_model, approved=False)
    add(ana, 100)
    assert leaderboard() == []


def test_since_uses_occurred_at_then_created_at(django_user_model):
    ana = make_user(django_user_model)
    add(ana, 100, PointEntry.Kind.TRANSLATED, when=datetime(2026, 10, 15, tzinfo=timezone.utc), key="old")
    add(ana, 40, PointEntry.Kind.TRANSLATED, when=datetime(2026, 11, 3, tzinfo=timezone.utc), key="new")
    assert leaderboard(since=datetime(2026, 11, 1, tzinfo=timezone.utc)) == [("ana", 40)]


def test_ranking_page_and_toggle(client, django_user_model):
    ana = make_user(django_user_model)
    add(ana, 120)
    html = client.get("/ranking/").content.decode()
    assert "ana" in html and "120" in html
    assert "?periodo=mes" in html
    assert client.get("/ranking/?periodo=mes").status_code == 200


def test_empty_ranking(client):
    assert "Ninguém pontuou" in client.get("/ranking/").content.decode()
