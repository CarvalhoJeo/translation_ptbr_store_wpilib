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
    assert "Confira o endereço de envio." in html
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


def test_pickup_ignores_junk_address_input(client, ana):
    product = make_product(cost=100)
    client.force_login(ana)
    data = {
        "variant": product.variants.get().pk,
        "delivery": "pickup",
        "request_token": str(uuid.uuid4()),
        "cep": "123",
        "uf": "XX",
    }
    response = client.post(f"/loja/{product.pk}/resgatar/", data)
    assert response.status_code == 302
    assert Redemption.objects.get().delivery == "pickup"


def test_sold_out_product_redirects_to_catalog(client, ana):
    product = make_product(variants=(("P", 0), ("M", 0)))
    client.force_login(ana)
    response = client.get(f"/loja/{product.pk}/resgatar/")
    assert response.status_code == 302 and response["Location"] == "/loja/"
    followed = client.get(f"/loja/{product.pk}/resgatar/", follow=True)
    assert "Este brinde esgotou." in followed.content.decode()
