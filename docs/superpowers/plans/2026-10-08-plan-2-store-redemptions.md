# Plan 2 — Store, Redemptions, Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let translators spend their points on physical swag. The plan adds:
- a catalog with photos and per-variant stock
- one-item redemptions delivered by mail (shipping paid via Pix outside the site) or in person
- an admin workflow with e-mail notifications
- a public ranking

**Architecture:**
- **New Django app `shop`:** Product, Variant, Redemption, RedemptionEvent, StoreSettings.
- **`shop/services.py`:** owns every state change. Each change runs in one DB transaction with row locks. Points move through the existing append-only ledger (`PointEntry` REDEMPTION/REFUND).
- **`shop/notify.py`:** sends e-mail after commit through Django's SMTP backend (Gmail), and never fails the action.
- **Photos:** `shop/blob.py` uploads them to Vercel Blob from the admin.
- **Cron:** the existing daily `/cron/sync/` also erases delivered addresses after 30 days.

**Tech Stack:** Django 5.2, django-allauth, Postgres (Neon) / SQLite for tests, `vercel` Python SDK (Blob), Pillow, Django SMTP e-mail, pytest + pytest-django.

**Spec:** `docs/superpowers/specs/2026-10-08-store-redemptions-design.md` (base: `docs/superpowers/specs/2026-10-07-translation-store-design.md`)

**Deviation from spec:** the app is named **`shop`**, not `store`, because `store` is already the Django project package (`store/settings.py`). The default variant **"Único"** is created after the admin saves inlines (`Product.ensure_default_variant()` called from `ProductAdmin.save_related`), not in `Product.save()`. Creating it in `save()` would add "Único" before the inline variants are saved.

## Global Constraints

- The repo is **public**. Secrets only come from env vars. Before every commit, run `git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'`, which must print nothing. Test data uses fake people only ("Ana Silva", `ana@example.com`, Transifex users `alice`/`bob`).
- All user-facing text is **pt-BR**.
- The ledger is append-only: never update or delete `PointEntry` rows. A redemption writes `kind=REDEMPTION, amount=-cost`; reject/cancel writes `kind=REFUND, amount=+cost`. `string_key` stays `""` for these.
- Redemption status values: `requested`, `approved`, `shipping_paid`, `shipped`, `ready_for_pickup`, `delivered`, `rejected`, `cancelled`. Delivery values: `mail`, `pickup`.
- Allowed transitions only: requested→approved, requested→rejected, requested→cancelled, approved→shipping_paid (mail), shipping_paid→shipped (mail, tracking required), approved→ready_for_pickup (pickup), shipped→delivered, ready_for_pickup→delivered.
- Required address fields for mail: `full_name, cep, street, number, district, city, uf, phone`. `complement` is optional. CEP is stored as `00000-000`. UF must be one of the 27.
- Addresses are erased 30 days after `delivered_at`.
- Product photos: JPEG/PNG/WebP, ≤ 4 MB, public Vercel Blob.
- E-mail: SMTP `smtp.gmail.com:587` TLS when `EMAIL_HOST_USER` is set, otherwise the console backend. A send failure must not roll back the action; it is recorded as a `RedemptionEvent` note starting with `falha ao enviar e-mail`.
- Run tests with `uv run pytest` (uv at `~/.local/bin/uv`). Never read, print or stage `.env` or `.env.*.local`.
- Work on branch `plan-2-store`, never on `main`. End commit messages with a blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Two translators click "Resgatar" on the last unit at the same moment:** exactly one succeeds, stock never goes negative, and the other sees "Esgotou enquanto você pedia." → Task 11 (Postgres concurrency test). Single-thread behaviour is in Task 4.
2. **A translator double-clicks "Confirmar resgate":** one redemption is created and one set of points is debited, not two → `request_token` idempotency, tested in Task 4 (service) and Task 7 (view).
3. **Gmail is down or the app password is wrong when an admin approves:** the approval still commits and the failure is visible in the redemption history → Task 3 (notify) and Task 6 (admin action under failure).
4. **The admin changes a product's price after someone redeemed it, then rejects the order:** the refund returns the price that was paid, not the new one → Task 4.
5. **An admin applies "Marcar enviado" to a selection mixing statuses:** valid rows advance, invalid rows get a per-row error, and nothing half-applies → Task 6.

---

### Task 1: Admin polish carried over from Plan 1

**Files:**
- Modify: `ledger/models.py` (Meta verbose names), `ledger/admin.py`, `accounts/models.py` (Meta verbose names), `accounts/apps.py`, `ledger/apps.py`, `transifex/apps.py`
- Create: migrations via `makemigrations`; `tests/test_admin_polish.py`

**Interfaces:**
- Produces: `ledger.admin.AdjustmentForm` (note required). The pt-BR verbose names "perfil/perfis", "lançamento de pontos/lançamentos de pontos", "evento pendente/eventos pendentes". App labels: "Tradutores", "Pontos", "Transifex".

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull && git checkout -b plan-2-store
```

- [ ] **Step 2: Write the failing tests** — `tests/test_admin_polish.py`:

```python
import pytest
from django.contrib.auth.models import Permission

from ledger.models import PointEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_logged(client, django_user_model):
    admin = django_user_model.objects.create_superuser("boss", "boss@example.com", "x")
    client.force_login(admin)
    return client, admin


def test_adjustment_requires_a_note(admin_client_logged, django_user_model):
    client, _ = admin_client_logged
    target = django_user_model.objects.create_user("ana")
    response = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": 50, "note": ""})
    assert response.status_code == 200
    assert not PointEntry.objects.exists()


def test_adjustment_with_note_is_saved_as_adjustment(admin_client_logged, django_user_model):
    client, _ = admin_client_logged
    target = django_user_model.objects.create_user("ana")
    response = client.post("/admin/ledger/pointentry/add/", {"user": target.pk, "amount": 50, "note": "Bônus"})
    assert response.status_code == 302
    entry = PointEntry.objects.get()
    assert (entry.kind, entry.amount, entry.note) == (PointEntry.Kind.ADJUSTMENT, 50, "Bônus")


def test_staff_without_ledger_permissions_cannot_see_point_entries(client, django_user_model):
    staff = django_user_model.objects.create_user("staff", is_staff=True)
    client.force_login(staff)
    assert client.get("/admin/ledger/pointentry/").status_code == 403


def test_staff_with_view_permission_can_list_point_entries(client, django_user_model):
    staff = django_user_model.objects.create_user("staff", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_pointentry"))
    client.force_login(staff)
    assert client.get("/admin/ledger/pointentry/").status_code == 200


def test_admin_index_uses_portuguese_names(admin_client_logged):
    client, _ = admin_client_logged
    html = client.get("/admin/").content.decode()
    for label in ["Perfis", "Lançamentos de pontos", "Eventos pendentes", "Tradutores", "Pontos"]:
        assert label in html
    assert "Point entrys" not in html
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_admin_polish.py -v`
Expected: FAIL (the note is accepted when empty; staff without perms gets 200; English labels).

- [ ] **Step 4: Implement**

`ledger/models.py`: add to `PointEntry.Meta`:
```python
        verbose_name = "lançamento de pontos"
        verbose_name_plural = "lançamentos de pontos"
```
Add a `Meta` body to `UnclaimedEvent` (keep its constraint):
```python
    class Meta:
        verbose_name = "evento pendente"
        verbose_name_plural = "eventos pendentes"
        constraints = [models.UniqueConstraint(fields=["string_key", "kind"], name="unique_unclaimed_per_string")]
```

`accounts/models.py`: add to `Profile.Meta`:
```python
        verbose_name = "perfil"
        verbose_name_plural = "perfis"
```

`accounts/apps.py`, `ledger/apps.py`, `transifex/apps.py`: add `verbose_name = "Tradutores"`, `verbose_name = "Pontos"` and `verbose_name = "Transifex"` respectively to each `AppConfig` class.

`ledger/admin.py`, replaced entirely:
```python
from django import forms
from django.contrib import admin

from .models import PointEntry, PointRates, UnclaimedEvent


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
```

Run: `uv run python manage.py makemigrations accounts ledger`

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: pt-BR admin names, required adjustment note, permission-aware ledger admin

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `shop` app — models

**Files:**
- Create: `shop/` via `startapp`, `shop/models.py`, migration, `tests/factories.py`, `tests/test_shop_models.py`
- Modify: `store/settings.py` (INSTALLED_APPS)

**Interfaces:**
- Produces:
  - `shop.models.StoreSettings.current() -> StoreSettings` (field `pix_instructions`)
  - `shop.models.Product`:
    - fields `name, description, cost, image_url, active, position, allows_mail, allows_pickup`
    - `ensure_default_variant() -> Variant | None`
    - property `total_stock: int`
    - `delivery_choices() -> list[tuple[str, str]]`
  - `shop.models.Variant`: fields `product (related_name="variants"), name, stock, active`; `DEFAULT_NAME = "Único"`
  - `shop.models.Redemption`:
    - fields `user (related_name="redemptions"), variant, cost, delivery, status, ledger_entry, request_token, full_name, cep, street, number, complement, district, city, uf, phone, tracking_code, admin_note, created_at, updated_at, delivered_at, address_erased_at`
    - nested `Status` and `Delivery` TextChoices
    - `ADDRESS_FIELDS`, `REQUIRED_ADDRESS_FIELDS`
    - property `tracking_url`
  - `shop.models.RedemptionEvent`: fields `redemption (related_name="events"), status_from, status_to, actor, note, created_at`
  - `tests/factories.py`: `make_user`, `give_points`, `make_product`, `make_redemption`, `MAIL_ADDRESS`

- [ ] **Step 1: Generate the app**

```bash
uv run python manage.py startapp shop && rm shop/tests.py
```
Add `"shop",` to `INSTALLED_APPS` in `store/settings.py`, right after `"transifex",`. In `shop/apps.py`, set `verbose_name = "Loja"`.

- [ ] **Step 2: Write test factories** — `tests/factories.py`:

```python
from accounts.services import approve_link, request_link
from ledger.models import PointEntry
from shop.models import Product, Redemption, Variant

MAIL_ADDRESS = {
    "full_name": "Ana Silva",
    "cep": "01001-000",
    "street": "Praça da Sé",
    "number": "100",
    "complement": "",
    "district": "Sé",
    "city": "São Paulo",
    "uf": "SP",
    "phone": "11999990000",
}


def make_user(django_user_model, username="ana", tx="alice", email="ana@example.com", approved=True, **extra):
    user = django_user_model.objects.create_user(username, email=email, **extra)
    if approved:
        approve_link(request_link(user, tx))
    return user


def give_points(user, amount):
    PointEntry.objects.create(user=user, amount=amount, kind=PointEntry.Kind.ADJUSTMENT, note="teste")


def make_product(name="Camiseta", cost=100, variants=(("M", 5),), **fields):
    product = Product.objects.create(name=name, cost=cost, **fields)
    for variant_name, stock in variants:
        Variant.objects.create(product=product, name=variant_name, stock=stock)
    product.ensure_default_variant()
    return product


def make_redemption(user, product, delivery="mail", status="requested", **fields):
    address = MAIL_ADDRESS if delivery == "mail" else {}
    return Redemption.objects.create(
        user=user,
        variant=product.variants.first(),
        cost=product.cost,
        delivery=delivery,
        status=status,
        **{**address, **fields},
    )
```

- [ ] **Step 3: Write the failing tests** — `tests/test_shop_models.py`:

```python
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import F

from shop.models import Product, Redemption, StoreSettings, Variant
from tests.factories import make_product, make_redemption, make_user

pytestmark = pytest.mark.django_db


def test_product_without_variants_gets_unico():
    product = make_product(variants=())
    assert [v.name for v in product.variants.all()] == ["Único"]


def test_ensure_default_variant_does_nothing_when_variants_exist():
    product = make_product(variants=(("P", 1), ("M", 2)))
    assert product.ensure_default_variant() is None
    assert product.variants.count() == 2


def test_total_stock_ignores_inactive_variants():
    product = make_product(variants=(("P", 1), ("M", 2)))
    Variant.objects.filter(product=product, name="P").update(active=False)
    assert Product.objects.get(pk=product.pk).total_stock == 2


def test_product_must_allow_some_delivery():
    product = Product(name="X", cost=1, allows_mail=False, allows_pickup=False)
    with pytest.raises(ValidationError):
        product.full_clean()


def test_delivery_choices_follow_product_flags():
    assert [c for c, _ in make_product(allows_pickup=False).delivery_choices()] == ["mail"]
    assert [c for c, _ in make_product(name="B", allows_mail=False).delivery_choices()] == ["pickup"]


def test_stock_cannot_go_negative():
    variant = make_product(variants=(("M", 0),)).variants.get()
    with pytest.raises(IntegrityError):
        Variant.objects.filter(pk=variant.pk).update(stock=F("stock") - 1)


def test_store_settings_is_a_singleton():
    assert StoreSettings.current().pk == StoreSettings.current().pk == 1


def test_tracking_url_only_with_code(django_user_model):
    redemption = make_redemption(make_user(django_user_model), make_product())
    assert redemption.tracking_url == ""
    redemption.tracking_code = "AA123456789BR"
    assert "AA123456789BR" in redemption.tracking_url


def test_request_token_is_unique(django_user_model):
    user = make_user(django_user_model)
    product = make_product()
    make_redemption(user, product, request_token="11111111-1111-1111-1111-111111111111")
    with pytest.raises(IntegrityError):
        make_redemption(user, product, request_token="11111111-1111-1111-1111-111111111111")


def test_address_field_lists():
    assert "complement" in Redemption.ADDRESS_FIELDS
    assert "complement" not in Redemption.REQUIRED_ADDRESS_FIELDS
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_shop_models.py -q`
Expected: FAIL with `ImportError` (no models).

- [ ] **Step 5: Implement** — `shop/models.py`:

```python
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
```

Run: `uv run python manage.py makemigrations shop`

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: shop app models — products, variants, redemptions, history

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: E-mail settings and notifications

**Files:**
- Create: `shop/notify.py`; templates `templates/emails/new_redemption.txt`, `approved.txt`, `shipped.txt`, `ready_for_pickup.txt`, `rejected.txt`; `tests/test_notify.py`
- Modify: `store/settings.py`, `.env.example`

**Interfaces:**
- Consumes: the models from Task 2 and `tests/factories.py`.
- Produces:
  - `shop.notify.after_commit(func, redemption_id) -> None`
  - `shop.notify.new_redemption(rid)`, `approved(rid)`, `shipped(rid)`, `ready_for_pickup(rid)`, `rejected(rid)` (all `-> None`)
  - settings `SITE_URL`, `DEFAULT_FROM_EMAIL`, the e-mail backend config

- [ ] **Step 1: Write the failing tests** — `tests/test_notify.py`:

```python
import pytest
from django.core import mail

from shop import notify
from shop.models import RedemptionEvent, StoreSettings
from tests.factories import make_product, make_redemption, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(django_user_model):
    return make_user(django_user_model)


def test_new_redemption_goes_to_staff_with_email(ana, django_user_model):
    django_user_model.objects.create_user("boss", email="boss@example.com", is_staff=True)
    django_user_model.objects.create_user("silent", email="", is_staff=True)
    r = make_redemption(ana, make_product(name="Caneca"))
    notify.new_redemption(r.pk)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["boss@example.com"]
    assert "Caneca" in mail.outbox[0].subject
    assert f"/admin/shop/redemption/{r.pk}/change/" in mail.outbox[0].body


def test_approved_mail_includes_pix_instructions(ana):
    settings_row = StoreSettings.current()
    settings_row.pix_instructions = "Pix: frete@example.com — R$ 25"
    settings_row.save()
    r = make_redemption(ana, make_product(), delivery="mail", status="approved")
    notify.approved(r.pk)
    assert mail.outbox[0].to == ["ana@example.com"]
    assert "Pix: frete@example.com" in mail.outbox[0].body


def test_approved_pickup_mail_talks_about_combining(ana):
    r = make_redemption(ana, make_product(), delivery="pickup", status="approved")
    notify.approved(r.pk)
    assert "combinar" in mail.outbox[0].body
    assert "Pix" not in mail.outbox[0].body


def test_shipped_mail_has_tracking(ana):
    r = make_redemption(ana, make_product(), status="shipped", tracking_code="AA123456789BR")
    notify.shipped(r.pk)
    assert "AA123456789BR" in mail.outbox[0].body


def test_ready_and_rejected_mails_include_admin_note(ana):
    r = make_redemption(ana, make_product(), delivery="pickup", status="ready_for_pickup", admin_note="Regional SP")
    notify.ready_for_pickup(r.pk)
    r2 = make_redemption(ana, make_product(name="B"), status="rejected", admin_note="Sem estoque real")
    notify.rejected(r2.pk)
    assert "Regional SP" in mail.outbox[0].body
    assert "Sem estoque real" in mail.outbox[1].body
    assert "devolvidos" in mail.outbox[1].body


def test_user_without_email_gets_nothing(django_user_model):
    user = make_user(django_user_model, email="")
    notify.approved(make_redemption(user, make_product(), status="approved").pk)
    assert mail.outbox == []


def test_send_failure_is_recorded_not_raised(ana, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(notify, "send_mail", boom)
    r = make_redemption(ana, make_product(), status="approved")
    notify.approved(r.pk)
    event = RedemptionEvent.objects.get(redemption=r)
    assert event.note.startswith("falha ao enviar e-mail")
    assert "smtp down" in event.note


def test_after_commit_defers_until_commit(ana, django_capture_on_commit_callbacks):
    r = make_redemption(ana, make_product(), status="approved")
    with django_capture_on_commit_callbacks(execute=True):
        notify.after_commit(notify.approved, r.pk)
        assert mail.outbox == []
    assert len(mail.outbox) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_notify.py -q`
Expected: FAIL with `ImportError: cannot import name 'notify'`.

- [ ] **Step 3: Settings** — append to `store/settings.py`:

```python
# E-mail: Gmail SMTP when EMAIL_HOST_USER is set (production), console otherwise.
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
if EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL") or (
    f"Loja de Traduções WPILib <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Loja de Traduções WPILib <noreply@localhost>"
)
# Absolute links in e-mails.
SITE_URL = os.environ.get("SITE_URL") or (
    f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
    if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    else "http://localhost:8000"
)
```

Append to `.env.example`:
```
# Gmail used to send store e-mails (needs 2-step verification + an App Password)
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
# Optional: "Loja de Traduções WPILib <conta@gmail.com>"
DEFAULT_FROM_EMAIL=
# Base URL for links in e-mails (auto on Vercel)
SITE_URL=http://localhost:8000
```

- [ ] **Step 4: Implement** `shop/notify.py`:

```python
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from .models import Redemption, RedemptionEvent, StoreSettings

logger = logging.getLogger(__name__)


def after_commit(func, redemption_id: int) -> None:
    """Send only if the surrounding transaction commits."""
    transaction.on_commit(lambda: func(redemption_id))


def _load(redemption_id: int) -> Redemption:
    return Redemption.objects.select_related("user", "variant__product").get(pk=redemption_id)


def _send(redemption: Redemption, template: str, recipients, subject: str, extra: dict | None = None) -> None:
    recipients = [address for address in recipients if address]
    if not recipients:
        return
    body = render_to_string(f"emails/{template}.txt", {"r": redemption, "site_url": settings.SITE_URL, **(extra or {})})
    try:
        send_mail(subject, body, None, recipients)
    except Exception as exc:  # e-mail must never undo a redemption step
        logger.exception("falha ao enviar e-mail %s do resgate %s", template, redemption.pk)
        RedemptionEvent.objects.create(
            redemption=redemption, note=f"falha ao enviar e-mail ({template}): {type(exc).__name__}: {exc}"[:1000]
        )


def new_redemption(redemption_id: int) -> None:
    r = _load(redemption_id)
    staff = (
        get_user_model()
        .objects.filter(is_staff=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    # Fixed path (not reverse()): the Redemption admin is registered in a later task.
    admin_url = f"{settings.SITE_URL}/admin/shop/redemption/{r.pk}/change/"
    _send(r, "new_redemption", list(staff), f"Novo resgate #{r.pk}: {r.variant}", {"admin_url": admin_url})


def approved(redemption_id: int) -> None:
    r = _load(redemption_id)
    extra = {"pix_instructions": StoreSettings.current().pix_instructions}
    _send(r, "approved", [r.user.email], f"Seu resgate #{r.pk} foi aprovado", extra)


def shipped(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "shipped", [r.user.email], f"Seu resgate #{r.pk} foi enviado")


def ready_for_pickup(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "ready_for_pickup", [r.user.email], f"Seu resgate #{r.pk} está pronto para retirar")


def rejected(redemption_id: int) -> None:
    r = _load(redemption_id)
    _send(r, "rejected", [r.user.email], f"Seu resgate #{r.pk} foi recusado")
```

Templates (plain text, pt-BR):

`templates/emails/new_redemption.txt`:
```
Novo resgate na Loja de Traduções WPILib

#{{ r.pk }} — {{ r.user.username }} pediu: {{ r.variant }} ({{ r.cost }} pontos)
Entrega: {{ r.get_delivery_display }}{% if r.delivery == "mail" %}
Destino: {{ r.city }}/{{ r.uf }}{% endif %}

Aprovar ou recusar: {{ admin_url }}
```

`templates/emails/approved.txt`:
```
Olá, {{ r.user.username }}!

Seu resgate #{{ r.pk }} ({{ r.variant }}) foi aprovado.
{% if r.delivery == "mail" %}
Para enviarmos pelos Correios, pague o frete por Pix:

{{ pix_instructions|default:"A equipe vai entrar em contato com o valor do frete." }}

Assim que o pagamento for confirmado, enviamos e mandamos o código de rastreio.
{% else %}
Vamos combinar a entrega em mãos com você em breve.
{% endif %}
Acompanhe em {{ site_url }}/conta/

— Equipe de tradução pt-BR da WPILib
```

`templates/emails/shipped.txt`:
```
Olá, {{ r.user.username }}!

Seu resgate #{{ r.pk }} ({{ r.variant }}) foi enviado pelos Correios.

Código de rastreio: {{ r.tracking_code }}
Rastrear: {{ r.tracking_url }}

— Equipe de tradução pt-BR da WPILib
```

`templates/emails/ready_for_pickup.txt`:
```
Olá, {{ r.user.username }}!

Seu resgate #{{ r.pk }} ({{ r.variant }}) está pronto para retirar em mãos.
{% if r.admin_note %}
{{ r.admin_note }}
{% endif %}
— Equipe de tradução pt-BR da WPILib
```

`templates/emails/rejected.txt`:
```
Olá, {{ r.user.username }}.

Seu resgate #{{ r.pk }} ({{ r.variant }}) foi recusado.
{% if r.admin_note %}Motivo: {{ r.admin_note }}
{% endif %}
Seus {{ r.cost }} pontos foram devolvidos à sua conta.

— Equipe de tradução pt-BR da WPILib
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: store e-mail notifications over SMTP, failures recorded not raised

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Redemption services (request + state machine)

**Files:**
- Create: `shop/services.py`, `tests/test_shop_services.py`

**Interfaces:**
- Consumes:
  - `accounts.services.get_profile`
  - `accounts.models.Profile`
  - `ledger.services.balance`
  - `ledger.models.PointEntry`
  - the Task 2 models
  - `shop.notify` (Task 3)
- Produces:
  - `shop.services.RedemptionError(Exception)`
  - `request_redemption(user, variant_id: int, delivery: str, address: dict | None = None, token: uuid.UUID | None = None) -> Redemption`
  - `approve(redemption, actor) -> Redemption`
  - `reject(redemption, actor, note: str) -> Redemption`
  - `cancel(redemption, user) -> Redemption`
  - `mark_shipping_paid(redemption, actor) -> Redemption`
  - `mark_shipped(redemption, actor, tracking_code: str) -> Redemption`
  - `mark_ready_for_pickup(redemption, actor, note: str) -> Redemption`
  - `mark_delivered(redemption, actor) -> Redemption`

- [ ] **Step 1: Write the failing tests** — `tests/test_shop_services.py`:

```python
import uuid

import pytest
from django.core import mail

from ledger.models import PointEntry
from ledger.services import balance
from shop import services
from shop.models import Redemption, RedemptionEvent, Variant
from shop.services import RedemptionError
from tests.factories import MAIL_ADDRESS, give_points, make_product, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(django_user_model):
    user = make_user(django_user_model)
    give_points(user, 500)
    return user


@pytest.fixture
def boss(django_user_model):
    return django_user_model.objects.create_user("boss", email="boss@example.com", is_staff=True)


def variant_of(product):
    return product.variants.get()


def test_request_by_mail_debits_points_and_stock(ana, boss, django_capture_on_commit_callbacks):
    product = make_product(cost=120, variants=(("M", 2),))
    with django_capture_on_commit_callbacks(execute=True):
        r = services.request_redemption(ana, variant_of(product).pk, "mail", MAIL_ADDRESS)
    assert r.status == "requested" and r.cost == 120 and r.city == "São Paulo"
    assert balance(ana) == 380
    assert Variant.objects.get(pk=r.variant_id).stock == 1
    assert r.ledger_entry.kind == PointEntry.Kind.REDEMPTION and r.ledger_entry.amount == -120
    assert r.events.get().status_to == "requested"
    assert mail.outbox[0].to == ["boss@example.com"]


def test_request_pickup_needs_no_address(ana):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    assert r.delivery == "pickup" and r.full_name == ""


@pytest.mark.parametrize(
    "setup, delivery, address, message",
    [
        ("unlinked", "pickup", None, "Vincule"),
        ("inactive_product", "pickup", None, "não está disponível"),
        ("inactive_variant", "pickup", None, "não está disponível"),
        ("pickup_only", "mail", MAIL_ADDRESS, "Forma de entrega"),
        ("ok", "mail", {**MAIL_ADDRESS, "cep": ""}, "endereço completo"),
        ("no_stock", "pickup", None, "Esgotou"),
        ("expensive", "pickup", None, "Saldo insuficiente"),
        ("ok", "drone", None, "Forma de entrega"),
    ],
)
def test_request_rejections_change_nothing(django_user_model, setup, delivery, address, message):
    user = make_user(django_user_model, approved=setup != "unlinked")
    give_points(user, 200)
    product = make_product(
        cost=1000 if setup == "expensive" else 100,
        variants=(("M", 0 if setup == "no_stock" else 3),),
        active=setup != "inactive_product",
        allows_mail=setup != "pickup_only",
    )
    variant = variant_of(product)
    if setup == "inactive_variant":
        Variant.objects.filter(pk=variant.pk).update(active=False)

    with pytest.raises(RedemptionError, match=message):
        services.request_redemption(user, variant.pk, delivery, address)

    assert balance(user) == 200
    assert Variant.objects.get(pk=variant.pk).stock == (0 if setup == "no_stock" else 3)
    assert not Redemption.objects.exists()


def test_complement_is_optional_and_cep_is_kept(ana):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", {**MAIL_ADDRESS, "complement": ""})
    assert r.cep == "01001-000"


def test_same_token_returns_the_same_redemption(ana):
    token = uuid.uuid4()
    variant = variant_of(make_product(cost=100, variants=(("M", 5),)))
    first = services.request_redemption(ana, variant.pk, "pickup", token=token)
    second = services.request_redemption(ana, variant.pk, "pickup", token=token)
    assert first.pk == second.pk
    assert balance(ana) == 400
    assert Variant.objects.get(pk=variant.pk).stock == 4


def test_full_mail_flow(ana, boss, django_capture_on_commit_callbacks):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", MAIL_ADDRESS)
    with django_capture_on_commit_callbacks(execute=True):
        services.approve(r, boss)
        services.mark_shipping_paid(r, boss)
        services.mark_shipped(r, boss, "AA123456789BR")
        r = services.mark_delivered(r, boss)
    assert r.status == "delivered" and r.delivered_at is not None and r.tracking_code == "AA123456789BR"
    steps = list(r.events.values_list("status_from", "status_to"))
    assert steps == [
        ("", "requested"),
        ("requested", "approved"),
        ("approved", "shipping_paid"),
        ("shipping_paid", "shipped"),
        ("shipped", "delivered"),
    ]
    assert r.events.last().actor == boss
    subjects = [m.subject for m in mail.outbox]
    assert any("aprovado" in s for s in subjects) and any("enviado" in s for s in subjects)


def test_full_pickup_flow(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    services.approve(r, boss)
    services.mark_ready_for_pickup(r, boss, "Regional SP")
    r = services.mark_delivered(r, boss)
    assert r.status == "delivered" and r.admin_note == "Regional SP"


@pytest.mark.parametrize(
    "delivery, steps, bad",
    [
        ("mail", [], lambda r, b: services.mark_shipped(r, b, "X")),
        ("mail", ["approve"], lambda r, b: services.mark_ready_for_pickup(r, b, "x")),
        ("pickup", ["approve"], lambda r, b: services.mark_shipping_paid(r, b)),
        ("mail", ["approve"], lambda r, b: services.mark_delivered(r, b)),
        ("mail", ["approve"], lambda r, b: services.reject(r, b, "x")),
        ("mail", ["approve"], lambda r, b: services.approve(r, b)),
    ],
)
def test_disallowed_transitions(ana, boss, delivery, steps, bad):
    r = services.request_redemption(ana, variant_of(make_product()).pk, delivery, MAIL_ADDRESS if delivery == "mail" else None)
    for step in steps:
        getattr(services, step)(r, boss)
    before = Redemption.objects.get(pk=r.pk).status
    with pytest.raises(RedemptionError):
        bad(r, boss)
    assert Redemption.objects.get(pk=r.pk).status == before


def test_ship_requires_tracking_and_reject_requires_note(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "mail", MAIL_ADDRESS)
    with pytest.raises(RedemptionError, match="motivo"):
        services.reject(r, boss, "  ")
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    with pytest.raises(RedemptionError, match="rastreio"):
        services.mark_shipped(r, boss, "")


def test_reject_refunds_frozen_cost_and_restores_stock(ana, boss, django_capture_on_commit_callbacks):
    product = make_product(cost=100, variants=(("M", 1),))
    r = services.request_redemption(ana, variant_of(product).pk, "pickup")
    product.cost = 999
    product.save()
    with django_capture_on_commit_callbacks(execute=True):
        r = services.reject(r, boss, "Item danificado")
    assert r.status == "rejected" and r.admin_note == "Item danificado"
    assert balance(ana) == 500
    assert variant_of(product).stock == 1
    refund = PointEntry.objects.get(kind=PointEntry.Kind.REFUND)
    assert refund.amount == 100
    assert any("recusado" in m.subject for m in mail.outbox)


def test_owner_can_cancel_only_while_requested(ana, boss, django_user_model):
    other = make_user(django_user_model, username="bia", tx="bob", email="bia@example.com")
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    with pytest.raises(RedemptionError):
        services.cancel(r, other)
    r = services.cancel(r, ana)
    assert r.status == "cancelled" and balance(ana) == 500

    r2 = services.request_redemption(ana, variant_of(make_product(name="B")).pk, "pickup")
    services.approve(r2, boss)
    with pytest.raises(RedemptionError):
        services.cancel(r2, ana)


def test_events_record_actor(ana, boss):
    r = services.request_redemption(ana, variant_of(make_product()).pk, "pickup")
    services.approve(r, boss)
    assert RedemptionEvent.objects.filter(redemption=r, status_to="approved", actor=boss).exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shop_services.py -q`
Expected: FAIL with `ImportError: cannot import name 'services'`.

- [ ] **Step 3: Implement** `shop/services.py`:

```python
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Profile
from accounts.services import get_profile
from ledger.models import PointEntry
from ledger.services import balance

from . import notify
from .models import Redemption, RedemptionEvent, Variant

Status = Redemption.Status
Delivery = Redemption.Delivery


class RedemptionError(Exception):
    pass


def request_redemption(
    user, variant_id: int, delivery: str, address: dict | None = None, token: uuid.UUID | None = None
) -> Redemption:
    if token is not None:
        existing = Redemption.objects.filter(request_token=token, user=user).first()
        if existing:
            return existing
    if get_profile(user).link_status != Profile.LinkStatus.APPROVED:
        raise RedemptionError("Vincule sua conta do Transifex antes de resgatar.")

    with transaction.atomic():
        # Lock the user row so concurrent requests by the same person see each other's debits.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        try:
            variant = Variant.objects.select_for_update().select_related("product").get(pk=variant_id)
        except Variant.DoesNotExist:
            raise RedemptionError("Produto não encontrado.")
        product = variant.product
        if not (product.active and variant.active):
            raise RedemptionError("Este produto não está disponível.")
        if delivery not in dict(product.delivery_choices()):
            raise RedemptionError("Forma de entrega não disponível para este produto.")

        fields = {}
        if delivery == Delivery.MAIL:
            fields = {name: str((address or {}).get(name, "")).strip() for name in Redemption.ADDRESS_FIELDS}
            if any(not fields[name] for name in Redemption.REQUIRED_ADDRESS_FIELDS):
                raise RedemptionError("Preencha o endereço completo para envio pelos Correios.")
        if variant.stock < 1:
            raise RedemptionError("Esgotou enquanto você pedia.")
        if balance(user) < product.cost:
            raise RedemptionError("Saldo insuficiente.")

        Variant.objects.filter(pk=variant.pk).update(stock=F("stock") - 1)
        entry = PointEntry.objects.create(
            user=user, amount=-product.cost, kind=PointEntry.Kind.REDEMPTION, note=f"Resgate: {variant}"[:200]
        )
        redemption = Redemption.objects.create(
            user=user,
            variant=variant,
            cost=product.cost,
            delivery=delivery,
            ledger_entry=entry,
            request_token=token,
            **fields,
        )
        RedemptionEvent.objects.create(redemption=redemption, status_to=Status.REQUESTED, actor=user)
        notify.after_commit(notify.new_redemption, redemption.pk)
    return redemption


def _transition(
    redemption: Redemption,
    *,
    allowed_from: set[str],
    to: str,
    actor,
    note: str = "",
    only_delivery: str | None = None,
    updates: dict | None = None,
    refund: bool = False,
    owner=None,
) -> Redemption:
    with transaction.atomic():
        r = Redemption.objects.select_for_update().get(pk=redemption.pk)
        if owner is not None and r.user_id != owner.pk:
            raise RedemptionError("Este resgate não é seu.")
        if r.status not in allowed_from or (only_delivery and r.delivery != only_delivery):
            raise RedemptionError(f"Não é possível passar de “{r.get_status_display()}” para “{Status(to).label}”.")
        previous = r.status
        r.status = to
        for name, value in (updates or {}).items():
            setattr(r, name, value)
        r.save()
        if refund:
            PointEntry.objects.create(
                user_id=r.user_id, amount=r.cost, kind=PointEntry.Kind.REFUND, note=f"Estorno do resgate #{r.pk}"
            )
            Variant.objects.filter(pk=r.variant_id).update(stock=F("stock") + 1)
        RedemptionEvent.objects.create(redemption=r, status_from=previous, status_to=to, actor=actor, note=note)
    return r


def approve(redemption, actor) -> Redemption:
    r = _transition(redemption, allowed_from={Status.REQUESTED}, to=Status.APPROVED, actor=actor)
    notify.after_commit(notify.approved, r.pk)
    return r


def reject(redemption, actor, note: str) -> Redemption:
    note = (note or "").strip()
    if not note:
        raise RedemptionError("Informe o motivo da recusa.")
    r = _transition(
        redemption,
        allowed_from={Status.REQUESTED},
        to=Status.REJECTED,
        actor=actor,
        note=note,
        updates={"admin_note": note},
        refund=True,
    )
    notify.after_commit(notify.rejected, r.pk)
    return r


def cancel(redemption, user) -> Redemption:
    return _transition(
        redemption, allowed_from={Status.REQUESTED}, to=Status.CANCELLED, actor=user, refund=True, owner=user
    )


def mark_shipping_paid(redemption, actor) -> Redemption:
    return _transition(
        redemption, allowed_from={Status.APPROVED}, to=Status.SHIPPING_PAID, actor=actor, only_delivery=Delivery.MAIL
    )


def mark_shipped(redemption, actor, tracking_code: str) -> Redemption:
    tracking_code = (tracking_code or "").strip().upper()
    if not tracking_code:
        raise RedemptionError("Informe o código de rastreio.")
    r = _transition(
        redemption,
        allowed_from={Status.SHIPPING_PAID},
        to=Status.SHIPPED,
        actor=actor,
        note=tracking_code,
        updates={"tracking_code": tracking_code},
    )
    notify.after_commit(notify.shipped, r.pk)
    return r


def mark_ready_for_pickup(redemption, actor, note: str) -> Redemption:
    note = (note or "").strip()
    r = _transition(
        redemption,
        allowed_from={Status.APPROVED},
        to=Status.READY_FOR_PICKUP,
        actor=actor,
        note=note,
        only_delivery=Delivery.PICKUP,
        updates={"admin_note": note},
    )
    notify.after_commit(notify.ready_for_pickup, r.pk)
    return r


def mark_delivered(redemption, actor) -> Redemption:
    return _transition(
        redemption,
        allowed_from={Status.SHIPPED, Status.READY_FOR_PICKUP},
        to=Status.DELIVERED,
        actor=actor,
        updates={"delivered_at": timezone.now()},
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: redemption services — locked request, state machine, refunds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Product photos (Vercel Blob) and product admin

**Files:**
- Create: `shop/blob.py`, `shop/admin.py`, `tests/test_shop_admin_products.py`
- Modify: `pyproject.toml` / `uv.lock` (deps)

**Interfaces:**
- Consumes: `Product`, `Variant` and `StoreSettings` from Task 2.
- Produces:
  - `shop.blob.ImageUploadError(Exception)`
  - `shop.blob.validate_image(uploaded) -> tuple[str, str]` (ext, content type)
  - `shop.blob.upload_product_image(uploaded, product_name: str) -> str` (public URL)
  - `shop.admin.ProductAdmin`, `VariantInline`, `StoreSettingsAdmin`

- [ ] **Step 1: Add dependencies**

```bash
uv add vercel pillow
```

- [ ] **Step 2: Write the failing tests** — `tests/test_shop_admin_products.py`:

```python
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from shop import admin as shop_admin
from shop.blob import ImageUploadError, validate_image
from shop.models import Product

pytestmark = pytest.mark.django_db


def image_file(fmt="PNG", name="foto.png", size=(10, 10)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


@pytest.fixture
def boss_client(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("boss", "boss@example.com", "x"))
    return client


def product_post(**overrides):
    data = {
        "name": "Camiseta",
        "description": "Algodão",
        "cost": "300",
        "position": "0",
        "active": "on",
        "allows_mail": "on",
        "allows_pickup": "on",
        "image_url": "",
        "variants-TOTAL_FORMS": "2",
        "variants-INITIAL_FORMS": "0",
        "variants-MIN_NUM_FORMS": "0",
        "variants-MAX_NUM_FORMS": "1000",
        "variants-0-name": "P",
        "variants-0-stock": "3",
        "variants-0-active": "on",
        "variants-1-name": "M",
        "variants-1-stock": "5",
        "variants-1-active": "on",
    }
    data.update(overrides)
    return data


def test_validate_image_accepts_png_jpeg_webp():
    assert validate_image(image_file("PNG")) == ("png", "image/png")
    assert validate_image(image_file("JPEG", "f.jpg")) == ("jpg", "image/jpeg")
    assert validate_image(image_file("WEBP", "f.webp")) == ("webp", "image/webp")


@pytest.mark.parametrize(
    "upload, message",
    [
        (SimpleUploadedFile("x.png", b"not an image"), "inválido"),
        (image_file("GIF", "x.gif"), "JPG, PNG ou WebP"),
        (SimpleUploadedFile("big.png", b"0" * (4 * 1024 * 1024 + 1)), "4 MB"),
    ],
)
def test_validate_image_rejects(upload, message):
    with pytest.raises(ImageUploadError, match=message):
        validate_image(upload)


def test_admin_creates_product_with_variants_and_uploads_photo(boss_client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        shop_admin, "upload_product_image", lambda f, name: calls.append(name) or "https://blob.example/p.png"
    )
    response = boss_client.post("/admin/shop/product/add/", product_post(photo_upload=image_file()))
    assert response.status_code == 302, response.content.decode()[:2000]
    product = Product.objects.get()
    assert product.image_url == "https://blob.example/p.png"
    assert calls == ["Camiseta"]
    assert sorted((v.name, v.stock) for v in product.variants.all()) == [("M", 5), ("P", 3)]


def test_admin_product_without_variants_gets_unico(boss_client):
    data = product_post(**{"variants-TOTAL_FORMS": "0"})
    assert boss_client.post("/admin/shop/product/add/", data).status_code == 302
    assert [v.name for v in Product.objects.get().variants.all()] == ["Único"]


def test_admin_rejects_invalid_photo(boss_client, monkeypatch):
    monkeypatch.setattr(shop_admin, "upload_product_image", lambda f, name: pytest.fail("should not upload"))
    data = product_post(photo_upload=SimpleUploadedFile("x.png", b"nope"))
    response = boss_client.post("/admin/shop/product/add/", data)
    assert response.status_code == 200
    assert "inválido" in response.content.decode()
    assert not Product.objects.exists()


def test_blob_outage_keeps_product_and_warns(boss_client, monkeypatch):
    def down(f, name):
        raise RuntimeError("blob down")

    monkeypatch.setattr(shop_admin, "upload_product_image", down)
    response = boss_client.post("/admin/shop/product/add/", product_post(photo_upload=image_file()), follow=True)
    assert Product.objects.get().image_url == ""
    assert "Não foi possível enviar a foto" in response.content.decode()


def test_product_requires_a_delivery_option(boss_client):
    data = product_post()
    data.pop("allows_mail")
    data.pop("allows_pickup")
    response = boss_client.post("/admin/shop/product/add/", data)
    assert response.status_code == 200
    assert "Escolha pelo menos uma forma de entrega" in response.content.decode()


def test_store_settings_admin_is_singleton(boss_client):
    assert boss_client.get("/admin/shop/storesettings/add/").status_code == 200
    from shop.models import StoreSettings

    StoreSettings.current()
    assert boss_client.get("/admin/shop/storesettings/add/").status_code == 403
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_shop_admin_products.py -q`
Expected: FAIL with `ImportError` (`shop.blob`).

- [ ] **Step 4: Implement** `shop/blob.py`:

```python
import secrets

import vercel.blob
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
MAX_BYTES = 4 * 1024 * 1024  # under Vercel's 4.5 MB request body limit


class ImageUploadError(Exception):
    pass


def validate_image(uploaded) -> tuple[str, str]:
    if uploaded.size > MAX_BYTES:
        raise ImageUploadError("A foto deve ter no máximo 4 MB.")
    try:
        with Image.open(uploaded) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError):
        raise ImageUploadError("Arquivo de imagem inválido.")
    finally:
        uploaded.seek(0)
    if image_format not in ALLOWED_FORMATS:
        raise ImageUploadError("Use uma foto JPG, PNG ou WebP.")
    return ALLOWED_FORMATS[image_format]


def upload_product_image(uploaded, product_name: str) -> str:
    """Upload to the project's public Blob store (BLOB_READ_WRITE_TOKEN) and return the public URL."""
    ext, content_type = validate_image(uploaded)
    path = f"products/{slugify(product_name) or 'produto'}-{secrets.token_hex(4)}.{ext}"
    result = vercel.blob.put(path, uploaded.read(), access="public", content_type=content_type)
    return result.url
```

`shop/admin.py`:

```python
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
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: product admin with variants inline and Vercel Blob photo upload

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Redemption admin with workflow actions

**Files:**
- Modify: `shop/admin.py` (append)
- Create: `templates/admin/shop/redemption_text_action.html`, `tests/test_shop_admin_redemptions.py`

**Interfaces:**
- Consumes: `shop.services` from Task 4.
- Produces: `shop.admin.RedemptionAdmin`, with action names `approve_selected`, `reject_selected`, `mark_paid_selected`, `ship_selected`, `ready_selected` and `deliver_selected`.

- [ ] **Step 1: Write the failing tests** — `tests/test_shop_admin_redemptions.py`:

```python
import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core import mail

from ledger.services import balance
from shop import notify, services
from shop.models import Redemption
from tests.factories import MAIL_ADDRESS, give_points, make_product, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def boss(django_user_model):
    return django_user_model.objects.create_superuser("boss", "boss@example.com", "x")


@pytest.fixture
def boss_client(client, boss):
    client.force_login(boss)
    return client


@pytest.fixture
def ana(django_user_model):
    user = make_user(django_user_model)
    give_points(user, 1000)
    return user


def new(ana, delivery="mail", name="Camiseta"):
    variant = make_product(name=name).variants.get()
    return services.request_redemption(ana, variant.pk, delivery, MAIL_ADDRESS if delivery == "mail" else None)


def act(client, action, ids, **extra):
    data = {"action": action, ACTION_CHECKBOX_NAME: [str(i) for i in ids], **extra}
    return client.post("/admin/shop/redemption/", data, follow=True)


def test_approve_action_advances_and_emails(boss_client, ana, django_capture_on_commit_callbacks):
    r = new(ana)
    with django_capture_on_commit_callbacks(execute=True):
        response = act(boss_client, "approve_selected", [r.pk])
    assert Redemption.objects.get(pk=r.pk).status == "approved"
    assert "1 resgate(s) aprovado(s)" in response.content.decode()
    assert any("aprovado" in m.subject for m in mail.outbox)


def test_reject_shows_form_then_refunds(boss_client, ana):
    r = new(ana)
    first = act(boss_client, "reject_selected", [r.pk])
    assert "Motivo da recusa" in first.content.decode()
    assert Redemption.objects.get(pk=r.pk).status == "requested"

    act(boss_client, "reject_selected", [r.pk], apply="1", text="Sem estoque real")
    r.refresh_from_db()
    assert r.status == "rejected" and r.admin_note == "Sem estoque real"
    assert balance(ana) == 1000


def test_reject_without_text_keeps_form(boss_client, ana):
    r = new(ana)
    response = act(boss_client, "reject_selected", [r.pk], apply="1", text="")
    assert "Motivo da recusa" in response.content.decode()
    assert Redemption.objects.get(pk=r.pk).status == "requested"


def test_ship_with_tracking(boss_client, ana, boss):
    r = new(ana)
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    act(boss_client, "ship_selected", [r.pk], apply="1", text="aa123456789br")
    r.refresh_from_db()
    assert (r.status, r.tracking_code) == ("shipped", "AA123456789BR")


def test_mixed_selection_reports_per_row_errors(boss_client, ana, boss):
    paid = new(ana, name="A")
    services.approve(paid, boss)
    services.mark_shipping_paid(paid, boss)
    still_requested = new(ana, name="B")

    response = act(boss_client, "ship_selected", [paid.pk, still_requested.pk], apply="1", text="BR1")
    html = response.content.decode()
    assert Redemption.objects.get(pk=paid.pk).status == "shipped"
    assert Redemption.objects.get(pk=still_requested.pk).status == "requested"
    assert f"#{still_requested.pk}:" in html


def test_pickup_ready_then_delivered(boss_client, ana, boss):
    r = new(ana, delivery="pickup")
    services.approve(r, boss)
    act(boss_client, "ready_selected", [r.pk], apply="1", text="Regional SP")
    act(boss_client, "deliver_selected", [r.pk])
    r.refresh_from_db()
    assert r.status == "delivered" and r.admin_note == "Regional SP"


def test_approve_commits_even_if_email_fails(boss_client, ana, monkeypatch, django_capture_on_commit_callbacks):
    r = new(ana)
    monkeypatch.setattr(notify, "send_mail", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    with django_capture_on_commit_callbacks(execute=True):
        act(boss_client, "approve_selected", [r.pk])
    r.refresh_from_db()
    assert r.status == "approved"
    assert r.events.filter(note__startswith="falha ao enviar e-mail").exists()


def test_change_page_shows_history_and_is_read_only(boss_client, ana):
    r = new(ana)
    response = boss_client.get(f"/admin/shop/redemption/{r.pk}/change/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Histórico" in html
    assert 'name="cost"' not in html


def test_admin_cannot_add_or_delete_redemptions(boss_client):
    assert boss_client.get("/admin/shop/redemption/add/").status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shop_admin_redemptions.py -q`
Expected: FAIL (404 / no such admin).

- [ ] **Step 3: Implement**. Append to `shop/admin.py`, and update its imports to:

```python
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.utils.html import format_html

from . import services
from .blob import ImageUploadError, upload_product_image, validate_image
from .models import Product, Redemption, RedemptionEvent, StoreSettings, Variant
```

Appended code:

```python
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

    @admin.action(description="Aprovar")
    def approve_selected(self, request, queryset):
        self._apply(request, queryset, services.approve, "aprovado(s)")

    @admin.action(description="Recusar (devolve pontos)")
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

    @admin.action(description="Marcar frete pago")
    def mark_paid_selected(self, request, queryset):
        self._apply(request, queryset, services.mark_shipping_paid, "com frete pago")

    @admin.action(description="Marcar enviado (rastreio)")
    def ship_selected(self, request, queryset):
        return self._apply_with_text(
            request,
            queryset,
            title="Marcar como enviado",
            label="Código de rastreio",
            func=services.mark_shipped,
            done="enviado(s)",
            kwarg="tracking_code",
        )

    @admin.action(description="Pronto para retirar")
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

    @admin.action(description="Marcar entregue")
    def deliver_selected(self, request, queryset):
        self._apply(request, queryset, services.mark_delivered, "entregue(s)")
```

`templates/admin/shop/redemption_text_action.html`:

```html
{% extends "admin/base_site.html" %}
{% block content %}
<form method="post">
  {% csrf_token %}
  <p>Resgates selecionados:</p>
  <ul>
    {% for r in redemptions %}
      <li>#{{ r.pk }} — {{ r.user }} — {{ r.variant }} ({{ r.get_status_display }}, {{ r.get_delivery_display }})</li>
    {% endfor %}
  </ul>
  {{ form.as_p }}
  <input type="hidden" name="action" value="{{ action }}">
  <input type="hidden" name="apply" value="1">
  <input type="submit" value="Confirmar">
  <a href="{% url 'admin:shop_redemption_changelist' %}">Cancelar</a>
</form>
{% endblock %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: redemption admin with per-step workflow actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Catalog and redemption request pages

**Files:**
- Create: `shop/forms.py`, `shop/views.py`, `shop/urls.py`, `shop/templates/shop/catalog.html`, `shop/templates/shop/request.html`, `tests/test_shop_views.py`
- Modify: `store/urls.py`, `templates/base.html` (nav), `accounts/templates/accounts/home.html` (step 3 link), `static/css/site.css` (append)

**Interfaces:**
- Consumes: `shop.services` (Task 4), `ledger.services.balance`, `accounts.services.get_profile`.
- Produces:
  - URL names `shop_catalog` (`/loja/`), `shop_request` (`/loja/<product_id>/resgatar/`) and `shop_cancel` (`/loja/resgates/<pk>/cancelar/`, POST only)
  - `shop.forms.RedemptionForm(product, data=None)` with `.address() -> dict`

- [ ] **Step 1: Write the failing tests** — `tests/test_shop_views.py`:

```python
import uuid

import pytest

from ledger.services import balance
from shop import services
from shop.models import Redemption, Variant
from tests.factories import MAIL_ADDRESS, give_points, make_product, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(django_user_model):
    user = make_user(django_user_model)
    give_points(user, 500)
    return user


def test_catalog_lists_active_products_and_states(client, ana):
    make_product(name="Camiseta", cost=100, variants=(("P", 0), ("M", 2)))
    make_product(name="Boné", cost=900)
    make_product(name="Adesivo", variants=(("Único", 0),))
    make_product(name="Escondido", active=False)
    client.force_login(ana)
    html = client.get("/loja/").content.decode()
    assert "Camiseta" in html and "Boné" in html and "Escondido" not in html
    assert "Esgotado" in html
    assert "Você precisa de 900 pontos" in html
    assert "Resgatar" in html


def test_catalog_for_anonymous_and_unlinked(client, django_user_model):
    make_product()
    assert "Entre com GitHub para resgatar" in client.get("/loja/").content.decode()
    client.force_login(make_user(django_user_model, approved=False))
    assert "Vincule sua conta" in client.get("/loja/").content.decode()


def test_request_page_offers_only_stocked_variants(client, ana):
    product = make_product(variants=(("P", 0), ("M", 2)))
    client.force_login(ana)
    html = client.get(f"/loja/{product.pk}/resgatar/").content.decode()
    assert ">M<" in html.replace(" ", "") or "M</label>" in html
    p = Variant.objects.get(product=product, name="P")
    assert f'value="{p.pk}"' not in html


def test_pickup_request_redirects_to_account(client, ana):
    product = make_product(cost=100)
    client.force_login(ana)
    data = {"variant": product.variants.get().pk, "delivery": "pickup", "request_token": str(uuid.uuid4())}
    response = client.post(f"/loja/{product.pk}/resgatar/", data)
    assert response.status_code == 302 and response["Location"] == "/conta/"
    assert Redemption.objects.get().delivery == "pickup"
    assert balance(ana) == 400


def test_double_submit_creates_one_redemption(client, ana):
    product = make_product(cost=100)
    client.force_login(ana)
    data = {"variant": product.variants.get().pk, "delivery": "pickup", "request_token": str(uuid.uuid4())}
    client.post(f"/loja/{product.pk}/resgatar/", data)
    client.post(f"/loja/{product.pk}/resgatar/", data)
    assert Redemption.objects.count() == 1
    assert balance(ana) == 400


def test_mail_request_validates_address(client, ana):
    product = make_product()
    client.force_login(ana)
    base = {"variant": product.variants.get().pk, "delivery": "mail", "request_token": str(uuid.uuid4())}
    response = client.post(f"/loja/{product.pk}/resgatar/", {**base, **MAIL_ADDRESS, "cep": "123", "uf": "XX"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "CEP inválido" in html
    assert not Redemption.objects.exists()

    response = client.post(f"/loja/{product.pk}/resgatar/", {**base, "full_name": "Ana Silva"})
    assert "Obrigatório para envio pelos Correios" in response.content.decode()


def test_mail_request_normalizes_cep(client, ana):
    product = make_product()
    client.force_login(ana)
    data = {
        "variant": product.variants.get().pk,
        "delivery": "mail",
        "request_token": str(uuid.uuid4()),
        **MAIL_ADDRESS,
        "cep": "01001000",
    }
    assert client.post(f"/loja/{product.pk}/resgatar/", data).status_code == 302
    assert Redemption.objects.get().cep == "01001-000"


def test_insufficient_points_shows_error(client, django_user_model):
    poor = make_user(django_user_model)
    product = make_product(cost=100)
    client.force_login(poor)
    data = {"variant": product.variants.get().pk, "delivery": "pickup", "request_token": str(uuid.uuid4())}
    html = client.post(f"/loja/{product.pk}/resgatar/", data).content.decode()
    assert "Saldo insuficiente" in html


def test_inactive_product_is_404(client, ana):
    product = make_product(active=False)
    client.force_login(ana)
    assert client.get(f"/loja/{product.pk}/resgatar/").status_code == 404


def test_request_requires_login(client):
    product = make_product()
    assert client.get(f"/loja/{product.pk}/resgatar/").status_code == 302


def test_cancel_own_requested_redemption(client, ana):
    r = services.request_redemption(ana, make_product(cost=100).variants.get().pk, "pickup")
    client.force_login(ana)
    assert client.get(f"/loja/resgates/{r.pk}/cancelar/").status_code == 405
    client.post(f"/loja/resgates/{r.pk}/cancelar/")
    assert Redemption.objects.get(pk=r.pk).status == "cancelled"
    assert balance(ana) == 500


def test_cannot_cancel_someone_elses(client, ana, django_user_model):
    r = services.request_redemption(ana, make_product().variants.get().pk, "pickup")
    client.force_login(make_user(django_user_model, username="bia", tx="bob", email="b@example.com"))
    assert client.post(f"/loja/resgates/{r.pk}/cancelar/").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_shop_views.py -q`
Expected: FAIL (404s).

- [ ] **Step 3: Implement**

`shop/forms.py`:

```python
import re
import uuid

from django import forms

from .models import Redemption

UF_CHOICES = [("", "—")] + [
    (uf, uf)
    for uf in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
]
CEP_RE = re.compile(r"^(\d{5})-?(\d{3})$")


class VariantChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class RedemptionForm(forms.Form):
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    variant = VariantChoiceField(queryset=None, label="Tamanho / variação", empty_label=None, widget=forms.RadioSelect)
    delivery = forms.ChoiceField(label="Entrega", widget=forms.RadioSelect)
    full_name = forms.CharField(label="Nome completo", max_length=120, required=False)
    cep = forms.CharField(label="CEP", max_length=9, required=False)
    street = forms.CharField(label="Rua", max_length=200, required=False)
    number = forms.CharField(label="Número", max_length=20, required=False)
    complement = forms.CharField(label="Complemento", max_length=100, required=False)
    district = forms.CharField(label="Bairro", max_length=100, required=False)
    city = forms.CharField(label="Cidade", max_length=100, required=False)
    uf = forms.ChoiceField(label="UF", choices=UF_CHOICES, required=False)
    phone = forms.CharField(label="Telefone (com DDD)", max_length=20, required=False)

    def __init__(self, product, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        variants = product.variants.filter(active=True, stock__gt=0)
        self.fields["variant"].queryset = variants
        if len(variants) == 1:
            self.fields["variant"].initial = variants[0].pk
        self.fields["delivery"].choices = product.delivery_choices()
        if len(self.fields["delivery"].choices) == 1:
            self.fields["delivery"].initial = self.fields["delivery"].choices[0][0]
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()

    def clean_cep(self):
        value = self.cleaned_data["cep"].strip()
        if not value:
            return ""
        match = CEP_RE.match(value)
        if not match:
            raise forms.ValidationError("CEP inválido (use 00000-000).")
        return f"{match.group(1)}-{match.group(2)}"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("delivery") == Redemption.Delivery.MAIL:
            for name in Redemption.REQUIRED_ADDRESS_FIELDS:
                if not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, "Obrigatório para envio pelos Correios.")
        return cleaned

    def address(self) -> dict:
        return {name: self.cleaned_data.get(name, "") for name in Redemption.ADDRESS_FIELDS}
```

`shop/views.py`:

```python
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Profile
from accounts.services import get_profile
from ledger.services import balance

from . import services
from .forms import RedemptionForm
from .models import Product, Redemption, Variant


def catalog(request):
    products = Product.objects.filter(active=True).prefetch_related(
        Prefetch("variants", queryset=Variant.objects.filter(active=True))
    )
    context = {"products": products, "balance": None, "linked": False}
    if request.user.is_authenticated:
        context["balance"] = balance(request.user)
        context["linked"] = get_profile(request.user).link_status == Profile.LinkStatus.APPROVED
    return render(request, "shop/catalog.html", context)


@login_required
def request_view(request, product_id: int):
    product = get_object_or_404(Product, pk=product_id, active=True)
    form = RedemptionForm(product, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            redemption = services.request_redemption(
                request.user,
                form.cleaned_data["variant"].pk,
                form.cleaned_data["delivery"],
                form.address(),
                token=form.cleaned_data["request_token"],
            )
        except services.RedemptionError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Resgate #{redemption.pk} pedido! Acompanhe em Minha conta.")
            return redirect("account")
    return render(request, "shop/request.html", {"product": product, "form": form, "balance": balance(request.user)})


@login_required
@require_POST
def cancel_view(request, pk: int):
    redemption = get_object_or_404(Redemption, pk=pk, user=request.user)
    try:
        services.cancel(redemption, request.user)
        messages.success(request, "Resgate cancelado e pontos devolvidos.")
    except services.RedemptionError as exc:
        messages.error(request, str(exc))
    return redirect("account")
```

`shop/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("loja/", views.catalog, name="shop_catalog"),
    path("loja/<int:product_id>/resgatar/", views.request_view, name="shop_request"),
    path("loja/resgates/<int:pk>/cancelar/", views.cancel_view, name="shop_cancel"),
]
```

`store/urls.py`: add `path("", include("shop.urls")),` before `path("", include("accounts.urls")),`.

`shop/templates/shop/catalog.html`:

```html
{% extends "base.html" %}
{% load socialaccount %}
{% block title %}Loja — Loja de Traduções WPILib{% endblock %}
{% block content %}
  <div class="account-head">
    <h1>Loja</h1>
    {% if balance is not None %}
      <div class="coin"><span class="coin__icon" aria-hidden="true">★</span><span class="coin__text"><strong>{{ balance }} pontos</strong> disponíveis</span></div>
    {% endif %}
  </div>

  {% if products %}
    <ul class="products">
      {% for product in products %}
        <li class="product card">
          {% if product.image_url %}
            <img class="product__img" src="{{ product.image_url }}" alt="{{ product.name }}" loading="lazy">
          {% else %}
            <div class="product__img product__img--empty" aria-hidden="true">🎁</div>
          {% endif %}
          <h2 class="product__name">{{ product.name }}</h2>
          {% if product.description %}<p class="product__desc">{{ product.description|linebreaksbr }}</p>{% endif %}
          <p class="product__cost"><span class="coin-mini" aria-hidden="true">★</span> {{ product.cost }} pontos</p>
          <ul class="chips">
            {% for v in product.variants.all %}
              {% if v.name != "Único" %}<li class="chip{% if not v.stock %} chip--off{% endif %}">{{ v.name }}</li>{% endif %}
            {% endfor %}
          </ul>
          <div class="product__action">
            {% if not product.total_stock %}
              <span class="btn btn--disabled">Esgotado</span>
            {% elif not user.is_authenticated %}
              <a class="btn btn--light-dark" href="{% provider_login_url 'github' process='login' %}">Entre com GitHub para resgatar</a>
            {% elif not linked %}
              <a class="btn btn--light-dark" href="{% url 'account' %}">Vincule sua conta</a>
            {% elif balance < product.cost %}
              <span class="btn btn--disabled">Você precisa de {{ product.cost }} pontos</span>
            {% else %}
              <a class="btn btn--gold" href="{% url 'shop_request' product.pk %}">Resgatar</a>
            {% endif %}
          </div>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p class="empty">Nenhum brinde disponível ainda. Volte em breve!</p>
  {% endif %}
{% endblock %}
```

`shop/templates/shop/request.html`:

```html
{% extends "base.html" %}
{% block title %}Resgatar {{ product.name }} — Loja de Traduções WPILib{% endblock %}
{% block content %}
  <p><a href="{% url 'shop_catalog' %}">← Voltar para a loja</a></p>
  <section class="card request">
    <div class="request__head">
      {% if product.image_url %}<img class="request__img" src="{{ product.image_url }}" alt="">{% endif %}
      <div>
        <h1 class="card__title">Resgatar {{ product.name }}</h1>
        <p><strong>{{ product.cost }} pontos</strong> · você tem {{ balance }} pontos</p>
      </div>
    </div>

    <form method="post" class="form" id="redeem-form">
      {% csrf_token %}
      {{ form.request_token }}
      {% if form.non_field_errors %}<ul class="errorlist">{% for e in form.non_field_errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}

      <fieldset class="choices">
        <legend>{{ form.variant.label }}</legend>
        {{ form.variant.errors }}
        {% for radio in form.variant %}<label class="choice">{{ radio.tag }} {{ radio.choice_label }}</label>{% endfor %}
      </fieldset>

      <fieldset class="choices">
        <legend>{{ form.delivery.label }}</legend>
        {{ form.delivery.errors }}
        {% for radio in form.delivery %}<label class="choice">{{ radio.tag }} {{ radio.choice_label }}</label>{% endfor %}
      </fieldset>

      <fieldset class="address" id="address-fields">
        <legend>Endereço para envio</legend>
        <div class="address__grid">
          {% for field in form %}
            {% if field.name in "full_name cep street number complement district city uf phone" %}
              <p class="address__{{ field.name }}">{{ field.label_tag }} {{ field }} {{ field.errors }}</p>
            {% endif %}
          {% endfor %}
        </div>
        <p class="helptext">O frete é pago por Pix depois que o pedido for aprovado. Seu endereço é apagado 30 dias depois da entrega.</p>
      </fieldset>

      <button class="btn btn--gold btn--big" type="submit" id="redeem-submit">Confirmar resgate</button>
    </form>
  </section>
  <script>
    (function () {
      var form = document.getElementById("redeem-form");
      var address = document.getElementById("address-fields");
      function sync() {
        var mail = form.querySelector('input[name="delivery"][value="mail"]');
        address.hidden = !(mail && mail.checked);
      }
      form.addEventListener("change", sync);
      sync();
      form.addEventListener("submit", function () {
        document.getElementById("redeem-submit").disabled = true;
      });
    })();
  </script>
{% endblock %}
```

Note: the `{% if field.name in "..." %}` substring check matches only these exact field names, because no other field name is a substring of that string. `variant`, `delivery` and `request_token` don't appear in it.

`templates/base.html` nav: before the `{% if user.is_authenticated %}`, add:
```html
        <a class="nav__link" href="{% url 'shop_catalog' %}">Loja</a>
```

`accounts/templates/accounts/home.html` step 3: replace `<p>Use seus pontos na loja para resgatar brindes da equipe. Em breve!</p>` with:
```html
        <p>Use seus pontos na <a href="{% url 'shop_catalog' %}">loja</a> para resgatar brindes da equipe.</p>
```

Append to `static/css/site.css`:

```css
/* ---------- Shop ---------- */

.products {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 20px;
}
.product { display: flex; flex-direction: column; margin: 0; }
.product__img {
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
  border-radius: 12px;
  background: var(--bg);
  margin-bottom: 12px;
}
.product__img--empty { display: grid; place-items: center; font-size: 3rem; }
.product__name { margin: 0 0 6px; font-size: 1.25rem; }
.product__desc { margin: 0 0 10px; color: var(--muted); font-size: 0.95rem; }
.product__cost { margin: 0 0 10px; font-weight: 900; font-size: 1.15rem; }
.product__action { margin-top: auto; padding-top: 8px; }
.product__action .btn { width: 100%; }
.coin-mini { color: var(--gold-dark); }
.chips { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 8px; padding: 0; }
.chip {
  padding: 2px 10px;
  border-radius: 8px;
  border: 2px solid var(--blue);
  color: var(--blue);
  font-weight: 800;
  font-size: 0.85rem;
}
.chip--off { border-color: var(--border); color: var(--muted); text-decoration: line-through; }
.btn--disabled { --btn-bg: var(--border); --btn-shadow: transparent; --btn-fg: var(--muted); cursor: not-allowed; }
.btn--light-dark { --btn-bg: var(--surface); --btn-shadow: var(--border); --btn-fg: var(--blue); border: 2px solid var(--border); }

.request__head { display: flex; gap: 16px; align-items: center; margin-bottom: 12px; }
.request__img { width: 96px; height: 96px; object-fit: cover; border-radius: 12px; }
.choices, .address { border: 2px solid var(--border); border-radius: 14px; padding: 12px 16px; margin: 0 0 16px; }
.choices legend, .address legend { font-weight: 900; padding: 0 6px; }
.choice {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 4px 14px 4px 0;
  font-weight: 700;
  cursor: pointer;
}
.address__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0 16px; }
.address__full_name, .address__street { grid-column: 1 / -1; }
select {
  padding: 10px 12px;
  border: 2px solid var(--border);
  border-radius: 12px;
  background: var(--bg);
  color: var(--text);
  font: inherit;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: catalog and redemption request pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Minha conta — redemptions, e-mail, pending link improvements

**Files:**
- Modify: `accounts/forms.py`, `accounts/views.py`, `accounts/templates/accounts/account.html`, `static/css/site.css` (append)
- Create: `tests/test_account_shop.py`

**Interfaces:**
- Consumes: `Redemption` (Task 2), URL `shop_cancel` (Task 7), `ledger.models.UnclaimedEvent`.
- Produces: `accounts.forms.EmailForm`. The account view handles `POST form=email` (save e-mail) and `form=link` (link request; the default when `form` is missing, for backward compatibility).

- [ ] **Step 1: Write the failing tests** — `tests/test_account_shop.py`:

```python
from datetime import datetime, timezone

import pytest

from accounts.models import Profile
from accounts.services import get_profile, request_link
from ledger.models import PointEntry, UnclaimedEvent
from shop import services
from tests.factories import give_points, make_product, make_user

pytestmark = pytest.mark.django_db


def test_lists_my_redemptions_with_cancel_only_when_requested(client, django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 500)
    boss = django_user_model.objects.create_user("boss", is_staff=True)
    open_r = services.request_redemption(ana, make_product(name="Caneca").variants.get().pk, "pickup")
    done_r = services.request_redemption(ana, make_product(name="Boné").variants.get().pk, "pickup")
    services.approve(done_r, boss)
    client.force_login(ana)
    html = client.get("/conta/").content.decode()
    assert "Meus resgates" in html and "Caneca" in html and "Boné" in html
    assert f"/loja/resgates/{open_r.pk}/cancelar/" in html
    assert f"/loja/resgates/{done_r.pk}/cancelar/" not in html
    assert "Aprovado" in html


def test_shows_tracking_link(client, django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 500)
    boss = django_user_model.objects.create_user("boss", is_staff=True)
    r = services.request_redemption(
        ana,
        make_product().variants.get().pk,
        "mail",
        {"full_name": "Ana Silva", "cep": "01001-000", "street": "R", "number": "1", "district": "D",
         "city": "São Paulo", "uf": "SP", "phone": "11"},
    )
    services.approve(r, boss)
    services.mark_shipping_paid(r, boss)
    services.mark_shipped(r, boss, "AA123456789BR")
    client.force_login(ana)
    assert "AA123456789BR" in client.get("/conta/").content.decode()


def test_update_email(client, django_user_model):
    ana = make_user(django_user_model, email="")
    client.force_login(ana)
    response = client.post("/conta/", {"form": "email", "email": "ana.nova@example.com"})
    assert response.status_code == 302
    ana.refresh_from_db()
    assert ana.email == "ana.nova@example.com"


def test_invalid_email_is_rejected(client, django_user_model):
    ana = make_user(django_user_model)
    client.force_login(ana)
    response = client.post("/conta/", {"form": "email", "email": "not-an-email"})
    assert response.status_code == 200
    ana.refresh_from_db()
    assert ana.email == "ana@example.com"


def test_pending_user_sees_parked_points_and_can_fix_username(client, django_user_model):
    user = make_user(django_user_model, approved=False)
    request_link(user, "alise")
    UnclaimedEvent.objects.create(
        tx_username="alise", kind=PointEntry.Kind.TRANSLATED, string_key="k1", words=10, amount=20,
        occurred_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    client.force_login(user)
    html = client.get("/conta/").content.decode()
    assert "20 pontos guardados" in html
    assert 'value="alise"' in html

    client.post("/conta/", {"form": "link", "transifex_username": "alice"})
    profile = get_profile(user)
    assert (profile.transifex_username, profile.link_status) == ("alice", Profile.LinkStatus.PENDING)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_account_shop.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`accounts/forms.py`, append:
```python
class EmailForm(forms.Form):
    email = forms.EmailField(label="E-mail para avisos dos resgates", max_length=254)
```

`accounts/views.py`, replace the `account` view and add these imports:
```python
from django.db.models import Sum

from ledger.models import UnclaimedEvent

from .forms import EmailForm, LinkForm
```
```python
@login_required
def account(request):
    profile = get_profile(request.user)
    which = request.POST.get("form", "link") if request.method == "POST" else None
    link_form = LinkForm(
        request.POST if which == "link" else None,
        initial={"transifex_username": profile.transifex_username}
        if profile.link_status == Profile.LinkStatus.PENDING
        else None,
    )
    email_form = EmailForm(request.POST if which == "email" else None, initial={"email": request.user.email})

    if which == "link" and link_form.is_valid():
        try:
            request_link(request.user, link_form.cleaned_data["transifex_username"])
        except LinkError as exc:
            link_form.add_error("transifex_username", str(exc))
        else:
            messages.success(request, "Pedido de vínculo enviado. Um admin vai aprovar em breve.")
            return redirect("account")
    if which == "email" and email_form.is_valid():
        request.user.email = email_form.cleaned_data["email"]
        request.user.save(update_fields=["email"])
        messages.success(request, "E-mail atualizado.")
        return redirect("account")

    parked_points = 0
    if profile.link_status == Profile.LinkStatus.PENDING:
        parked_points = (
            UnclaimedEvent.objects.filter(tx_username__iexact=profile.transifex_username).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
    return render(
        request,
        "accounts/account.html",
        {
            "profile": profile,
            "can_request": profile.link_status != Profile.LinkStatus.APPROVED,
            "form": link_form,
            "email_form": email_form,
            "parked_points": parked_points,
            "balance": balance(request.user),
            "entries": request.user.point_entries.all()[:50],
            "redemptions": request.user.redemptions.select_related("variant__product")[:50],
        },
    )
```

`accounts/templates/accounts/account.html`:

1. Replace the Transifex card body. Pending users now see the form prefilled, with a correction button:
```html
  <section class="card">
    <h2 class="card__title">Transifex</h2>
    {% if profile.link_status == "approved" %}
      <p><span class="pill pill--ok">Vinculado</span> como <strong>{{ profile.transifex_username }}</strong>.</p>
    {% elif profile.link_status == "pending" %}
      <p><span class="pill pill--wait">Aguardando aprovação</span> pedido de vínculo com <strong>{{ profile.transifex_username }}</strong>.</p>
      {% if parked_points %}<p class="parked">⏳ <strong>{{ parked_points }} pontos guardados</strong> aguardando aprovação.</p>{% endif %}
    {% elif profile.link_status == "rejected" %}
      <p><span class="pill pill--no">Recusado</span> Seu pedido anterior foi recusado. Confira o nome e tente de novo.</p>
    {% endif %}
    {% if can_request %}
      <form method="post" class="form">
        {% csrf_token %}<input type="hidden" name="form" value="link">
        {{ form.as_p }}
        <button class="btn btn--blue" type="submit">{% if profile.link_status == "pending" %}Corrigir nome{% else %}Pedir vínculo{% endif %}</button>
      </form>
    {% endif %}
  </section>
```

2. After the Transifex card, add:
```html
  <section class="card">
    <h2 class="card__title">Meus resgates</h2>
    {% if redemptions %}
      <ul class="redemptions">
        {% for r in redemptions %}
          <li class="redemption">
            <div class="redemption__main">
              <strong>#{{ r.pk }} {{ r.variant }}</strong>
              <span class="pill pill--status pill--{{ r.status }}">{{ r.get_status_display }}</span>
              <span class="redemption__meta">{{ r.cost }} pontos · {{ r.get_delivery_display }} · {{ r.created_at|date:"d/m/Y" }}</span>
            </div>
            {% if r.tracking_code %}<p class="redemption__line">Rastreio: <a href="{{ r.tracking_url }}">{{ r.tracking_code }}</a></p>{% endif %}
            {% if r.admin_note %}<p class="redemption__line">📝 {{ r.admin_note }}</p>{% endif %}
            {% if r.status == "requested" %}
              <form method="post" action="{% url 'shop_cancel' r.pk %}">
                {% csrf_token %}<button class="btn btn--light-dark" type="submit">Cancelar</button>
              </form>
            {% endif %}
          </li>
        {% endfor %}
      </ul>
    {% else %}
      <p class="empty">Nenhum resgate ainda. <a href="{% url 'shop_catalog' %}">Ver a loja</a></p>
    {% endif %}
  </section>

  <section class="card">
    <h2 class="card__title">E-mail</h2>
    <form method="post" class="form">
      {% csrf_token %}<input type="hidden" name="form" value="email">
      {{ email_form.as_p }}
      <button class="btn btn--blue" type="submit">Salvar e-mail</button>
    </form>
  </section>
```

Append to `static/css/site.css`:
```css
.parked { margin: 8px 0 12px; }
.redemptions { list-style: none; margin: 0; padding: 0; }
.redemption { padding: 12px 0; border-bottom: 1px solid var(--border); }
.redemption:last-child { border-bottom: 0; }
.redemption__main { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.redemption__meta { color: var(--muted); font-size: 0.9rem; }
.redemption__line { margin: 6px 0 0; }
.redemption form { margin-top: 8px; }
.pill--status { background: var(--muted); }
.pill--requested { background: var(--amber); }
.pill--approved, .pill--shipping_paid { background: var(--blue); }
.pill--shipped, .pill--ready_for_pickup { background: #7a4ff0; }
.pill--delivered { background: var(--green); }
.pill--rejected, .pill--cancelled { background: var(--red); }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass, including Plan 1's `tests/test_account_view.py`. Its POST has no `form` field, which falls back to `link`.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: Minha conta shows redemptions, editable e-mail, parked points

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Public ranking

**Files:**
- Modify: `ledger/services.py` (append), `accounts/views.py`, `accounts/urls.py`, `templates/base.html` (nav), `static/css/site.css` (append)
- Create: `accounts/templates/accounts/ranking.html`, `tests/test_ranking.py`

**Interfaces:**
- Consumes: the `PointEntry` and `Profile` models.
- Produces:
  - `ledger.services.leaderboard(since: datetime | None = None, limit: int = 100) -> list[tuple[str, int]]` (GitHub username, points earned)
  - URL name `ranking` (`/ranking/`, query `?periodo=mes`)

- [ ] **Step 1: Write the failing tests** — `tests/test_ranking.py`:

```python
from datetime import datetime, timezone

import pytest

from ledger.models import PointEntry
from ledger.services import leaderboard
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def add(user, amount, kind=PointEntry.Kind.ADJUSTMENT, when=None, key=""):
    PointEntry.objects.create(user=user, amount=amount, kind=kind, occurred_at=when, string_key=key, note="x")


def test_ranks_by_points_earned_not_balance(django_user_model):
    ana = make_user(django_user_model, "ana", "alice", "a@example.com")
    bia = make_user(django_user_model, "bia", "bob", "b@example.com")
    add(ana, 300, PointEntry.Kind.TRANSLATED, key="k1")
    add(ana, -250, PointEntry.Kind.REDEMPTION)
    add(bia, 200, PointEntry.Kind.REVIEWED, key="k2")
    add(bia, -50)  # negative adjustment does not count as earned
    add(bia, 50, PointEntry.Kind.REFUND)  # refunds are not earnings
    assert leaderboard() == [("ana", 300), ("bia", 200)]


def test_excludes_unapproved(django_user_model):
    ana = make_user(django_user_model, approved=False)
    add(ana, 100)
    assert leaderboard() == []


def test_since_uses_occurred_at_then_created_at(django_user_model):
    ana = make_user(django_user_model)
    add(ana, 100, PointEntry.Kind.TRANSLATED, when=datetime(2026, 10, 15, tzinfo=timezone.utc), key="old")
    add(ana, 40, PointEntry.Kind.TRANSLATED, when=datetime(2026, 11, 3, tzinfo=timezone.utc), key="new")
    assert leaderboard(since=datetime(2026, 11, 1, tzinfo=timezone.utc)) == [("ana", 40)]


def test_ranking_page_and_toggle(client, django_user_model):
    ana = make_user(django_user_model)
    add(ana, 120)
    html = client.get("/ranking/").content.decode()
    assert "ana" in html and "120" in html
    assert "?periodo=mes" in html
    assert client.get("/ranking/?periodo=mes").status_code == 200


def test_empty_ranking(client):
    assert "Ninguém pontuou" in client.get("/ranking/").content.decode()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ranking.py -q`
Expected: FAIL with `ImportError: cannot import name 'leaderboard'`.

- [ ] **Step 3: Implement**

`ledger/services.py`, update the imports to:
```python
from datetime import datetime

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
```
Then append:
```python
EARNING_KINDS = (PointEntry.Kind.TRANSLATED, PointEntry.Kind.REVIEWED, PointEntry.Kind.ADJUSTMENT)


def leaderboard(since: datetime | None = None, limit: int = 100) -> list[tuple[str, int]]:
    """Points earned (positive translated/reviewed/adjustment entries) by approved translators."""
    entries = PointEntry.objects.filter(
        kind__in=EARNING_KINDS, amount__gt=0, user__profile__link_status=Profile.LinkStatus.APPROVED
    )
    if since is not None:
        entries = entries.annotate(earned_at=Coalesce("occurred_at", "created_at")).filter(earned_at__gte=since)
    rows = (
        entries.values("user__username")
        .annotate(points=Sum("amount"))
        .order_by("-points", "user__username")[:limit]
    )
    return [(row["user__username"], row["points"]) for row in rows]
```

`accounts/views.py`, add these imports:
```python
from django.utils import timezone

from ledger.services import balance, leaderboard
```
Then add the view:
```python
def ranking(request):
    monthly = request.GET.get("periodo") == "mes"
    since = None
    if monthly:
        since = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return render(request, "accounts/ranking.html", {"rows": leaderboard(since), "monthly": monthly})
```

`accounts/urls.py`: add `path("ranking/", views.ranking, name="ranking"),`.

`templates/base.html` nav: after the Loja link, add `<a class="nav__link" href="{% url 'ranking' %}">Ranking</a>`.

`accounts/templates/accounts/ranking.html`:
```html
{% extends "base.html" %}
{% block title %}Ranking — Loja de Traduções WPILib{% endblock %}
{% block content %}
  <div class="account-head">
    <h1>Ranking</h1>
    <nav class="toggle" aria-label="Período">
      <a class="toggle__opt{% if not monthly %} toggle__opt--on{% endif %}" href="?">Total</a>
      <a class="toggle__opt{% if monthly %} toggle__opt--on{% endif %}" href="?periodo=mes">Este mês</a>
    </nav>
  </div>
  {% if rows %}
    <ol class="ranking card">
      {% for username, points in rows %}
        <li class="ranking__row{% if forloop.counter <= 3 %} ranking__row--top{% endif %}">
          <span class="ranking__pos">{% if forloop.counter == 1 %}🥇{% elif forloop.counter == 2 %}🥈{% elif forloop.counter == 3 %}🥉{% else %}{{ forloop.counter }}{% endif %}</span>
          <span class="ranking__name">{{ username }}</span>
          <span class="ranking__points">{{ points }} pts</span>
        </li>
      {% endfor %}
    </ol>
  {% else %}
    <p class="empty">Ninguém pontuou {% if monthly %}este mês{% else %}ainda{% endif %}. Que tal ser o primeiro?</p>
  {% endif %}
{% endblock %}
```

Append to `static/css/site.css`:
```css
.toggle { display: inline-flex; background: var(--surface); border: 2px solid var(--border); border-radius: 999px; padding: 4px; }
.toggle__opt { padding: 6px 14px; border-radius: 999px; text-decoration: none; color: var(--muted); }
.toggle__opt--on { background: var(--blue); color: #fff; }
.ranking { list-style: none; padding: 8px 22px; }
.ranking__row { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.ranking__row:last-child { border-bottom: 0; }
.ranking__row--top { font-size: 1.15rem; }
.ranking__pos { width: 2.2em; text-align: center; font-weight: 900; color: var(--muted); }
.ranking__name { flex: 1; font-weight: 800; }
.ranking__points { font-weight: 900; color: var(--gold-dark); }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: public ranking by points earned, total and this month

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Cron — address erasure, sync locking, claim de-duplication

**Files:**
- Create: `shop/maintenance.py`, `tests/test_maintenance.py`
- Modify: `transifex/views.py`, `transifex/sync.py`, `ledger/services.py` (`claim_unclaimed`), `tests/test_cron.py` (assert new key)

**Interfaces:**
- Consumes: `Redemption` (Task 2) and the existing `sync_resource` / `claim_unclaimed`.
- Produces:
  - `shop.maintenance.erase_old_addresses(now: datetime) -> int`
  - `shop.maintenance.ADDRESS_RETENTION = timedelta(days=30)`
  - `transifex.sync.ResourceBusy(Exception)`
  - `/cron/sync/` JSON gains `addresses_erased: int`

- [ ] **Step 1: Write the failing tests** — `tests/test_maintenance.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from ledger.models import PointEntry, UnclaimedEvent
from ledger.services import claim_unclaimed
from shop.maintenance import erase_old_addresses
from shop.models import Redemption
from tests.factories import make_product, make_redemption, make_user

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)


def test_erases_addresses_delivered_more_than_30_days_ago(django_user_model):
    ana = make_user(django_user_model)
    old = make_redemption(ana, make_product(name="A"), status="delivered", delivered_at=NOW - timedelta(days=31), tracking_code="BR1")
    recent = make_redemption(ana, make_product(name="B"), status="delivered", delivered_at=NOW - timedelta(days=29))
    open_one = make_redemption(ana, make_product(name="C"), status="shipped")

    assert erase_old_addresses(NOW) == 1

    old.refresh_from_db()
    assert all(getattr(old, f) == "" for f in Redemption.ADDRESS_FIELDS)
    assert old.address_erased_at == NOW and old.tracking_code == "BR1"
    assert Redemption.objects.get(pk=recent.pk).full_name == "Ana Silva"
    assert Redemption.objects.get(pk=open_one.pk).full_name == "Ana Silva"
    assert erase_old_addresses(NOW) == 0


def test_claim_skips_events_already_credited(django_user_model):
    ana = make_user(django_user_model, approved=False)
    when = datetime(2026, 11, 2, tzinfo=timezone.utc)
    PointEntry.objects.create(user=ana, amount=20, kind=PointEntry.Kind.TRANSLATED, string_key="dup", words=10)
    UnclaimedEvent.objects.create(tx_username="alice", kind=PointEntry.Kind.TRANSLATED, string_key="dup", words=10, amount=20, occurred_at=when)
    UnclaimedEvent.objects.create(tx_username="alice", kind=PointEntry.Kind.TRANSLATED, string_key="new", words=5, amount=10, occurred_at=when)

    assert claim_unclaimed(ana, "alice") == 1
    assert PointEntry.objects.filter(string_key="dup").count() == 1
    assert not UnclaimedEvent.objects.exists()
```

In `tests/test_cron.py`, inside `test_tracks_resources_syncs_and_reports`, add `assert body["addresses_erased"] == 0`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_maintenance.py tests/test_cron.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`shop/maintenance.py`:
```python
from datetime import datetime, timedelta

from .models import Redemption

ADDRESS_RETENTION = timedelta(days=30)


def erase_old_addresses(now: datetime) -> int:
    """LGPD: blank shipping addresses 30 days after delivery; keep status, tracking and history."""
    stale = Redemption.objects.filter(delivered_at__lt=now - ADDRESS_RETENTION, address_erased_at__isnull=True)
    return stale.update(**{name: "" for name in Redemption.ADDRESS_FIELDS}, address_erased_at=now)
```

`transifex/views.py`: add the import `from shop.maintenance import erase_old_addresses`. Then, after `body.update(asdict(result))`, add:
```python
    body["addresses_erased"] = erase_old_addresses(timezone.now())
```

`ledger/services.py` `claim_unclaimed`: replace its body with:
```python
def claim_unclaimed(user, tx_username: str) -> int:
    with transaction.atomic():
        events = list(UnclaimedEvent.objects.select_for_update().filter(tx_username__iexact=tx_username))
        already = set(
            PointEntry.objects.filter(string_key__in=[e.string_key for e in events]).values_list("string_key", "kind")
        )
        fresh = [e for e in events if (e.string_key, e.kind) not in already]
        PointEntry.objects.bulk_create(
            PointEntry(
                user=user,
                amount=e.amount,
                kind=e.kind,
                string_key=e.string_key,
                words=e.words,
                occurred_at=e.occurred_at,
            )
            for e in fresh
        )
        UnclaimedEvent.objects.filter(pk__in=[e.pk for e in events]).delete()
    return len(fresh)
```

`transifex/sync.py`:
- Add `class ResourceBusy(Exception): """Another sync run holds this resource."""` near `SyncResult`.
- In `sync_resource`, make the first statement inside `with transaction.atomic():`:
  ```python
        # Overlapping cron runs: skip a resource another run is already writing.
        if not TrackedResource.objects.select_for_update(skip_locked=True).filter(pk=tracked.pk).values_list("pk", flat=True)[:1]:
            raise ResourceBusy(tracked.resource_id)
  ```
- In `sync_all`, before the generic `except Exception`, add:
  ```python
        except ResourceBusy:
            result.resources_skipped += 1
  ```

On SQLite `select_for_update` is a no-op, so the busy path is tested on Postgres in Task 11.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: erase delivered addresses after 30 days; lock resources during sync; dedupe claims

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: GitHub e-mail scope, Postgres concurrency tests, docs

**Files:**
- Modify: `store/settings.py` (SCOPE), `pyproject.toml` (pytest marker), `CLAUDE.md`, `README.md`
- Create: `tests/test_concurrency.py`, `tests/test_scope.py`

**Interfaces:**
- Consumes: `shop.services.request_redemption`, `transifex.sync.sync_resource`, `ResourceBusy`.
- Produces: the pytest marker `postgres`. The docs commands.

- [ ] **Step 1: Write the tests**

`tests/test_scope.py`:
```python
from django.conf import settings


def test_github_login_requests_email_scope():
    assert settings.SOCIALACCOUNT_PROVIDERS["github"]["SCOPE"] == ["read:user", "user:email"]
```

`tests/test_concurrency.py`:
```python
import threading
from datetime import datetime, timezone

import pytest
from django.db import connection, transaction

from ledger.services import balance
from shop import services
from shop.models import Redemption, Variant
from tests.factories import give_points, make_product, make_user
from transifex.models import TrackedResource
from transifex.sync import ResourceBusy, sync_resource

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def require_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("row-lock behaviour needs PostgreSQL (set DATABASE_URL to a Postgres)")


def run_together(*callables):
    barrier = threading.Barrier(len(callables))
    outcomes = [None] * len(callables)

    def worker(index, fn):
        try:
            barrier.wait()
            outcomes[index] = ("ok", fn())
        except Exception as exc:
            outcomes[index] = ("error", exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(callables)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def test_last_unit_goes_to_exactly_one(django_user_model):
    ana = make_user(django_user_model, "ana", "alice", "a@example.com")
    bia = make_user(django_user_model, "bia", "bob", "b@example.com")
    give_points(ana, 500)
    give_points(bia, 500)
    variant = make_product(cost=100, variants=(("M", 1),)).variants.get()

    outcomes = run_together(
        lambda: services.request_redemption(ana, variant.pk, "pickup"),
        lambda: services.request_redemption(bia, variant.pk, "pickup"),
    )

    assert sorted(kind for kind, _ in outcomes) == ["error", "ok"]
    error = next(value for kind, value in outcomes if kind == "error")
    assert "Esgotou" in str(error)
    assert Variant.objects.get(pk=variant.pk).stock == 0
    assert Redemption.objects.count() == 1


def test_parallel_requests_cannot_overspend(django_user_model):
    ana = make_user(django_user_model)
    give_points(ana, 150)
    product_a = make_product(name="A", cost=100, variants=(("M", 5),)).variants.get()
    product_b = make_product(name="B", cost=100, variants=(("M", 5),)).variants.get()

    outcomes = run_together(
        lambda: services.request_redemption(ana, product_a.pk, "pickup"),
        lambda: services.request_redemption(ana, product_b.pk, "pickup"),
    )

    assert sorted(kind for kind, _ in outcomes) == ["error", "ok"]
    assert balance(ana) == 50


def test_sync_skips_a_resource_locked_by_another_run():
    tracked = TrackedResource.objects.create(resource_id="r1")
    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with transaction.atomic():
            list(TrackedResource.objects.select_for_update().filter(pk=tracked.pk))
            locked.set()
            release.wait(10)
        connection.close()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    locked.wait(10)

    class EmptySource:
        def events_for(self, resource_id, since):
            return iter(())

    try:
        with pytest.raises(ResourceBusy):
            sync_resource(tracked, EmptySource(), datetime(2026, 11, 10, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))
    finally:
        release.set()
        holder.join(10)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_scope.py -q`
Expected: FAIL (the scope is `["read:user"]`).

- [ ] **Step 3: Implement**

`store/settings.py`: change `"SCOPE": ["read:user"],` to `"SCOPE": ["read:user", "user:email"],`.

`pyproject.toml`, under `[tool.pytest.ini_options]`, add:
```toml
markers = ["postgres: needs a real PostgreSQL for row-lock behaviour (skipped on SQLite)"]
```

- [ ] **Step 4: Run the Postgres tests locally (Docker)**

```bash
docker run -d --rm --name store-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 postgres:17
# wait until ready
until docker exec store-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
DATABASE_URL=postgres://postgres:pg@localhost:55432/postgres uv run pytest -m postgres -v
DATABASE_URL=postgres://postgres:pg@localhost:55432/postgres uv run pytest -q
docker stop store-pg
```
Expected: the 3 `postgres` tests pass, and the full suite passes on Postgres too. Then run `uv run pytest -q` on SQLite: everything passes, with the 3 `postgres` tests skipped.

- [ ] **Step 5: Docs**

`CLAUDE.md`:
- Update "Project status" to say that Plans 1 and 2 are implemented.
- Add these bullets under "Architecture rules that span multiple apps":
  ```markdown
  - **`shop.services` owns every redemption state change.** Each runs in one transaction with `select_for_update` (variant + user row on request; redemption on transitions) and writes a `RedemptionEvent`. Points move only via ledger entries (`REDEMPTION` −cost, `REFUND` +cost). Never edit `Redemption.status` directly.
  - **E-mail is fire-after-commit** (`shop.notify.after_commit`). Failures are recorded as `RedemptionEvent` notes, never raised.
  - **Photos** go to a public Vercel Blob store via `shop.blob.upload_product_image` (needs `BLOB_READ_WRITE_TOKEN`); the model only stores `image_url`.
  ```
- Add to "Commands":
  ```markdown
  - Postgres-only concurrency tests: `docker run -d --rm --name store-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 postgres:17` then `DATABASE_URL=postgres://postgres:pg@localhost:55432/postgres uv run pytest -m postgres` (skipped on SQLite)
  ```
- Under "Deployment", add the env vars `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (Gmail app password) and `BLOB_READ_WRITE_TOKEN` (from the Blob store), and note that the daily cron also erases addresses delivered more than 30 days ago.

`README.md`: add a section **"Loja e resgates"** in pt-BR, covering:
- products and variants are registered in the admin (Loja → Produtos)
- the Pix text for shipping is set in "Configurações da loja"
- the order steps
- that admins receive e-mail and approve through the actions on the Resgates list

- [ ] **Step 6: Run all tests and commit**

```bash
uv run pytest -q
git add -A && git diff --cached -- . ':!uv.lock' | grep -nE '1/[a-f0-9]{30,}|gh[pousr]_[A-Za-z0-9]{20,}'
git commit -m "feat: GitHub e-mail scope, Postgres concurrency tests, docs for the store

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Production rollout (controller-run, needs the user)

This task touches the user's accounts. The controller runs it after the final review, not a subagent.

- [ ] **Step 1: Blob store.** Create a **public** Blob store connected to the project. If the CLI offers it, run `vercel blob store add` (check `vercel blob --help` first). Otherwise, ask the user to create it in the dashboard: Storage → Create → Blob → Public, connected to Production, Preview and Development. Confirm that `BLOB_READ_WRITE_TOKEN` shows up in `vercel env ls production`, by name only.
- [ ] **Step 2: Gmail.** Ask the user to:
  1. turn on 2-step verification;
  2. create an App Password at https://myaccount.google.com/apppasswords;
  3. add it in their **own terminal** (not `!`) with `vercel env add EMAIL_HOST_PASSWORD production --sensitive`.

  The controller sets `EMAIL_HOST_USER` (non-secret) with the address the user gives.
- [ ] **Step 3: Merge and deploy.** Merge `plan-2-store` into `main` and push. Then run `vercel deploy --prod --yes`, and confirm in the build log that the `shop` migrations ran.
- [ ] **Step 4: Smoke test.** Check that `/loja/` and `/ranking/` return 200 and that `/static/css/site.css` is served. In `/admin/`, create a test product with a photo, check that its `image_url` points at `*.public.blob.vercel-storage.com`, then deactivate it. Trigger `vercel crons run /cron/sync/` and check that the log line includes `addresses_erased`.
- [ ] **Step 5: E-mail check.** Ask the user to set their own e-mail in Minha conta. Then have them redeem a 1-point test product (pickup), approve it in the admin, and confirm both e-mails arrived (admin "novo resgate" and translator "aprovado"). Finally, cancel or reject the test redemption so the points come back.
