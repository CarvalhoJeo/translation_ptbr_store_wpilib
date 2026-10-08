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
    if not product.variants.filter(active=True, stock__gt=0).exists():
        messages.error(request, "Este brinde esgotou.")
        return redirect("shop_catalog")
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
