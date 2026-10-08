from datetime import datetime, timezone

from ledger.models import PointEntry
from transifex.source import ResourceTranslationsSource

LAUNCH = datetime(2026, 11, 1, tzinfo=timezone.utc)
RES = "o:wpilib:p:frc-docs:r:demo"


def tx_item(n, translator="alice", translated="2026-11-02T10:00:00Z", reviewer=None, reviewed=None):
    def user(name):
        return {"data": {"type": "users", "id": f"u:{name}"}} if name else None

    return {
        "id": f"{RES}:s:{n}:l:pt",
        "attributes": {"datetime_translated": translated, "datetime_reviewed": reviewed},
        "relationships": {"translator": user(translator), "reviewer": user(reviewer)},
    }


class FakeClient:
    def __init__(self, translated=(), reviewed=()):
        self.translated, self.reviewed, self.calls = list(translated), list(reviewed), []

    def iter_translations(self, resource_id, language, filters):
        self.calls.append(filters)
        rows = self.reviewed if filters.get("filter[reviewed]") == "true" else self.translated
        return iter(rows)


def run(client, since=LAUNCH):
    return list(ResourceTranslationsSource(client, "l:pt", LAUNCH).events_for(RES, since))


def test_translation_after_launch_becomes_event():
    client = FakeClient(translated=[(tx_item(1), {"other": "Hello big world"})])
    [ev] = run(client)
    assert ev.tx_username == "alice"
    assert ev.kind == PointEntry.Kind.TRANSLATED
    assert ev.string_key == f"{RES}:s:1:l:pt"
    assert ev.words == 3
    assert ev.occurred_at == datetime(2026, 11, 2, 10, tzinfo=timezone.utc)


def test_translation_before_launch_is_skipped():
    client = FakeClient(translated=[(tx_item(1, translated="2026-10-31T23:59:59Z"), {"other": "Hi"})])
    assert run(client) == []


def test_null_translator_is_skipped():
    client = FakeClient(translated=[(tx_item(1, translator=None), {"other": "Hi"})])
    assert run(client) == []


def test_pre_launch_translation_reviewed_after_launch_credits_only_review():
    row = tx_item(1, translated="2026-10-01T00:00:00Z", reviewer="bob", reviewed="2026-11-03T00:00:00Z")
    client = FakeClient(translated=[(row, {"other": "Hi there"})], reviewed=[(row, {"other": "Hi there"})])
    events = run(client)
    assert [(e.tx_username, e.kind, e.words) for e in events] == [("bob", PointEntry.Kind.REVIEWED, 2)]


def test_review_before_launch_is_skipped():
    row = tx_item(1, reviewer="bob", reviewed="2026-10-15T00:00:00Z")
    client = FakeClient(reviewed=[(row, {"other": "Hi"})])
    assert [e.kind for e in run(client)] == []


def test_translated_query_overlaps_cursor_by_one_hour():
    client = FakeClient()
    run(client, since=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc))
    assert client.calls[0] == {"filter[translated]": "true", "filter[date_translated][gt]": "2026-11-05T11:00:00Z"}
    assert client.calls[1] == {"filter[reviewed]": "true"}


def test_since_before_launch_is_clamped_to_launch():
    client = FakeClient()
    run(client, since=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert client.calls[0]["filter[date_translated][gt]"] == "2026-10-31T23:00:00Z"
