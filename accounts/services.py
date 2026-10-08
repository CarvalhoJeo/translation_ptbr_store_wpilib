import re

from .models import Profile

_USERNAME_RE = re.compile(r"^[\w.@+-]+$")


class LinkError(Exception):
    pass


def normalize_username(raw: str) -> str:
    name = raw.strip().rstrip("/")
    if "/" in name:
        name = name.rsplit("/", 1)[1]
    name = name.removeprefix("u:").removeprefix("@")
    if not name or not _USERNAME_RE.match(name):
        raise LinkError("Nome de usuário do Transifex inválido.")
    return name


def get_profile(user) -> Profile:
    return Profile.objects.get_or_create(user=user)[0]


def request_link(user, raw_username: str) -> Profile:
    name = normalize_username(raw_username)
    profile = get_profile(user)
    if profile.link_status == Profile.LinkStatus.APPROVED:
        raise LinkError("Seu vínculo já foi aprovado; peça a um admin para alterar.")
    taken = (
        Profile.objects.filter(link_status=Profile.LinkStatus.APPROVED, transifex_username__iexact=name)
        .exclude(pk=profile.pk)
        .exists()
    )
    if taken:
        raise LinkError("Este usuário do Transifex já está vinculado a outra conta.")
    profile.transifex_username = name
    profile.link_status = Profile.LinkStatus.PENDING
    profile.save()
    return profile
