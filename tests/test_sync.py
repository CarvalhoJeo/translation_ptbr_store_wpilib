from datetime import datetime, timezone

import pytest
import responses
from django.core.management import CommandError, call_command
from responses import matchers

from accounts.services import approve_link, request_link
from ledger.events import ProgressEvent
from accounts.models import Profile
from ledger.models import PointEntry, UnclaimedEvent
from ledger.services import balance
from transifex.client import TransifexError
from transifex.models import TrackedResource
from transifex.sync import sync_all
from ledger.services import record_progress

pytestmark = pytest.mark.django_db

LAUNCH = datetime(2026, 11, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def ev(key, user="alice", words=10):
    return ProgressEvent(user, PointEntry.Kind.TRANSLATED, key, words, datetime(2026, 11, 2, tzinfo=timezone.utc))


class FakeSource:
    def __init__(self, by_resource, fail=(), crash=()):
        self.by_resource, self.fail, self.crash, self.since = by_resource, set(fail), set(crash), {}

    def events_for(self, resource_id, since):
        self.since[resource_id] = since
        yield from self.by_resource.get(resource_id, [])
        if resource_id in self.fail:
            raise TransifexError("HTTP 503 no meio da paginação")
        if resource_id in getattr(self, "crash", ()):
            raise ValueError("boom")


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


def test_unexpected_exception_in_one_resource_does_not_abort_sync(ana):
    TrackedResource.objects.create(resource_id="bad")
    TrackedResource.objects.create(resource_id="good")
    source = FakeSource({"bad": [ev("k1")], "good": [ev("k2")]}, crash={"bad"})

    result = sync_all(source, NOW, LAUNCH)

    assert (result.resources_ok, result.resources_failed) == (1, 1)
    assert balance(ana) == 20  # no partial points from "bad"
    bad = TrackedResource.objects.get(resource_id="bad")
    assert bad.cursor is None
    assert "ValueError" in bad.last_error
    assert TrackedResource.objects.get(resource_id="good").cursor == NOW


def test_late_approval_race_is_swept_after_sync(django_user_model):
    user = django_user_model.objects.create_user("ana")
    request_link(user, "alice")
    record_progress(ev("parked"))  # alice not approved yet: parked
    assert UnclaimedEvent.objects.count() == 1
    Profile.objects.filter(user=user).update(link_status="approved")  # race: approved without claiming

    result = sync_all(FakeSource({}), NOW, LAUNCH)

    assert result.claimed_late == 1
    assert balance(user) == 20
    assert UnclaimedEvent.objects.count() == 0


def test_sync_does_not_overwrite_admin_edits_to_other_fields(ana):
    tracked = TrackedResource.objects.create(resource_id="r1")

    class EditingSource(FakeSource):
        def events_for(self, resource_id, since):
            TrackedResource.objects.filter(pk=tracked.pk).update(active=False)
            return super().events_for(resource_id, since)

    sync_all(EditingSource({}), NOW, LAUNCH)
    assert TrackedResource.objects.get().active is False


class FakeClock:
    """Each call advances time by `step` seconds."""

    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_time_budget_stops_before_starting_more_resources(ana):
    for rid in ("r1", "r2", "r3"):
        TrackedResource.objects.create(resource_id=rid)
    source = FakeSource({"r1": [ev("k1")], "r2": [ev("k2")], "r3": [ev("k3")]})

    # clock reads 10, 20, 30...; deadline 25 → r1 and r2 start, r3 is skipped
    result = sync_all(source, NOW, LAUNCH, deadline=25, clock=FakeClock(10))

    assert (result.resources_ok, result.resources_skipped) == (2, 1)
    assert TrackedResource.objects.get(resource_id="r3").cursor is None
    assert balance(ana) == 40


def test_least_recently_synced_resources_go_first(ana):
    TrackedResource.objects.create(resource_id="a-recent", last_synced_at=NOW)
    TrackedResource.objects.create(resource_id="z-never")
    TrackedResource.objects.create(resource_id="m-old", last_synced_at=LAUNCH)
    source = FakeSource({})

    sync_all(source, NOW, LAUNCH)

    assert list(source.since) == ["z-never", "m-old", "a-recent"]
