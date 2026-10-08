from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ledger.events import ProgressEvent
from ledger.models import PointEntry

from .words import count_words


class ProgressSource(Protocol):
    def events_for(self, resource_id: str, since: datetime) -> Iterator[ProgressEvent]: ...


def _username(relationship: dict | None) -> str | None:
    data = (relationship or {}).get("data")
    return data["id"].removeprefix("u:") if data else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class ResourceTranslationsSource:
    """Builds events from GET /resource_translations (free plan). See spec: Sync."""

    # Re-read a margin before the cursor so strings saved mid-sync are not missed; the ledger dedupes.
    OVERLAP = timedelta(hours=1)

    def __init__(self, client, language: str, launch_at: datetime):
        self._client = client
        self._language = language
        self._launch_at = launch_at

    def events_for(self, resource_id: str, since: datetime) -> Iterator[ProgressEvent]:
        after = (max(since, self._launch_at) - self.OVERLAP).astimezone(timezone.utc)
        translated = {"filter[translated]": "true", "filter[date_translated][gt]": after.strftime("%Y-%m-%dT%H:%M:%SZ")}
        for item, strings in self._client.iter_translations(resource_id, self._language, translated):
            event = self._event(item, strings, PointEntry.Kind.TRANSLATED, "translator", "datetime_translated")
            if event:
                yield event
        # No review-date filter exists in the API, so scan all reviewed strings; the ledger dedupes.
        for item, strings in self._client.iter_translations(resource_id, self._language, {"filter[reviewed]": "true"}):
            event = self._event(item, strings, PointEntry.Kind.REVIEWED, "reviewer", "datetime_reviewed")
            if event:
                yield event

    def _event(self, item, strings, kind, user_field, time_field) -> ProgressEvent | None:
        username = _username(item["relationships"].get(user_field))
        occurred_at = _parse(item["attributes"].get(time_field))
        if not username or occurred_at is None or occurred_at < self._launch_at:
            return None
        return ProgressEvent(
            tx_username=username,
            kind=kind,
            string_key=item["id"],
            words=count_words(strings),
            occurred_at=occurred_at,
        )
