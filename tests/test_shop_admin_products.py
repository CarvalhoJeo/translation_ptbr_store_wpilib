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
