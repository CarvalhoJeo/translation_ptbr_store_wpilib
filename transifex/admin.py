from django.contrib import admin

from .models import TrackedResource


@admin.register(TrackedResource)
class TrackedResourceAdmin(admin.ModelAdmin):
    list_display = ["resource_id", "active", "last_synced_at", "cursor", "last_error"]
    list_filter = ["active"]
    search_fields = ["resource_id"]
    readonly_fields = ["cursor", "last_synced_at", "last_error"]
