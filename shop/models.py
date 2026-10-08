from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from ledger.models import PointEntry


class StoreSettings(models.Model):
    pix_instructions = models.TextField(
        "instruções do Pix do frete",
        blank=True,
        help_text="Vai no e-mail de aprovação de pedidos pelos Correios (chave Pix, valor ou 'a combinar').",
    )

    class Meta:
        verbose_name = verbose_name_plural = "configurações da loja"

    @classmethod
    def current(cls) -> "StoreSettings":
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return "Configurações da loja"


class Product(models.Model):
    name = models.CharField("nome", max_length=120)
    description = models.TextField("descrição", blank=True)
    cost = models.PositiveIntegerField("custo (pontos)")
    image_url = models.URLField("foto (URL)", max_length=500, blank=True)
    active = models.BooleanField("ativo", default=True)
    position = models.PositiveIntegerField("ordem", default=0)
    allows_mail = models.BooleanField("envio pelos Correios", default=True)
    allows_pickup = models.BooleanField("retirada em mãos", default=True)

    class Meta:
        ordering = ["position", "name"]
        verbose_name = "produto"
        verbose_name_plural = "produtos"

    def __str__(self):
        return self.name

    def clean(self):
        if not (self.allows_mail or self.allows_pickup):
            raise ValidationError("Escolha pelo menos uma forma de entrega.")

    def ensure_default_variant(self) -> "Variant | None":
        """Every product sells through variants; one without sizes gets a single 'Único'."""
        if self.variants.exists():
            return None
        return Variant.objects.create(product=self, name=Variant.DEFAULT_NAME, stock=0)

    @property
    def total_stock(self) -> int:
        return sum(v.stock for v in self.variants.all() if v.active)

    def delivery_choices(self) -> list[tuple[str, str]]:
        choices = []
        if self.allows_mail:
            choices.append((Redemption.Delivery.MAIL, "Correios (frete pago por Pix)"))
        if self.allows_pickup:
            choices.append((Redemption.Delivery.PICKUP, "Em mãos (combinar com a equipe)"))
        return choices


class Variant(models.Model):
    DEFAULT_NAME = "Único"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants", verbose_name="produto")
    name = models.CharField("variação", max_length=40)
    stock = models.PositiveIntegerField("estoque", default=0)
    active = models.BooleanField("ativa", default=True)

    class Meta:
        ordering = ["product", "pk"]
        verbose_name = "variação"
        verbose_name_plural = "variações"
        constraints = [models.UniqueConstraint(fields=["product", "name"], name="unique_variant_name_per_product")]

    def __str__(self):
        return self.product.name if self.name == self.DEFAULT_NAME else f"{self.product.name} — {self.name}"


class Redemption(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Pedido"
        APPROVED = "approved", "Aprovado"
        SHIPPING_PAID = "shipping_paid", "Frete pago"
        SHIPPED = "shipped", "Enviado"
        READY_FOR_PICKUP = "ready_for_pickup", "Pronto para retirar"
        DELIVERED = "delivered", "Entregue"
        REJECTED = "rejected", "Recusado"
        CANCELLED = "cancelled", "Cancelado"

    class Delivery(models.TextChoices):
        MAIL = "mail", "Correios"
        PICKUP = "pickup", "Em mãos"

    ADDRESS_FIELDS = ("full_name", "cep", "street", "number", "complement", "district", "city", "uf", "phone")
    REQUIRED_ADDRESS_FIELDS = ("full_name", "cep", "street", "number", "district", "city", "uf", "phone")
    TRACKING_URL = "https://rastreamento.correios.com.br/app/index.php?objetos={code}"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="redemptions", verbose_name="tradutor"
    )
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="redemptions", verbose_name="item")
    cost = models.PositiveIntegerField("custo pago (pontos)")
    delivery = models.CharField("entrega", max_length=10, choices=Delivery.choices)
    status = models.CharField("etapa", max_length=20, choices=Status.choices, default=Status.REQUESTED)
    ledger_entry = models.OneToOneField(
        PointEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="redemption"
    )
    request_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    full_name = models.CharField("nome completo", max_length=120, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    street = models.CharField("rua", max_length=200, blank=True)
    number = models.CharField("número", max_length=20, blank=True)
    complement = models.CharField("complemento", max_length=100, blank=True)
    district = models.CharField("bairro", max_length=100, blank=True)
    city = models.CharField("cidade", max_length=100, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True)
    phone = models.CharField("telefone", max_length=20, blank=True)

    tracking_code = models.CharField("código de rastreio", max_length=40, blank=True)
    admin_note = models.TextField("observação para o tradutor", blank=True)
    created_at = models.DateTimeField("pedido em", auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField("entregue em", null=True, blank=True)
    address_erased_at = models.DateTimeField("endereço apagado em", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "resgate"
        verbose_name_plural = "resgates"

    def __str__(self):
        return f"#{self.pk} {self.user} — {self.variant}"

    @property
    def tracking_url(self) -> str:
        return self.TRACKING_URL.format(code=self.tracking_code) if self.tracking_code else ""


class RedemptionEvent(models.Model):
    redemption = models.ForeignKey(Redemption, on_delete=models.CASCADE, related_name="events")
    status_from = models.CharField("de", max_length=20, blank=True)
    status_to = models.CharField("para", max_length=20, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="por"
    )
    note = models.TextField("observação", blank=True)
    created_at = models.DateTimeField("quando", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "evento do resgate"
        verbose_name_plural = "histórico"

    def __str__(self):
        return f"{self.status_from or '—'} → {self.status_to or '—'}"
