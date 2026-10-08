from datetime import datetime, timedelta

from .models import Redemption

ADDRESS_RETENTION = timedelta(days=30)


def erase_old_addresses(now: datetime) -> int:
    """LGPD: blank shipping addresses 30 days after delivery; keep status, tracking and history."""
    stale = Redemption.objects.filter(delivered_at__lt=now - ADDRESS_RETENTION, address_erased_at__isnull=True)
    return stale.update(**{name: "" for name in Redemption.ADDRESS_FIELDS}, address_erased_at=now)
