import pytest

from accounts.models import Profile
from accounts.services import LinkError, get_profile, normalize_username, request_link


@pytest.mark.parametrize(
    "raw",
    ["alice", "  alice  ", "u:alice", "@alice", "https://app.transifex.com/user/profile/alice/"],
)
def test_normalize_username_accepts_common_forms(raw):
    assert normalize_username(raw) == "alice"


def test_normalize_username_keeps_dots_and_underscores():
    assert normalize_username("_.miguel_sr") == "_.miguel_sr"


@pytest.mark.parametrize("raw", ["", "   ", "two words", "a/b c"])
def test_normalize_username_rejects_invalid(raw):
    with pytest.raises(LinkError):
        normalize_username(raw)


@pytest.mark.django_db
def test_request_link_sets_pending(django_user_model):
    user = django_user_model.objects.create_user("ana")
    profile = request_link(user, "u:Alice")
    assert profile.transifex_username == "Alice"
    assert profile.link_status == Profile.LinkStatus.PENDING


@pytest.mark.django_db
def test_request_link_rejects_username_approved_for_someone_else(django_user_model):
    owner = django_user_model.objects.create_user("owner")
    owner_profile = get_profile(owner)
    owner_profile.transifex_username = "alice"
    owner_profile.link_status = Profile.LinkStatus.APPROVED
    owner_profile.save()

    other = django_user_model.objects.create_user("other")
    with pytest.raises(LinkError):
        request_link(other, "ALICE")


@pytest.mark.django_db
def test_request_link_refuses_to_change_an_approved_link(django_user_model):
    user = django_user_model.objects.create_user("ana")
    profile = get_profile(user)
    profile.transifex_username = "alice"
    profile.link_status = Profile.LinkStatus.APPROVED
    profile.save()
    with pytest.raises(LinkError):
        request_link(user, "bob")
