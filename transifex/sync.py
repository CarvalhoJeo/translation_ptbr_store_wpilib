import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from accounts.models import Profile
from ledger.models import UnclaimedEvent
from ledger.services import claim_unclaimed, record_progress

from .models import TrackedResource
from .source import ProgressSource

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    resources_ok: int = 0
    resources_failed: int = 0
    new_events: int = 0
    claimed_late: int = 0


def sync_resource(tracked: TrackedResource, source: ProgressSource, now: datetime, launch_at: datetime) -> int:
    since = tracked.cursor or launch_at
    # Fetch everything first: a failure here leaves cursor and points untouched,
    # and the DB transaction stays short.
    events = list(source.events_for(tracked.resource_id, since))
    with transaction.atomic():
        new = sum(1 for event in events if record_progress(event))
        tracked.cursor = now
        tracked.last_synced_at = now
        tracked.last_error = ""
        tracked.save(update_fields=["cursor", "last_synced_at", "last_error"])
    return new


def sync_all(source: ProgressSource, now: datetime, launch_at: datetime) -> SyncResult:
    result = SyncResult()
    for tracked in TrackedResource.objects.filter(active=True):
        try:
            result.new_events += sync_resource(tracked, source, now, launch_at)
            result.resources_ok += 1
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
