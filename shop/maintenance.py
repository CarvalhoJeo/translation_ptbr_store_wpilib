from datetime import datetime, timedelta

from django.db.models import Q

from .models import Redemption

ADDRESS_RETENTION = timedelta(days=30)


def erase_old_addresses(now: datetime) -> int:
    """LGPD: blank shipping addresses 30 days after delivery; keep status, tracking and history."""
    cutoff = now - ADDRESS_RETENTION
    refused = Q(status__in=(Redemption.Status.REJECTED, Redemption.Status.CANCELLED), updated_at__lt=cutoff)
    stale = Redemption.objects.filter(Q(delivered_at__lt=cutoff) | refused, address_erased_at__isnull=True)
    return stale.update(**{name: "" for name in Redemption.ADDRESS_FIELDS}, address_erased_at=now)
