import json
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"


class NetworkAccessError(RuntimeError):
    """Raised when a test tries to make a real HTTP request."""


def _blocked(caller, url_index):
    def fail(*args, **kwargs):
        if "url" in kwargs:
            target = kwargs["url"]
        elif len(args) > url_index:
            target = args[url_index]
        else:
            target = "<unknown url>"
        raise NetworkAccessError(
            f"Test attempted a real network call via {caller} to {target!r}. "
            "Mock the response instead of hitting the live job board."
        )

    return fail


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly on any outbound HTTP request from a test."""
    monkeypatch.setattr(
        requests.Session, "request", _blocked("requests.Session.request", 2)
    )
    for name in ("get", "post", "put", "patch", "delete", "head", "options"):
        monkeypatch.setattr(requests, name, _blocked(f"requests.{name}", 0))
    monkeypatch.setattr(requests, "request", _blocked("requests.request", 1))


class FakeResponse:
    def __init__(self, payload, status_code=200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self.payload


@pytest.fixture
def fake_get(monkeypatch):
    calls = []

    def install(payload, status_code=200):
        def get(url: str, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(payload, status_code)

        monkeypatch.setattr(requests, "get", get)
        return calls

    return install


@pytest.fixture
def load_fixture():
    return lambda name: json.loads((FIXTURES / name).read_text())
