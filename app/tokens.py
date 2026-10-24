"""
Access-token minting and verification.

Pure JWT logic — no database, no request objects. The refresh-token family machinery
lives in ``app/refresh.py``; this module knows only how to make and check the
short-lived bearer token.

Three decisions here are security-relevant and easy to undo by accident:

**The algorithm is passed explicitly to decode.** ``jwt.decode(token, key)`` without
``algorithms=[...]`` lets the *token* choose its own verification algorithm via its
header — the classic ``alg: none`` and RS256→HS256 confusion attacks. PyJWT defends
against this by requiring the parameter, and this module never builds it from the
token.

**Every claim we rely on is in ``require``.** A token missing ``exp`` is not a token
that never expires; it is a token that must be rejected. Same for ``sub``, ``sid`` and
``tv`` — code downstream reads them and would otherwise get ``None`` and make a
decision with it.

**Role is deliberately NOT a claim.** It is read from the database on every request.
That costs one query and buys revocation-within-one-request instead of
revocation-within-15-minutes, which is the right trade for a service holding other
people's provider keys. If you are here to "optimise" the role into the token, read
`tv` below first: the same argument was already had, and this is the answer.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app import config

logger = logging.getLogger(__name__)

#: Claims we always require to be present. A token without one of these is rejected
#: rather than defaulted.
REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "iss", "aud", "sid", "tv"]


class TokenError(Exception):
    """Any failure to produce a trustworthy set of claims.

    One exception type on purpose: the caller turns every one of these into the same
    401 with the same body. Distinguishing "expired" from "bad signature" in the
    response would tell an attacker which half of their forgery was right.
    """


@dataclass(frozen=True)
class AccessClaims:
    """The verified contents of an access token."""

    user_id: str
    session_id: str
    token_version: int
    jti: str
    expires_at: datetime

    @property
    def seconds_remaining(self) -> float:
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()


def _secret() -> str:
    secret = config.jwt_secret()
    if not secret:
        # Should be unreachable: routes are gated on config.auth_available(). Raising
        # here rather than signing with "" is the difference between a 503 and a
        # service that issues tokens anyone can forge.
        raise TokenError("JWT secret is not configured")
    return secret


def _algorithm() -> str:
    return str(config.get("jwt.algorithm") or "HS256")


def mint_access_token(
    user_id: str,
    session_id: str,
    token_version: int,
) -> tuple[str, int]:
    """Mint a short-lived access token. Returns ``(token, expires_in_seconds)``.

    ``expires_in`` is returned as a *relative* number of seconds because that is what
    the client must use to schedule its refresh. Handing it an absolute timestamp
    invites the client to compare it against its own clock, and a browser with a skewed
    clock would then refresh far too early or far too late.
    """
    ttl = int(config.get("security.access_token_ttl_seconds") or 900)
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "sid": session_id,
        #: Bumping users.token_version invalidates every outstanding token for that
        #: user at once — the mechanism behind logout-everywhere, role changes and
        #: deactivation. Verified against the database on every request.
        "tv": token_version,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "iss": str(config.get("jwt.issuer") or "weft-service"),
        "aud": str(config.get("jwt.audience") or "weft-web"),
    }

    token = jwt.encode(payload, _secret(), algorithm=_algorithm())
    return token, ttl


def decode_access_token(token: str) -> AccessClaims:
    """Verify a token and return its claims. Raises ``TokenError`` on any problem.

    Does NOT check ``tv`` against the database — that needs a session, so it belongs to
    the dependency in ``app/deps.py``. This function's job is "is this token
    structurally trustworthy", not "is this user still allowed in".
    """
    if not token:
        raise TokenError("empty token")

    try:
        payload = jwt.decode(
            token,
            _secret(),
            # Explicit, and never read from the token's own header. This is the line
            # that defends against alg:none and HS/RS confusion.
            algorithms=[_algorithm()],
            issuer=str(config.get("jwt.issuer") or "weft-service"),
            audience=str(config.get("jwt.audience") or "weft-web"),
            leeway=int(config.get("jwt.leeway_seconds") or 10),
            options={
                "require": REQUIRED_CLAIMS,
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.PyJWTError as exc:
        # Logged at debug, not warning: a stream of expired tokens is normal operation
        # (every client hits one every 15 minutes), and logging them at warning trains
        # operators to ignore the level that matters.
        logger.debug("Access token rejected: %s", type(exc).__name__)
        raise TokenError(str(exc)) from exc

    try:
        return AccessClaims(
            user_id=str(payload["sub"]),
            session_id=str(payload["sid"]),
            token_version=int(payload["tv"]),
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # `require` guarantees presence, not type. A token whose `tv` is "banana"
        # passes the claim check and would otherwise blow up in the comparison.
        raise TokenError(f"malformed claim: {exc}") from exc
