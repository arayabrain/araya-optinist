"""CORS_ORIGINS narrows the allowed origins; unset or "*" keeps the old wildcard."""

import pytest

from studio.app.common.core.middleware.cors_config import get_cors_allow_origins


@pytest.mark.parametrize("raw", ["", "*", " * ", "https://a.example, *"])
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
