import pytest
import responses
from responses import matchers

from transifex.client import TransifexClient, TransifexError

URL = "https://rest.api.transifex.com/resource_translations"
RES = "o:wpilib:p:frc-docs:r:demo"


def item(n, user="alice"):
    return {
        "id": f"{RES}:s:{n}:l:pt",
        "attributes": {"origin": "EDITOR", "datetime_translated": "2026-11-02T10:00:00Z"},
        "relationships": {
            "translator": {"data": {"type": "users", "id": f"u:{user}"}},
            "reviewer": None,
            "resource_string": {"data": {"type": "resource_strings", "id": f"{RES}:s:{n}"}},
        },
    }


def included(n, text):
    return {"type": "resource_strings", "id": f"{RES}:s:{n}", "attributes": {"strings": {"other": text}}}


@responses.activate
def test_iter_translations_follows_next_and_maps_source_strings():
    first_params = {"filter[resource]": RES, "filter[language]": "l:pt", "include": "resource_string", "filter[translated]": "true"}
    responses.get(
        URL,
        match=[matchers.query_param_matcher(first_params)],
        json={"data": [item(1)], "included": [included(1, "Hello world")], "links": {"next": f"{URL}?page=2"}},
    )
    responses.get(
        URL,
        match=[matchers.query_param_matcher({"page": "2"})],
        json={"data": [item(2, "bob")], "included": [included(2, "Bye")], "links": {"next": None}},
    )
    client = TransifexClient("tok", sleep=lambda s: None)

    rows = list(client.iter_translations(RES, "l:pt", {"filter[translated]": "true"}))

    assert [r[0]["id"] for r in rows] == [f"{RES}:s:1:l:pt", f"{RES}:s:2:l:pt"]
    assert rows[0][1] == {"other": "Hello world"}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_retries_on_429_then_succeeds():
    responses.get(URL, status=429)
    responses.get(URL, json={"data": [], "links": {"next": None}})
    sleeps = []
    client = TransifexClient("tok", sleep=sleeps.append)

    assert list(client.iter_translations(RES, "l:pt", {})) == []
    assert sleeps == [1]


@responses.activate
def test_403_raises_without_retry():
    responses.get(URL, status=403, json={"errors": [{"detail": "Your plan does not support"}]})
    client = TransifexClient("tok", sleep=lambda s: pytest.fail("should not retry"))

    with pytest.raises(TransifexError, match="403"):
        list(client.iter_translations(RES, "l:pt", {}))


@responses.activate
def test_gives_up_after_max_retries():
    for _ in range(4):
        responses.get(URL, status=503)
    client = TransifexClient("tok", sleep=lambda s: None, max_retries=3)

    with pytest.raises(TransifexError, match="503"):
        list(client.iter_translations(RES, "l:pt", {}))


@responses.activate
def test_iter_resources_yields_ids():
    responses.get(
        "https://rest.api.transifex.com/resources",
        match=[matchers.query_param_matcher({"filter[project]": "o:wpilib:p:frc-docs"})],
        json={"data": [{"id": f"{RES}1"}, {"id": f"{RES}2"}], "links": {"next": None}},
    )
    client = TransifexClient("tok")
    assert list(client.iter_resources("o:wpilib:p:frc-docs")) == [f"{RES}1", f"{RES}2"]


@responses.activate
def test_non_json_200_raises_transifex_error():
    responses.get(URL, body="<html>oops</html>", status=200)
    client = TransifexClient("tok", sleep=lambda s: None)
    with pytest.raises(TransifexError, match="não-JSON"):
        list(client.iter_translations(RES, "l:pt", {}))
