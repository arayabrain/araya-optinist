"""CORS_ORIGINS narrows the allowed origins; unset or "*" keeps the old wildcard."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from studio.app.common.core.middleware.cors_config import (
    get_cors_allow_origins,
    get_cors_origin_options,
)


@pytest.mark.parametrize("raw", ["", "*", " * ", '"*"', "'*'", "https://a.example, *"])
def test_wildcard_when_unset_or_star(raw):
    assert get_cors_allow_origins(raw) == ["*"]


def test_comma_separated_list_is_split_and_trimmed():
    raw = " https://app.example.org ,https://admin.example.org,, "

    assert get_cors_allow_origins(raw) == [
        "https://app.example.org",
        "https://admin.example.org",
    ]


def test_reads_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.org")

    assert get_cors_allow_origins() == ["https://app.example.org"]


def test_defaults_to_wildcard_without_environment(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert get_cors_allow_origins() == ["*"]


def test_quotes_and_trailing_slash_are_removed():
    """Browsers send Origin without them, so they would never match."""
    raw = "\"https://app.example.org/\", 'https://admin.example.org'"

    assert get_cors_allow_origins(raw) == [
        "https://app.example.org",
        "https://admin.example.org",
    ]


def _client():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, **get_cors_origin_options(), allow_credentials=True
    )
    app.post("/auth/login")(lambda: {})
    return TestClient(app)


def test_wildcard_echoes_the_origin_of_a_cookieless_credentialed_request(
    monkeypatch,
):
    """Browsers drop a credentialed response that says "*"."""
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    response = _client().post(
        "/auth/login", headers={"origin": "http://localhost:3000"}
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_a_narrowed_list_echoes_only_its_own_origins(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.org")
    client = _client()

    allowed = client.post("/auth/login", headers={"origin": "https://app.example.org"})
    foreign = client.post("/auth/login", headers={"origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "https://app.example.org"
    assert "access-control-allow-origin" not in foreign.headers
