from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ProgressEvent:
    """One credited action on Transifex, already normalized. The ledger never sees API JSON."""

    tx_username: str
    kind: str  # PointEntry.Kind.TRANSLATED or PointEntry.Kind.REVIEWED
    string_key: str  # Transifex resource_translation id
    words: int
    occurred_at: datetime
