import pytest
import responses

from accounts.services import approve_link, request_link
from ledger.services import balance
from transifex.models import TrackedResource

pytestmark = pytest.mark.django_db

API = "https://rest.api.transifex.com"
RES = "o:wpilib:p:frc-docs:r:demo"


@pytest.fixture
def cron_settings(settings):
    settings.CRON_SECRET = "s3cret-value-for-tests"
    settings.TRANSIFEX_API_TOKEN = "tok"
    return settings


def test_rejects_request_without_secret(client, cron_settings):
    assert client.get("/cron/sync/").status_code == 401


def test_rejects_wrong_secret(client, cron_settings):
    response = client.get("/cron/sync/", HTTP_AUTHORIZATION="Bearer wrong")
    assert response.status_code == 401


def test_rejects_everything_when_cron_secret_unset(client, settings):
    settings.CRON_SECRET = ""
    assert client.get("/cron/sync/", HTTP_AUTHORIZATION="Bearer ").status_code == 401


def test_only_get_is_allowed(client, cron_settings):
    response = client.post("/cron/sync/", HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests")
    assert response.status_code == 405


@responses.activate
def test_tracks_resources_syncs_and_reports(client, cron_settings, django_user_model):
    user = django_user_model.objects.create_user("ana")
    approve_link(request_link(user, "alice"))
    responses.get(f"{API}/resources", json={"data": [{"id": RES}], "links": {"next": None}})
    translated = {
        "id": f"{RES}:s:1:l:pt",
        "attributes": {"datetime_translated": "2026-11-02T10:00:00Z", "datetime_reviewed": None},
        "relationships": {
            "translator": {"data": {"type": "users", "id": "u:alice"}},
            "reviewer": None,
            "resource_string": {"data": {"type": "resource_strings", "id": f"{RES}:s:1"}},
        },
    }
    source_string = {"type": "resource_strings", "id": f"{RES}:s:1", "attributes": {"strings": {"other": "Hello world"}}}
    # first call: translated pass; second call: reviewed pass
    responses.get(f"{API}/resource_translations", json={"data": [translated], "included": [source_string], "links": {"next": None}})
    responses.get(f"{API}/resource_translations", json={"data": [], "links": {"next": None}})

    response = client.get("/cron/sync/", HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests")

    assert response.status_code == 200
    body = response.json()
    assert body["resources_added"] == 1
    assert (body["resources_ok"], body["resources_failed"], body["new_events"]) == (1, 0, 1)
    assert TrackedResource.objects.get().resource_id == RES
    assert balance(user) == 4  # 2 words × 2 points
    assert body["addresses_erased"] == 0


@responses.activate
def test_resource_listing_failure_still_syncs_known_resources(client, cron_settings):
    TrackedResource.objects.create(resource_id=RES)
    responses.get(f"{API}/resources", status=403, json={"errors": [{"detail": "forbidden"}]})
    responses.get(f"{API}/resource_translations", json={"data": [], "links": {"next": None}})

    body = client.get("/cron/sync/", HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests").json()

    assert "403" in body["track_error"]
    assert body["resources_ok"] == 1


def test_missing_transifex_token_is_a_server_error(client, cron_settings):
    cron_settings.TRANSIFEX_API_TOKEN = ""
    response = client.get("/cron/sync/", HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests")
    assert response.status_code == 500
