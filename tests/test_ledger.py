from datetime import datetime, timezone

import pytest

from accounts.models import Profile
from accounts.services import LinkError, approve_link, get_profile, request_link
from ledger.events import ProgressEvent
from ledger.models import PointEntry, PointRates, UnclaimedEvent
from ledger.services import balance, record_progress

pytestmark = pytest.mark.django_db

T = datetime(2026, 11, 2, tzinfo=timezone.utc)


def event(user="alice", kind=PointEntry.Kind.TRANSLATED, key="r:x:s:1:l:pt", words=10):
    return ProgressEvent(tx_username=user, kind=kind, string_key=key, words=words, occurred_at=T)


def approved(django_user_model, login, tx_name):
    user = django_user_model.objects.create_user(login)
    profile = request_link(user, tx_name)
    approve_link(profile)
    return user


def test_translation_credits_approved_user_at_default_rate(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    assert record_progress(event(words=10)) is True
    assert balance(user) == 20


def test_review_uses_review_rate(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(kind=PointEntry.Kind.REVIEWED, words=10))
    assert balance(user) == 10


def test_matching_is_case_insensitive(django_user_model):
    user = approved(django_user_model, "ana", "Alice")
    record_progress(event(user="alice"))
    assert balance(user) == 20


def test_same_event_twice_is_ignored(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    assert record_progress(event()) is True
    assert record_progress(event()) is False
    assert balance(user) == 20


def test_translate_and_review_same_string_both_count(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event())
    record_progress(event(kind=PointEntry.Kind.REVIEWED))
    assert balance(user) == 30


def test_unknown_translator_is_parked_then_claimed_on_approval(django_user_model):
    assert record_progress(event(user="bob", words=5)) is True
    assert UnclaimedEvent.objects.count() == 1
    assert record_progress(event(user="bob", words=5)) is False  # idempotent while parked

    user = django_user_model.objects.create_user("bruno")
    profile = request_link(user, "Bob")
    assert approve_link(profile) == 1
    assert balance(user) == 10
    assert UnclaimedEvent.objects.count() == 0


def test_rate_change_only_affects_future_events(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(key="k1", words=10))
    rates = PointRates.current()
    rates.translated_word = 5
    rates.save()
    record_progress(event(key="k2", words=10))
    assert balance(user) == 20 + 50


def test_second_account_cannot_be_approved_for_same_username(django_user_model):
    approved(django_user_model, "ana", "alice")
    other = django_user_model.objects.create_user("intruso")
    profile = get_profile(other)
    profile.transifex_username = "ALICE"
    profile.link_status = Profile.LinkStatus.PENDING
    profile.save()
    with pytest.raises(LinkError):
        approve_link(profile)
    profile.refresh_from_db()
    assert profile.link_status == Profile.LinkStatus.PENDING


def test_balance_includes_negative_entries(django_user_model):
    user = approved(django_user_model, "ana", "alice")
    record_progress(event(words=10))
    PointEntry.objects.create(user=user, amount=-15, kind=PointEntry.Kind.ADJUSTMENT, note="teste")
    assert balance(user) == 5


def test_balance_of_user_without_entries_is_zero(django_user_model):
    assert balance(django_user_model.objects.create_user("nova")) == 0


def test_profile_admin_cannot_edit_link_fields_directly():
    from django.contrib.admin.sites import site

    readonly = site._registry[Profile].get_readonly_fields(request=None)
    assert {"transifex_username", "link_status"} <= set(readonly)
