from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class Profile(models.Model):
    class LinkStatus(models.TextChoices):
        NONE = "none", "Sem vínculo"
        PENDING = "pending", "Aguardando aprovação"
        APPROVED = "approved", "Aprovado"
        REJECTED = "rejected", "Recusado"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    transifex_username = models.CharField("usuário no Transifex", max_length=150, blank=True)
    link_status = models.CharField(max_length=10, choices=LinkStatus.choices, default=LinkStatus.NONE)

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfis"
        constraints = [
            models.UniqueConstraint(
                Lower("transifex_username"),
                condition=models.Q(link_status="approved"),
                name="unique_approved_transifex_username",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.transifex_username or '—'} ({self.get_link_status_display()})"
