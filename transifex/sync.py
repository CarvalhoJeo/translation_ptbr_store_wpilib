from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from ledger.services import record_progress

from .client import TransifexError
from .models import TrackedResource
from .source import ProgressSource


@dataclass
class SyncResult:
    resources_ok: int = 0
    resources_failed: int = 0
    new_events: int = 0


def sync_resource(tracked: TrackedResource, source: ProgressSource, now: datetime, launch_at: datetime) -> int:
    since = tracked.cursor or launch_at
    with transaction.atomic():
        new = sum(1 for event in source.events_for(tracked.resource_id, since) if record_progress(event))
        tracked.cursor = now
        tracked.last_synced_at = now
        tracked.last_error = ""
        tracked.save()
    return new


def sync_all(source: ProgressSource, now: datetime, launch_at: datetime) -> SyncResult:
    result = SyncResult()
    for tracked in TrackedResource.objects.filter(active=True):
        try:
            result.new_events += sync_resource(tracked, source, now, launch_at)
            result.resources_ok += 1
        except TransifexError as exc:
            # The atomic block rolled back points and cursor; only record the error.
            TrackedResource.objects.filter(pk=tracked.pk).update(last_error=str(exc)[:2000])
            result.resources_failed += 1
    return result
