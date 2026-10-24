"""
Tests for app/csrf.py.

**Why this file matters.** The refresh endpoint authenticates purely from an ambient
cookie, which makes it the one route a cross-site request can drive. SameSite=Strict is
the first line of defence; this module is the second, and the second line is the one
that still works on an old browser or against a sibling subdomain.

The test that justifies the design is `test_attacker_cannot_mint_a_token_without_the_secret`.
Plain double-submit — compare cookie to header, accept if equal — is defeated by an
attacker who can *write* a cookie (a compromised sibling subdomain, or a MITM on any
plain-HTTP subdomain of the parent domain). They set both halves to the same value and
the check passes. Signing the value means they cannot produce one the server accepts.
"""

import time

import pytest

from app import config, csrf
from app.csrf import CSRFError

SECRET = "csrf-test-secret"


@pytest.fixture(autouse=True)
def configured(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "server:\n"
        '  public_origin: "https://weft.example.com"\n'
        "  csrf:\n"
        f'    secret: "{SECRET}"\n'
        "    ttl_seconds: 86400\n"
        "  security:\n"
        "    allowed_origins:\n"
        '      - "https://weft.example.com"\n',
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)


# ---------------------------------------------------------------------------
# Issue and verify
# ---------------------------------------------------------------------------

def test_issued_token_verifies():
    csrf.verify(csrf.issue())


def test_tokens_are_unique_per_issue():
    assert len({csrf.issue() for _ in range(20)}) == 20


def test_token_has_the_expected_shape():
    parts = csrf.issue("sess-1").split(".")
    assert len(parts) == 4


# ---------------------------------------------------------------------------
# Forgery — the reason the token is signed
# ---------------------------------------------------------------------------

def test_attacker_cannot_mint_a_token_without_the_secret():
    """The cookie-injection scenario. An attacker who can write cookies for the parent
    domain sets both halves to a value they chose; plain double-submit accepts it.
    Here they also need the HMAC key, which they do not have."""
    forged = "nonce.{}.sess-1.YWJjZGVmZ2hpamtsbW5vcA".format(int(time.time()))
    with pytest.raises(CSRFError):
        csrf.verify(forged)


def test_token_signed_with_a_different_secret_is_rejected(tmp_path):
    token = csrf.issue()

    path = tmp_path / "other.yml"
    path.write_text('server:\n  csrf:\n    secret: "a-different-secret"\n', encoding="utf-8")
    config.load_config(str(path), force=True)

    with pytest.raises(CSRFError):
        csrf.verify(token)


def test_tampering_with_the_payload_invalidates_the_signature():
    nonce, issued_at, session_id, signature = csrf.issue("sess-1").split(".")
    with pytest.raises(CSRFError):
        csrf.verify(".".join((nonce, issued_at, "sess-hijacked", signature)))


@pytest.mark.parametrize("bad", ["", "a", "a.b", "a.b.c", "a.b.c.d.e", "....", "not-a-token"])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(CSRFError):
        csrf.verify(bad)


def test_missing_token_is_rejected():
    with pytest.raises(CSRFError):
        csrf.verify(None)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

def test_expired_token_is_rejected(tmp_path):
    path = tmp_path / "short.yml"
    path.write_text(
        f'server:\n  csrf:\n    secret: "{SECRET}"\n    ttl_seconds: -1\n', encoding="utf-8"
    )
    config.load_config(str(path), force=True)

    with pytest.raises(CSRFError, match="expired"):
        csrf.verify(csrf.issue())


def test_token_from_the_future_is_rejected():
    """Not something a legitimate token does; a sign of a hand-built one."""
    future = str(int(time.time()) + 3600)
    payload = f"nonce.{future}."
    signature = csrf._sign(payload)
    with pytest.raises(CSRFError, match="future"):
        csrf.verify(f"{payload}.{signature}")


def test_non_numeric_timestamp_is_rejected():
    payload = "nonce.not-a-number."
    with pytest.raises(CSRFError):
        csrf.verify(f"{payload}.{csrf._sign(payload)}")


# ---------------------------------------------------------------------------
# Session binding
# ---------------------------------------------------------------------------

def test_token_bound_to_a_session_verifies_for_that_session():
    csrf.verify(csrf.issue("sess-1"), session_id="sess-1")


def test_token_bound_to_one_session_is_rejected_for_another():
    """Stops a token minted for one login being replayed into another."""
    with pytest.raises(CSRFError, match="different session"):
        csrf.verify(csrf.issue("sess-1"), session_id="sess-2")


def test_unbound_token_is_accepted_for_any_session():
    """Login is itself a protected POST and happens before a session exists, so a
    pre-login token has to be usable."""
    csrf.verify(csrf.issue(""), session_id="sess-1")


# ---------------------------------------------------------------------------
# Double submit
# ---------------------------------------------------------------------------

def test_matching_cookie_and_header_pass():
    token = csrf.issue("sess-1")
    csrf.verify_double_submit(token, token, session_id="sess-1")


def test_mismatched_cookie_and_header_are_rejected():
    with pytest.raises(CSRFError, match="do not match"):
        csrf.verify_double_submit(csrf.issue(), csrf.issue())


def test_missing_cookie_is_rejected():
    with pytest.raises(CSRFError, match="cookie"):
        csrf.verify_double_submit(None, csrf.issue())


def test_missing_header_is_rejected():
    with pytest.raises(CSRFError, match="header"):
        csrf.verify_double_submit(csrf.issue(), None)


def test_both_halves_matching_but_unsigned_are_rejected():
    """The cookie-injection attack in its exact form: attacker sets both halves to the
    same attacker-chosen value."""
    forged = "x.{}.x.x".format(int(time.time()))
    with pytest.raises(CSRFError):
        csrf.verify_double_submit(forged, forged)


# ---------------------------------------------------------------------------
# Origin checking
# ---------------------------------------------------------------------------

def test_allowed_origin_passes():
    assert csrf.origin_allowed("https://weft.example.com") is True


def test_foreign_origin_is_rejected():
    assert csrf.origin_allowed("https://evil.example") is False


def test_lookalike_subdomain_is_rejected():
    """A prefix/suffix comparison instead of an exact match would let this through."""
    assert csrf.origin_allowed("https://weft.example.com.evil.example") is False
    assert csrf.origin_allowed("https://evil-weft.example.com") is False


def test_scheme_must_match():
    """http:// and https:// are different origins. Accepting the plain-HTTP twin would
    undo the point of Secure cookies."""
    assert csrf.origin_allowed("http://weft.example.com") is False


def test_missing_origin_and_referer_is_rejected():
    """A state-changing request with neither header is not a browser doing normal work.
    Defaulting it to allowed would make the whole check optional."""
    assert csrf.origin_allowed(None, None) is False


def test_referer_is_used_when_origin_is_absent():
    assert csrf.origin_allowed(None, "https://weft.example.com/some/path?q=1") is True


def test_referer_from_a_foreign_site_is_rejected():
    assert csrf.origin_allowed(None, "https://evil.example/weft.example.com") is False


def test_trailing_slash_on_origin_is_tolerated():
    assert csrf.origin_allowed("https://weft.example.com/") is True


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

def test_no_secret_means_every_check_fails_closed(tmp_path):
    """Without a key, signing is meaningless — so nothing verifies, rather than
    everything passing."""
    path = tmp_path / "nosecret.yml"
    path.write_text('server:\n  csrf:\n    secret: ""\n', encoding="utf-8")
    config.load_config(str(path), force=True)

    with pytest.raises(CSRFError):
        csrf.issue()
    with pytest.raises(CSRFError):
        csrf.verify("anything.123.x.y")
