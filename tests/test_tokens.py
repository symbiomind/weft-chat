"""
Tests for app/tokens.py.

**Why this file matters more than any other in the suite.** Everything downstream
trusts whatever ``decode_access_token`` returns. If it can be made to return claims for
a token the server never signed, every authorization check in the service is decoration.

The tests are therefore mostly negative, and three of them cover attacks rather than
bugs:

* ``test_alg_none_is_rejected`` — the classic JWT forgery. Strip the signature, set the
  header to ``{"alg":"none"}``, and a decoder that reads the algorithm from the token
  will happily accept it.
* ``test_token_signed_with_another_secret_is_rejected`` — proves the signature is
  actually checked, not merely parsed.
* ``test_tampered_payload_is_rejected`` — proves an attacker cannot edit their own
  valid token to raise their privileges.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import config
from app.tokens import REQUIRED_CLAIMS, AccessClaims, TokenError, decode_access_token, mint_access_token

SECRET = "test-secret-not-used-anywhere-real-and-long-enough-for-hs256"


@pytest.fixture(autouse=True)
def configured(tmp_path):
    """A minimal but complete JWT configuration for every test in this file."""
    path = tmp_path / "config.yml"
    path.write_text(
        "server:\n"
        "  jwt:\n"
        f'    secret: "{SECRET}"\n'
        "    algorithm: HS256\n"
        '    issuer: "weft-service"\n'
        '    audience: "weft-web"\n'
        "    leeway_seconds: 10\n"
        "  security:\n"
        "    access_token_ttl_seconds: 900\n",
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)


def mint(**overrides) -> str:
    """Hand-build a token so tests can produce ones the real minter never would."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1", "sid": "sess-1", "tv": 0, "jti": "jti-1",
        "iat": now, "exp": now + timedelta(minutes=15),
        "iss": "weft-service", "aud": "weft-web",
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_mint_and_decode_round_trip():
    token, expires_in = mint_access_token("user-1", "sess-1", 0)
    claims = decode_access_token(token)

    assert isinstance(claims, AccessClaims)
    assert claims.user_id == "user-1"
    assert claims.session_id == "sess-1"
    assert claims.token_version == 0
    assert expires_in == 900


def test_expires_in_is_relative_seconds_not_a_timestamp():
    """The client schedules its refresh from this. An absolute timestamp would invite
    it to compare against its own possibly-skewed clock."""
    _, expires_in = mint_access_token("u", "s", 0)
    assert 0 < expires_in < 86400


def test_each_token_gets_a_unique_jti():
    a = decode_access_token(mint_access_token("u", "s", 0)[0])
    b = decode_access_token(mint_access_token("u", "s", 0)[0])
    assert a.jti != b.jti


def test_seconds_remaining_is_positive_for_a_fresh_token():
    claims = decode_access_token(mint_access_token("u", "s", 0)[0])
    assert 0 < claims.seconds_remaining <= 900


# ---------------------------------------------------------------------------
# Forgery
# ---------------------------------------------------------------------------

def test_alg_none_is_rejected():
    """The classic JWT forgery: an unsigned token declaring alg=none. A decoder that
    takes the algorithm from the token's own header accepts it."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": "attacker", "sid": "s", "tv": 0, "jti": "j",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        "iss": "weft-service", "aud": "weft-web",
    }).encode()).rstrip(b"=")
    forged = f"{header.decode()}.{payload.decode()}."

    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_token_signed_with_another_secret_is_rejected():
    now = datetime.now(timezone.utc)
    foreign = jwt.encode(
        {"sub": "attacker", "sid": "s", "tv": 0, "jti": "j", "iat": now,
         "exp": now + timedelta(hours=1), "iss": "weft-service", "aud": "weft-web"},
        "a-completely-different-secret-also-long-enough-for-hs256", algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(foreign)


def test_tampered_payload_is_rejected():
    """An attacker editing their own valid token — the reason claims are signed rather
    than merely encoded. Note base64 is not encryption: the payload is readable, which
    is why nothing secret goes in it."""
    token = mint(sub="user-1")
    header_b64, payload_b64, signature = token.split(".")

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = "someone-else"
    tampered_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()

    with pytest.raises(TokenError):
        decode_access_token(f"{header_b64}.{tampered_payload}.{signature}")


def test_garbage_is_rejected():
    for garbage in ("", "not-a-token", "a.b.c", "...", "Bearer x"):
        with pytest.raises(TokenError):
            decode_access_token(garbage)


# ---------------------------------------------------------------------------
# Expiry and skew
# ---------------------------------------------------------------------------

def test_expired_token_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(TokenError):
        decode_access_token(mint(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)))


def test_token_expired_within_leeway_is_still_accepted():
    """10s of leeway is deliberate and small. Large leeway extends the life of a token
    you believed had expired."""
    now = datetime.now(timezone.utc)
    claims = decode_access_token(mint(exp=now - timedelta(seconds=5)))
    assert claims.user_id == "user-1"


def test_token_expired_beyond_leeway_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(TokenError):
        decode_access_token(mint(exp=now - timedelta(seconds=60)))


# ---------------------------------------------------------------------------
# Required claims
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("claim", REQUIRED_CLAIMS)
def test_every_required_claim_is_actually_required(claim):
    """A token missing `exp` is not a token that never expires — it is one that must be
    rejected. Same for the claims downstream code reads without checking."""
    with pytest.raises(TokenError):
        decode_access_token(mint(**{claim: None}))


def test_wrong_issuer_is_rejected():
    """Stops a token minted by a different service that happens to share the secret —
    for example a sibling deployment restored from the same .env."""
    with pytest.raises(TokenError):
        decode_access_token(mint(iss="some-other-service"))


def test_wrong_audience_is_rejected():
    with pytest.raises(TokenError):
        decode_access_token(mint(aud="some-other-audience"))


def test_non_integer_token_version_is_rejected():
    """`require` guarantees a claim is present, not that it has the right type. A `tv`
    of "banana" passes the claim check and would otherwise explode at the comparison,
    inside the auth dependency, at runtime."""
    with pytest.raises(TokenError):
        decode_access_token(mint(tv="banana"))


# ---------------------------------------------------------------------------
# What must NOT be in the token
# ---------------------------------------------------------------------------

def test_role_is_not_a_claim():
    """Role is read from the database per request. Putting it in the token would make a
    demotion take up to 15 minutes to take effect, on a service that holds other
    people's provider API keys. This test exists so that trade is re-argued
    deliberately rather than 'optimised' away."""
    token, _ = mint_access_token("u", "s", 0)
    payload = jwt.decode(token, SECRET, algorithms=["HS256"], audience="weft-web")
    assert "role" not in payload
    assert "permissions" not in payload


def test_no_secret_material_in_the_payload():
    """The payload is base64, not encrypted — anyone holding the token can read it."""
    token, _ = mint_access_token("u", "s", 0)
    payload = jwt.decode(token, SECRET, algorithms=["HS256"], audience="weft-web")
    assert SECRET not in json.dumps(payload)
    assert set(payload) == {"sub", "sid", "tv", "jti", "iat", "exp", "iss", "aud"}


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

def test_minting_without_a_secret_raises_rather_than_signing_with_empty(tmp_path):
    """Signing with "" would produce tokens anyone could forge. A raised error becomes
    a 503; a signed-with-nothing token becomes a breach."""
    path = tmp_path / "config.yml"
    path.write_text('server:\n  jwt:\n    secret: ""\n', encoding="utf-8")
    config.load_config(str(path), force=True)

    with pytest.raises(TokenError):
        mint_access_token("u", "s", 0)
    with pytest.raises(TokenError):
        decode_access_token("anything")
