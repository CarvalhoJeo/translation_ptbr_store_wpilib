from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.utils.html import format_html

from . import services
from .blob import ImageUploadError, upload_product_image, validate_image
from .models import Product, Redemption, RedemptionEvent, StoreSettings, Variant


class ProductForm(forms.ModelForm):
    photo_upload = forms.FileField(
        label="Enviar foto", required=False, help_text="JPG, PNG ou WebP, até 4 MB. Substitui a foto atual."
    )

    class Meta:
        model = Product
        fields = "__all__"

    def clean_photo_upload(self):
        upload = self.cleaned_data.get("photo_upload")
        if upload:
            try:
                validate_image(upload)
            except ImageUploadError as exc:
                raise forms.ValidationError(str(exc))
        return upload


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 1
    fields = ["name", "stock", "active"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductForm
    inlines = [VariantInline]
    list_display = ["name", "cost", "stock_total", "active", "position"]
    list_editable = ["active", "position"]
    search_fields = ["name"]
    fields = [
        "name",
        "description",
        "cost",
        "position",
        "active",
        "allows_mail",
        "allows_pickup",
        "photo_upload",
        "image_url",
        "image_preview",
    ]
    readonly_fields = ["image_preview"]

    @admin.display(description="estoque")
    def stock_total(self, obj):
        return obj.total_stock

    @admin.display(description="foto atual")
    def image_preview(self, obj):
        if not obj.image_url:
            return "—"
        return format_html('<img src="{}" alt="" style="max-height:160px;border-radius:8px">', obj.image_url)

    def save_model(self, request, obj, form, change):
        upload = form.cleaned_data.get("photo_upload")
        if upload:
            try:
                obj.image_url = upload_product_image(upload, obj.name)
            except Exception as exc:  # a Blob outage must not lose the rest of the form
                messages.error(request, f"Não foi possível enviar a foto: {exc}")
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.ensure_default_variant()


@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not StoreSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


class TextActionForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, label: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].label = label


class RedemptionEventInline(admin.TabularInline):
    model = RedemptionEvent
    extra = 0
    can_delete = False
    fields = readonly_fields = ["created_at", "status_from", "status_to", "actor", "note"]
    verbose_name_plural = "Histórico"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Redemption)
class RedemptionAdmin(admin.ModelAdmin):
    list_display = ["id", "created_at", "user", "variant", "cost", "delivery", "status", "tracking_code"]
    list_filter = ["status", "delivery"]
    search_fields = ["user__username", "tracking_code", "full_name"]
    list_select_related = ["user", "variant__product"]
    inlines = [RedemptionEventInline]
    actions = [
        "approve_selected",
        "reject_selected",
        "mark_paid_selected",
        "ship_selected",
        "ready_selected",
        "deliver_selected",
    ]

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in Redemption._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Edits happen only through the workflow actions; the detail page is read-only.
        if obj is not None:
            return False
        return super().has_change_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def _apply(self, request, queryset, func, done: str, **kwargs):
        succeeded = 0
        for redemption in queryset.order_by("pk"):
            try:
                func(redemption, request.user, **kwargs)
                succeeded += 1
            except services.RedemptionError as exc:
                self.message_user(request, f"#{redemption.pk}: {exc}", messages.ERROR)
        if succeeded:
            self.message_user(request, f"{succeeded} resgate(s) {done}.", messages.SUCCESS)

    def _apply_with_text(self, request, queryset, *, title, label, func, done, kwarg):
        if "apply" in request.POST:
            form = TextActionForm(request.POST, label=label)
            if form.is_valid():
                self._apply(request, queryset, func, done, **{kwarg: form.cleaned_data["text"]})
                return None
        else:
            form = TextActionForm(
                initial={"_selected_action": request.POST.getlist(ACTION_CHECKBOX_NAME)}, label=label
            )
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "form": form,
            "redemptions": queryset,
            "action": request.POST["action"],
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/shop/redemption_text_action.html", context)

    @admin.action(description="Aprovar", permissions=["change"])
    def approve_selected(self, request, queryset):
        self._apply(request, queryset, services.approve, "aprovado(s)")

    @admin.action(description="Recusar (devolve pontos)", permissions=["change"])
    def reject_selected(self, request, queryset):
        return self._apply_with_text(
            request,
            queryset,
            title="Recusar resgates",
            label="Motivo da recusa",
            func=services.reject,
            done="recusado(s)",
            kwarg="note",
        )

    @admin.action(description="Marcar frete pago", permissions=["change"])
    def mark_paid_selected(self, request, queryset):
        self._apply(request, queryset, services.mark_shipping_paid, "com frete pago")

    @admin.action(description="Marcar enviado (rastreio)", permissions=["change"])
    def ship_selected(self, request, queryset):
        if queryset.values("user_id").distinct().count() > 1:
            self.message_user(
                request,
                "Selecione resgates de um único tradutor para usar o mesmo código de rastreio.",
                messages.ERROR,
            )
            return None
        return self._apply_with_text(
            request,
            queryset,
            title="Marcar como enviado",
            label="Código de rastreio",
            func=services.mark_shipped,
            done="enviado(s)",
            kwarg="tracking_code",
        )

    @admin.action(description="Pronto para retirar", permissions=["change"])
    def ready_selected(self, request, queryset):
        return self._apply_with_text(
            request,
            queryset,
            title="Pronto para retirar",
            label="Onde e quando retirar",
            func=services.mark_ready_for_pickup,
            done="pronto(s) para retirar",
            kwarg="note",
        )

    @admin.action(description="Marcar entregue", permissions=["change"])
    def deliver_selected(self, request, queryset):
        self._apply(request, queryset, services.mark_delivered, "entregue(s)")
