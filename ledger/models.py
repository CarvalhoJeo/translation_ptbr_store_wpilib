from django.conf import settings
from django.db import models


class PointRates(models.Model):
    translated_word = models.PositiveIntegerField("pontos por palavra traduzida", default=2)
    reviewed_word = models.PositiveIntegerField("pontos por palavra revisada", default=1)

    class Meta:
        verbose_name = verbose_name_plural = "taxas de pontos"

    @classmethod
    def current(cls) -> "PointRates":
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return f"{self.translated_word}/palavra traduzida, {self.reviewed_word}/palavra revisada"


class PointEntry(models.Model):
    """Append-only. Never update or delete rows; add an adjustment instead."""

    class Kind(models.TextChoices):
        TRANSLATED = "translated", "Tradução"
        REVIEWED = "reviewed", "Revisão"
        REDEMPTION = "redemption", "Resgate"
        REFUND = "refund", "Estorno"
        ADJUSTMENT = "adjustment", "Ajuste"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="point_entries")
    amount = models.IntegerField()
    kind = models.CharField(max_length=12, choices=Kind.choices)
    string_key = models.CharField(max_length=500, blank=True)
    words = models.PositiveIntegerField(default=0)
    occurred_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "lançamento de pontos"
        verbose_name_plural = "lançamentos de pontos"
        constraints = [
            models.UniqueConstraint(
                fields=["string_key", "kind"],
                condition=~models.Q(string_key=""),
                name="unique_progress_per_string",
            )
        ]

    def __str__(self):
        return f"{self.user} {self.amount:+d} ({self.get_kind_display()})"


class UnclaimedEvent(models.Model):
    """Post-launch progress by a Transifex user with no approved link yet."""

    tx_username = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=PointEntry.Kind.choices)
    string_key = models.CharField(max_length=500)
    words = models.PositiveIntegerField()
    amount = models.IntegerField()
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "evento pendente"
        verbose_name_plural = "eventos pendentes"
        constraints = [models.UniqueConstraint(fields=["string_key", "kind"], name="unique_unclaimed_per_string")]
