import time
from collections.abc import Iterator

import requests

BASE_URL = "https://rest.api.transifex.com"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class TransifexError(Exception):
    pass


class TransifexClient:
    def __init__(self, token: str, *, session=None, sleep=time.sleep, max_retries: int = 3):
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json"}
        )
        self._sleep = sleep
        self._max_retries = max_retries

    def _get(self, url: str, params: dict | None) -> dict:
        for attempt in range(self._max_retries + 1):
            last_attempt = attempt == self._max_retries
            try:
                resp = self._session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                if last_attempt:
                    raise TransifexError(f"falha de rede em {url}: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and not last_attempt:
                self._sleep(2**attempt)
                continue
            if resp.status_code != 200:
                raise TransifexError(f"HTTP {resp.status_code} em {resp.url}: {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise TransifexError(f"resposta não-JSON em {resp.url}") from exc
        raise AssertionError("unreachable")

    def _paginate(self, path: str, params: dict) -> Iterator[dict]:
        url, query = BASE_URL + path, params
        while url:
            body = self._get(url, query)
            yield body
            url, query = (body.get("links") or {}).get("next"), None

    def iter_resources(self, project_id: str) -> Iterator[str]:
        for body in self._paginate("/resources", {"filter[project]": project_id}):
            for item in body["data"]:
                yield item["id"]

    def iter_translations(
        self, resource_id: str, language: str, filters: dict[str, str]
    ) -> Iterator[tuple[dict, dict[str, str]]]:
        params = {
            "filter[resource]": resource_id,
            "filter[language]": language,
            "include": "resource_string",
            **filters,
        }
        for body in self._paginate("/resource_translations", params):
            sources = {
                inc["id"]: inc["attributes"]["strings"]
                for inc in body.get("included", [])
                if inc["type"] == "resource_strings"
            }
            for item in body["data"]:
                string_id = item["relationships"]["resource_string"]["data"]["id"]
                yield item, sources.get(string_id) or {}
