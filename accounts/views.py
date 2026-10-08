from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from ledger.models import PointRates
from ledger.services import balance

from .forms import LinkForm
from .models import Profile
from .services import LinkError, get_profile, request_link


def home(request):
    return render(request, "accounts/home.html", {"rates": PointRates.current()})


@login_required
def account(request):
    profile = get_profile(request.user)
    form = LinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request_link(request.user, form.cleaned_data["transifex_username"])
        except LinkError as exc:
            form.add_error("transifex_username", str(exc))
        else:
            messages.success(request, "Pedido de vínculo enviado. Um admin vai aprovar em breve.")
            return redirect("account")
    return render(
        request,
        "accounts/account.html",
        {
            "profile": profile,
            "can_request": profile.link_status in (Profile.LinkStatus.NONE, Profile.LinkStatus.REJECTED),
            "form": form,
            "balance": balance(request.user),
            "entries": request.user.point_entries.all()[:50],
        },
    )
