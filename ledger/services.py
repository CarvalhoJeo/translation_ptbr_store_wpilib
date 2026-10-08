from datetime import datetime

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from accounts.models import Profile

from .events import ProgressEvent
from .models import PointEntry, PointRates, UnclaimedEvent


def points_for(kind: str, words: int) -> int:
    rates = PointRates.current()
    per_word = rates.translated_word if kind == PointEntry.Kind.TRANSLATED else rates.reviewed_word
    return per_word * words


def record_progress(event: ProgressEvent) -> bool:
    key = {"string_key": event.string_key, "kind": event.kind}
    if PointEntry.objects.filter(**key).exists() or UnclaimedEvent.objects.filter(**key).exists():
        return False
    amount = points_for(event.kind, event.words)
    profile = (
        Profile.objects.filter(link_status=Profile.LinkStatus.APPROVED, transifex_username__iexact=event.tx_username)
        .select_related("user")
        .first()
    )
    if profile:
        PointEntry.objects.create(
            user=profile.user, amount=amount, words=event.words, occurred_at=event.occurred_at, **key
        )
    else:
        UnclaimedEvent.objects.create(
            tx_username=event.tx_username, words=event.words, amount=amount, occurred_at=event.occurred_at, **key
        )
    return True


def claim_unclaimed(user, tx_username: str) -> int:
    with transaction.atomic():
        events = list(UnclaimedEvent.objects.select_for_update().filter(tx_username__iexact=tx_username))
        already = set(
            PointEntry.objects.filter(string_key__in=[e.string_key for e in events]).values_list("string_key", "kind")
        )
        fresh = [e for e in events if (e.string_key, e.kind) not in already]
        PointEntry.objects.bulk_create(
            PointEntry(
                user=user,
                amount=e.amount,
                kind=e.kind,
                string_key=e.string_key,
                words=e.words,
                occurred_at=e.occurred_at,
            )
            for e in fresh
        )
        UnclaimedEvent.objects.filter(pk__in=[e.pk for e in events]).delete()
    return len(fresh)


def balance(user) -> int:
    return PointEntry.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0


EARNING_KINDS = (PointEntry.Kind.TRANSLATED, PointEntry.Kind.REVIEWED, PointEntry.Kind.ADJUSTMENT)


def leaderboard(since: datetime | None = None, limit: int = 100) -> list[tuple[str, int]]:
    """Points earned by approved translators: translations, reviews and admin adjustments
    (negative adjustments count, so correcting a mistaken bonus also corrects the ranking).
    Redemptions and refunds never affect the ranking."""
    entries = PointEntry.objects.filter(
        kind__in=EARNING_KINDS, user__profile__link_status=Profile.LinkStatus.APPROVED
    )
    if since is not None:
        entries = entries.annotate(earned_at=Coalesce("occurred_at", "created_at")).filter(earned_at__gte=since)
    rows = (
        entries.values("user__username")
        .annotate(points=Sum("amount"))
        .filter(points__gt=0)
        .order_by("-points", "user__username")[:limit]
    )
    return [(row["user__username"], row["points"]) for row in rows]
