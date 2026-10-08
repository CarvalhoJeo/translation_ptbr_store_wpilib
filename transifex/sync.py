import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import F

from accounts.models import Profile
from ledger.models import UnclaimedEvent
from ledger.services import claim_unclaimed, record_progress

from .models import TrackedResource
from .source import ProgressSource

logger = logging.getLogger(__name__)


class ResourceBusy(Exception):
    """Another sync run holds this resource."""


@dataclass
class SyncResult:
    resources_ok: int = 0
    resources_failed: int = 0
    new_events: int = 0
    claimed_late: int = 0
    resources_skipped: int = 0


def sync_resource(tracked: TrackedResource, source: ProgressSource, now: datetime, launch_at: datetime) -> int:
    since = tracked.cursor or launch_at
    # Fetch everything first: a failure here leaves cursor and points untouched,
    # and the DB transaction stays short.
    events = list(source.events_for(tracked.resource_id, since))
    with transaction.atomic():
        # Overlapping cron runs: skip a resource another run is already writing.
        if not TrackedResource.objects.select_for_update(skip_locked=True).filter(pk=tracked.pk).values_list("pk", flat=True)[:1]:
            raise ResourceBusy(tracked.resource_id)
        new = sum(1 for event in events if record_progress(event))
        tracked.cursor = now
        tracked.last_synced_at = now
        tracked.last_error = ""
        tracked.save(update_fields=["cursor", "last_synced_at", "last_error"])
    return new


def track_project_resources(client, project_id: str) -> int:
    """Start tracking resources added to the project on Transifex; returns how many are new."""
    return sum(TrackedResource.objects.get_or_create(resource_id=rid)[1] for rid in client.iter_resources(project_id))


def sync_all(
    source: ProgressSource,
    now: datetime,
    launch_at: datetime,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SyncResult:
    """Sync active resources, least recently synced first.

    With a `deadline` (a `clock()` reading), no new resource starts after it; the
    skipped ones go first on the next run.
    """
    result = SyncResult()
    resources = list(
        TrackedResource.objects.filter(active=True).order_by(F("last_synced_at").asc(nulls_first=True), "resource_id")
    )
    for index, tracked in enumerate(resources):
        if deadline is not None and clock() >= deadline:
            result.resources_skipped = len(resources) - index
            break
        try:
            result.new_events += sync_resource(tracked, source, now, launch_at)
            result.resources_ok += 1
        except ResourceBusy:
            result.resources_skipped += 1
        except Exception as exc:
            # Nothing was written for this resource; only record the error.
            logger.exception("falha ao sincronizar o recurso %s", tracked.resource_id)
            TrackedResource.objects.filter(pk=tracked.pk).update(last_error=f"{type(exc).__name__}: {exc}"[:2000])
            result.resources_failed += 1
    result.claimed_late = _claim_late_approvals()
    return result


def _claim_late_approvals() -> int:
    """Credit parked events of profiles approved while a sync was running."""
    claimed = 0
    parked = {u.lower() for u in UnclaimedEvent.objects.values_list("tx_username", flat=True)}
    if not parked:
        return 0
    approved = Profile.objects.select_related("user").filter(link_status=Profile.LinkStatus.APPROVED)
    for profile in approved:
        if profile.transifex_username.lower() in parked:
            claimed += claim_unclaimed(profile.user, profile.transifex_username)
    return claimed
