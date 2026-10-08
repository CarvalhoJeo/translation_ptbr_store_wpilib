from django.contrib import admin

from .models import PointEntry, PointRates, UnclaimedEvent


@admin.register(PointRates)
class PointRatesAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PointRates.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PointEntry)
class PointEntryAdmin(admin.ModelAdmin):
    """Admins may only add manual adjustments; existing entries are immutable."""

    list_display = ["created_at", "user", "amount", "kind", "words", "note"]
    list_filter = ["kind"]
    search_fields = ["user__username", "string_key", "note"]
    fields = ["user", "amount", "note"]

    def save_model(self, request, obj, form, change):
        obj.kind = PointEntry.Kind.ADJUSTMENT
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UnclaimedEvent)
class UnclaimedEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "tx_username", "kind", "words", "amount"]
    search_fields = ["tx_username"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
