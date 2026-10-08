from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from .blob import ImageUploadError, upload_product_image, validate_image
from .models import Product, StoreSettings, Variant


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
