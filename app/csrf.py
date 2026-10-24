"""
CSRF tokens: signed double-submit.

**Why this exists when the refresh cookie is already SameSite=Strict.** SameSite is
necessary but not sufficient. It is a browser-side mitigation, and it has gaps:
older browsers ignore it, a same-site subdomain is not cross-site, and a redirect
chain that originates same-site is not always classified the way you would hope.
Defence in depth is cheap here, and the endpoint at risk is specific and known.

**The endpoint an attacker actually wants is ``POST /api/auth/refresh``.** It is the
only route that authenticates purely from an ambient cookie — everything else needs a
bearer token that an attacker's page cannot read. A successful CSRF against refresh
hands the attacker a rotated session.

**Why the token is signed rather than a bare random value.** Plain double-submit
compares a cookie against a header and trusts that only same-origin script could have
set the cookie. That assumption breaks against a *cookie-injection* attacker — a
compromised sibling subdomain, or a MITM on any plain-HTTP subdomain of the same site,
can write a cookie for the parent domain. They then know both halves and the check
passes. An HMAC over the value means the attacker cannot mint a cookie the server will
accept, because they do not have the signing key.

The token is bound to a session id where one exists, so a token minted for one login
cannot be replayed into another.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time

from app import config

logger = logging.getLogger(__name__)

#: Name of both the cookie and the header half. Not `__Host-` prefixed by the same
#: config switch as the refresh cookie — see cookies.py for why the prefix is
#: conditional.
COOKIE_NAME = "weft_csrf"
HEADER_NAME = "X-Weft-CSRF"

#: Methods that change state and therefore require a valid token.
PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_SEPARATOR = "."


class CSRFError(Exception):
    """Token missing, malformed, expired, or not matching."""


def _secret() -> bytes:
    secret = config.csrf_secret()
    if not secret:
        raise CSRFError("CSRF secret is not configured")
    return secret.encode("utf-8")


def _sign(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue(session_id: str = "") -> str:
    """Mint a CSRF token: ``<nonce>.<issued_at>.<session_id>.<signature>``.

    Readable by JavaScript on purpose — the whole mechanism depends on same-origin
    script being able to copy the cookie into a header, which is precisely what a
    cross-origin attacker cannot do.
    """
    nonce = secrets.token_urlsafe(16)
    issued_at = str(int(time.time()))
    payload = _SEPARATOR.join((nonce, issued_at, session_id))
    return _SEPARATOR.join((payload, _sign(payload)))


def verify(token: str | None, session_id: str = "") -> None:
    """Validate a token. Returns None on success, raises ``CSRFError`` otherwise."""
    if not token:
        raise CSRFError("missing CSRF token")

    parts = token.split(_SEPARATOR)
    if len(parts) != 4:
        raise CSRFError("malformed CSRF token")

    nonce, issued_at, token_session, signature = parts
    payload = _SEPARATOR.join((nonce, issued_at, token_session))

    # compare_digest, not ==. String comparison short-circuits on the first differing
    # byte, which leaks the length of a correct prefix and makes the signature
    # forgeable one character at a time.
    if not hmac.compare_digest(signature, _sign(payload)):
        raise CSRFError("bad CSRF signature")

    try:
        age = int(time.time()) - int(issued_at)
    except ValueError as exc:
        raise CSRFError("malformed CSRF timestamp") from exc

    ttl = int(config.get("csrf.ttl_seconds") or 86400)
    if age > ttl:
        raise CSRFError("CSRF token expired")
    if age < -60:
        # Issued in the future by more than clock skew: not something a legitimate
        # token does.
        raise CSRFError("CSRF token issued in the future")

    # A token minted before login (session_id "") is accepted for any session — it has
    # to be, because login itself is a protected POST and there is no session yet. Once
    # a token carries a session it must match, so one user's token cannot be replayed
    # into another user's request.
    if token_session and session_id and not hmac.compare_digest(token_session, session_id):
        raise CSRFError("CSRF token belongs to a different session")


def verify_double_submit(
    cookie_value: str | None,
    header_value: str | None,
    session_id: str = "",
) -> None:
    """The full check performed on every state-changing request.

    Both halves must be present, identical, and carry a valid signature. Verifying the
    signature *as well as* the match is what defends against cookie injection: an
    attacker who can write the cookie still cannot produce a signature for it.
    """
    if not cookie_value:
        raise CSRFError("missing CSRF cookie")
    if not header_value:
        raise CSRFError(f"missing {HEADER_NAME} header")

    if not hmac.compare_digest(cookie_value, header_value):
        raise CSRFError("CSRF cookie and header do not match")

    verify(cookie_value, session_id)


def origin_allowed(origin: str | None, referer: str | None = None) -> bool:
    """Check the Origin header (falling back to Referer) against the allowlist.

    Belt to the CSRF braces, and the primary defence for the WebSocket handshake where
    the double-submit scheme does not apply.

    A state-changing request with *neither* header is rejected. Browsers always send
    Origin on cross-origin requests and on same-origin POSTs; a request carrying
    neither is not a browser doing normal work, and defaulting it to "allowed" would
    make the whole check optional from the attacker's point of view.
    """
    allowed = config.allowed_origins()

    candidate = origin
    if not candidate and referer:
        # Reduce a Referer URL to its origin. Referer carries a path; Origin does not.
        parts = referer.split("/")
        if len(parts) >= 3:
            candidate = "//".join((parts[0], parts[2]))

    if not candidate:
        return False

    return candidate.rstrip("/") in allowed
