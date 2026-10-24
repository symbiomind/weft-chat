"""
FastAPI dependencies: authentication, authorization, CSRF, and the degradation gates.

This module is where a request stops being anonymous. Four things happen, in this
order, and the order is deliberate:

1. **Subsystem gate** — if the relevant secret is missing, 503 before doing anything
   else. A service with no JWT secret must not appear to authenticate.
2. **Token verification** — signature, expiry, issuer, audience (``app/tokens.py``).
3. **Database check** — the user still exists, is still active, their
   ``token_version`` still matches the token's ``tv`` claim, and the device the token
   was issued to (its ``sid``) is still signed in. This is the step that makes
   revocation immediate instead of eventual — for the whole account (``tv``) and for
   one device (``sid``).
4. **must_change_password gate** — a bootstrap or reset credential can do exactly two
   things: change its own password, and log out.

Step 3 is the one people remove for performance. Without it, a demoted or deactivated
user keeps full access until their access token expires — up to fifteen minutes on a
service holding other people's provider API keys.
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, cookies, csrf, db, tokens
from app.models import AuthSession, Role, User, utcnow

logger = logging.getLogger(__name__)

#: One body for every authentication failure. Distinguishing "expired" from "bad
#: signature" from "no such user" in the response tells an attacker which half of their
#: attempt was right.
_UNAUTHENTICATED = "Not authenticated"


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_UNAUTHENTICATED,
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# Degradation gates
# ---------------------------------------------------------------------------

async def require_auth_available() -> None:
    """503 when there is no JWT secret, rather than minting forgeable tokens."""
    if not config.auth_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on this server.",
        )


async def require_db_healthy() -> None:
    """503 when migrations failed, rather than querying a half-built schema."""
    if db.migration_failed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is starting up or misconfigured.",
        )


async def require_vault_available() -> None:
    if not config.vault_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The provider vault is not configured on this server.",
        )


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

async def require_csrf(request: Request) -> None:
    """Enforce origin + signed double-submit on every state-changing request.

    Applied to POST/PUT/PATCH/DELETE. The endpoint this most protects is
    ``/api/auth/refresh``, the only route that authenticates from an ambient cookie
    alone.
    """
    if request.method not in csrf.PROTECTED_METHODS:
        return

    if not config.csrf_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CSRF protection is not configured on this server.",
        )

    if not csrf.origin_allowed(
        request.headers.get("origin"), request.headers.get("referer")
    ):
        logger.warning(
            "Rejected %s %s: origin %r is not allowed.",
            request.method, request.url.path, request.headers.get("origin"),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")

    try:
        csrf.verify_double_submit(
            request.cookies.get(cookies.csrf_cookie_name()),
            request.headers.get(csrf.HEADER_NAME),
        )
    except csrf.CSRFError as exc:
        logger.warning("Rejected %s %s: %s", request.method, request.url.path, exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed"
        ) from exc


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _bearer_token(request: Request) -> str | None:
    """Extract a bearer token. Case-insensitive scheme, per RFC 7235."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def current_user(
    request: Request,
    session: AsyncSession = Depends(db.get_session),
    _auth: None = Depends(require_auth_available),
    _db: None = Depends(require_db_healthy),
) -> User:
    """The authenticated user, or 401.

    Verifies the token cryptographically and then re-checks the user against the
    database on every request. Both halves are required: the signature proves the token
    was issued by us, the database check proves the account it names is still allowed in
    *right now*.
    """
    token = _bearer_token(request)
    if not token:
        raise _unauthenticated()

    try:
        claims = tokens.decode_access_token(token)
    except tokens.TokenError:
        raise _unauthenticated() from None

    # One query for both: the account, and the device this token was issued to.
    found = (
        await session.execute(
            select(User, AuthSession)
            .outerjoin(
                AuthSession,
                and_(AuthSession.id == claims.session_id, AuthSession.user_id == User.id),
            )
            .where(User.id == claims.user_id)
        )
    ).one_or_none()

    if found is None:
        # Token is valid but the account is gone. Deleted mid-session.
        raise _unauthenticated()
    user, device = found

    if not user.is_active:
        raise _unauthenticated()

    if user.token_version != claims.token_version:
        # Logged out everywhere, role changed, or deactivated and reactivated. The
        # token is cryptographically fine and semantically stale.
        logger.debug(
            "Rejected a token for user %s: tv %s != %s",
            user.id, claims.token_version, user.token_version,
        )
        raise _unauthenticated()

    if device is None or device.revoked_at is not None or device.expires_at <= utcnow():
        # This device was signed out from another one (or its session ran out). tv
        # cannot say that: it is per account, and bumping it signs out every device.
        raise _unauthenticated()

    # Stash the session id so handlers can scope things (CSRF binding, socket
    # revocation) to this device rather than the whole account.
    request.state.session_id = claims.session_id
    request.state.access_claims = claims
    return user


async def current_user_unrestricted(
    user: User = Depends(current_user),
) -> User:
    """An authenticated user, *without* the must-change-password gate.

    Only for the two routes a bootstrap credential is allowed to reach: changing its
    own password, and logging out.
    """
    return user


async def active_user(user: User = Depends(current_user)) -> User:
    """The normal dependency for authenticated routes.

    Adds the must_change_password gate: an account holding a bootstrap or
    administrator-reset credential can do nothing until it sets its own password. 409
    rather than 403, because the client's correct response is "go do this thing first",
    not "you are not allowed".
    """
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Password change required before this account can be used.",
        )
    return user


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def require_role(*allowed: Role) -> Callable[..., Awaitable[User]]:
    """Dependency factory gating a route on role.

    Roles come from the database via ``current_user``, never from a token claim, so a
    demotion takes effect on the demoted user's very next request.
    """

    async def dependency(user: User = Depends(active_user)) -> User:
        if user.role not in allowed:
            # 403, not 404: the caller is authenticated, and hiding the route's
            # existence from a logged-in user of the same service buys nothing.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges.",
            )
        return user

    return dependency


#: An owner or an admin. May manage users and mint `user` invites.
require_admin = require_role(Role.owner, Role.admin)

#: Owner only. The single privilege-escalation path in the service: granting `admin`,
#: changing any role, and deleting accounts.
require_owner = require_role(Role.owner)


# ---------------------------------------------------------------------------
# Request metadata
# ---------------------------------------------------------------------------

def client_ip(request: Request) -> str | None:
    """The caller's IP as the reverse proxy reports it.

    ``request.client.host`` is already proxy-corrected because uvicorn runs with
    ``--proxy-headers`` and an explicit ``--forwarded-allow-ips``. Reading
    ``X-Forwarded-For`` directly here would trust a header any client can set whenever
    that allow-list is wrong — the rate limiter would then be bypassable by sending one
    header.
    """
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")
