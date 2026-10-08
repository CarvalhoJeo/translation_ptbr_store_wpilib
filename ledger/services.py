from django.db import transaction
from django.db.models import Sum

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
        PointEntry.objects.bulk_create(
            PointEntry(
                user=user,
                amount=e.amount,
                kind=e.kind,
                string_key=e.string_key,
                words=e.words,
                occurred_at=e.occurred_at,
            )
            for e in events
        )
        UnclaimedEvent.objects.filter(pk__in=[e.pk for e in events]).delete()
    return len(events)


def balance(user) -> int:
    return PointEntry.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0
