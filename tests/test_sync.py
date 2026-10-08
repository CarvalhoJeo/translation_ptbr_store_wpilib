from datetime import datetime, timezone

import pytest
import responses
from django.core.management import CommandError, call_command
from responses import matchers

from accounts.services import approve_link, request_link
from ledger.events import ProgressEvent
from ledger.models import PointEntry
from ledger.services import balance
from transifex.client import TransifexError
from transifex.models import TrackedResource
from transifex.sync import sync_all

pytestmark = pytest.mark.django_db

LAUNCH = datetime(2026, 11, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def ev(key, user="alice", words=10):
    return ProgressEvent(user, PointEntry.Kind.TRANSLATED, key, words, datetime(2026, 11, 2, tzinfo=timezone.utc))


class FakeSource:
    def __init__(self, by_resource, fail=()):
        self.by_resource, self.fail, self.since = by_resource, set(fail), {}

    def events_for(self, resource_id, since):
        self.since[resource_id] = since
        yield from self.by_resource.get(resource_id, [])
        if resource_id in self.fail:
            raise TransifexError("HTTP 503 no meio da paginação")


@pytest.fixture
def ana(django_user_model):
    user = django_user_model.objects.create_user("ana")
    approve_link(request_link(user, "alice"))
    return user


def test_sync_credits_points_and_sets_cursor(ana):
    TrackedResource.objects.create(resource_id="r1")
    result = sync_all(FakeSource({"r1": [ev("k1"), ev("k2")]}), NOW, LAUNCH)

    assert (result.resources_ok, result.resources_failed, result.new_events) == (1, 0, 2)
    assert balance(ana) == 40
    tracked = TrackedResource.objects.get()
    assert tracked.cursor == NOW and tracked.last_synced_at == NOW and tracked.last_error == ""


def test_running_twice_does_not_double_count(ana):
    TrackedResource.objects.create(resource_id="r1")
    source = FakeSource({"r1": [ev("k1")]})
    sync_all(source, NOW, LAUNCH)
    second = sync_all(source, NOW, LAUNCH)
    assert second.new_events == 0
    assert balance(ana) == 20


def test_first_sync_starts_at_launch_then_uses_cursor(ana):
    TrackedResource.objects.create(resource_id="r1")
    source = FakeSource({})
    sync_all(source, NOW, LAUNCH)
    assert source.since["r1"] == LAUNCH
    sync_all(source, NOW, LAUNCH)
    assert source.since["r1"] == NOW


def test_failure_mid_resource_rolls_back_and_other_resources_continue(ana):
    TrackedResource.objects.create(resource_id="bad")
    TrackedResource.objects.create(resource_id="good")
    source = FakeSource({"bad": [ev("k1")], "good": [ev("k2")]}, fail={"bad"})

    result = sync_all(source, NOW, LAUNCH)

    assert (result.resources_ok, result.resources_failed) == (1, 1)
    assert balance(ana) == 20  # only k2
    bad = TrackedResource.objects.get(resource_id="bad")
    assert bad.cursor is None
    assert "503" in bad.last_error


def test_inactive_resources_are_skipped(ana):
    TrackedResource.objects.create(resource_id="r1", active=False)
    source = FakeSource({"r1": [ev("k1")]})
    assert sync_all(source, NOW, LAUNCH).resources_ok == 0
    assert balance(ana) == 0


def test_sync_command_requires_token(settings):
    settings.TRANSIFEX_API_TOKEN = ""
    with pytest.raises(CommandError, match="TRANSIFEX_API_TOKEN"):
        call_command("sync_transifex")


@responses.activate
def test_track_resources_command_adds_new_ids_once(settings):
    settings.TRANSIFEX_API_TOKEN = "tok"
    responses.get(
        "https://rest.api.transifex.com/resources",
        match=[matchers.query_param_matcher({"filter[project]": "o:wpilib:p:frc-docs"})],
        json={"data": [{"id": "r1"}, {"id": "r2"}], "links": {"next": None}},
    )
    call_command("track_resources")
    call_command("track_resources")
    assert sorted(TrackedResource.objects.values_list("resource_id", flat=True)) == ["r1", "r2"]
