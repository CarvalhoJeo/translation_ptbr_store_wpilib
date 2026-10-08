from django import forms
from django.contrib import admin

from .models import PointEntry, PointRates, UnclaimedEvent
from .services import balance


@admin.register(PointRates)
class PointRatesAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PointRates.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


class AdjustmentForm(forms.ModelForm):
    note = forms.CharField(label="Motivo", max_length=200, help_text="Obrigatório: aparece no extrato do tradutor.")

    class Meta:
        model = PointEntry
        fields = ["user", "amount", "note"]

    def clean(self):
        cleaned = super().clean()
        user, amount = cleaned.get("user"), cleaned.get("amount")
        if user and amount is not None and amount < 0:
            current = balance(user)
            if current + amount < 0:
                raise forms.ValidationError(f"O ajuste deixaria o saldo negativo (saldo atual: {current}).")
        return cleaned


@admin.register(PointEntry)
class PointEntryAdmin(admin.ModelAdmin):
    """Admins may only add manual adjustments; existing entries are immutable."""

    form = AdjustmentForm
    list_display = ["created_at", "user", "amount", "kind", "words", "note"]
    list_filter = ["kind"]
    search_fields = ["user__username", "string_key", "note"]
    fields = ["user", "amount", "note"]

    def save_model(self, request, obj, form, change):
        obj.kind = PointEntry.Kind.ADJUSTMENT
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

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
