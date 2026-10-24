"""
Tests for app/security_headers.py.

The CSP has its own reasoning in the module docstring and is exercised end to end by
the route tests. What is asserted here is the part with no other home: **caching**.

`no-store` on the API is a security property, not a performance one. Those responses
carry access tokens, usernames and provider hints, and a cached copy is one that
outlives the session on a shared machine or in an intermediary. `no-cache` on the static
app is the opposite concern — it means "cache it, but always ask" — and exists because
this app has no build step and no content-hashed filenames, so a stale module is
indistinguishable from a bug.
"""

import pytest

from app import config, security_headers


@pytest.fixture(autouse=True)
def loaded_config(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("server:\n  name: weft\n", encoding="utf-8")
    config.load_config(str(path), force=True)


@pytest.mark.parametrize("path", [
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/providers",
    "/api/wefts",
    "/api/chat",
    "/api/speech",
    "/ws",
])
def test_api_responses_are_never_stored(path):
    """An access token in a cached response body outlives the session that earned it."""
    assert security_headers.headers_for(path)["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/",
    "/index.html",
    "/js/main.js",
    "/js/views/chat.js",
    "/css/app.css",
    "/manifest.json",
])
def test_the_static_app_is_cached_but_always_revalidated(path):
    """`no-cache` stores the file and asks before reusing it.

    Not `no-store`, which would re-download the whole client on every navigation. With
    the ETag Starlette already sends, a revalidation is a conditional request answering
    304 with no body.
    """
    assert security_headers.headers_for(path)["Cache-Control"] == "no-cache"


def test_nothing_is_left_without_a_caching_rule():
    """The failure this prevents is silent: with no Cache-Control at all, a browser
    invents a freshness lifetime and serves from memory without asking. In an installed
    PWA there is no address bar to hard-refresh from, so a deployed change can simply
    not be running — with nothing anywhere saying so."""
    for path in ("/", "/health", "/api/wefts", "/js/main.js", "/icons/icon-192.png"):
        assert "Cache-Control" in security_headers.headers_for(path), path


def test_the_security_headers_are_still_on_every_response():
    """Caching was added to this dict; nothing else should have left it."""
    headers = security_headers.headers_for("/js/main.js")

    for name in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Cross-Origin-Opener-Policy",
        "Permissions-Policy",
    ):
        assert name in headers, name


def test_autoplay_is_allowed_for_this_origin_only():
    """Replies are spoken as they arrive; denying autoplay contradicts the app."""
    policy = security_headers.headers_for("/")["Permissions-Policy"]

    assert "autoplay=(self)" in policy
    assert "autoplay=()" not in policy
