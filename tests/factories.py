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
