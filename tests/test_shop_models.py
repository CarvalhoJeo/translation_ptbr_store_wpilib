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
