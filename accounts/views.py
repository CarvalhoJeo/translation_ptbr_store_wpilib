from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from ledger.models import PointRates, UnclaimedEvent
from ledger.services import balance, leaderboard

from .forms import EmailForm, LinkForm
from .models import Profile
from .services import LinkError, get_profile, request_link


def home(request):
    return render(request, "accounts/home.html", {"rates": PointRates.current()})


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


def ranking(request):
    monthly = request.GET.get("periodo") == "mes"
    since = None
    if monthly:
        since = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return render(request, "accounts/ranking.html", {"rows": leaderboard(since), "monthly": monthly})
