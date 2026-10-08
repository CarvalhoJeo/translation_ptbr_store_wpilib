from django.db import models


class TrackedResource(models.Model):
    resource_id = models.CharField(max_length=300, unique=True)
    active = models.BooleanField(default=True)
    cursor = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["resource_id"]
        verbose_name = "recurso monitorado"
        verbose_name_plural = "recursos monitorados"

    def __str__(self):
        return self.resource_id
