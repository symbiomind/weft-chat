"""
Authentication routes: csrf, login, refresh, logout, logout-all, me, password.

**The shape of the contract with the browser.** Login returns the access token in the
*body* and sets the refresh token as an httpOnly cookie. The client keeps the access
token in a JavaScript variable and never in storage; it cannot read the refresh cookie
at all. An XSS therefore steals at most a 15-minute credential that dies when the tab
closes, instead of a 30-day one it can take home.

**Why logout authenticates from the cookie, not the access token.** People log out
after leaving a tab open overnight, by which point the access token expired hours ago.
A logout that requires a live access token fails exactly when it is most wanted, and
leaves a valid refresh token alive on the device.

**Why /refresh is the most defended route here.** It is the only endpoint that
authenticates purely from an ambient cookie, which makes it the one a cross-site
request could drive. It carries SameSite, an Origin check, and a signed double-submit
CSRF token — three independent mechanisms, because the failure of any one of them
should not be a session takeover.
"""

import logging

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, cookies, csrf, db, deps, passwords, ratelimit, refresh, tokens, useragent
from app.models import AuthSession, Role, User, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str | None
    role: Role
    must_change_password: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    #: RELATIVE seconds. The client schedules its refresh from this and must never
    #: compare a server timestamp against its own clock.
    expires_in: int
    csrf: str
    user: UserOut


class SessionOut(BaseModel):
    """One signed-in device, as the Account screen shows it."""

    id: str
    #: The device asking. Listed first.
    current: bool
    #: Refreshed within the access-token lifetime, so very likely open right now.
    active: bool
    browser: str | None
    os: str | None
    ip: str | None
    #: The raw string the labels came from, for when they say nothing useful.
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    #: RELATIVE, like expires_in: the client says "3 hours ago" from this rather than
    #: comparing last_seen_at against its own clock.
    seen_seconds_ago: int


def _user_out(user: User) -> UserOut:
    """Serialise a user. Note what is absent: password_hash, token_version.

    token_version is an internal revocation counter; exposing it tells a caller how
    many times an account has been force-logged-out, which is nobody's business.
    """
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
    )


# ---------------------------------------------------------------------------
# CSRF bootstrap
# ---------------------------------------------------------------------------

@router.get("/csrf")
async def get_csrf(response: Response) -> dict:
    """Issue a CSRF token as both a readable cookie and a body value.

    Called by the client before its first state-changing request. Unauthenticated by
    necessity: login is itself a protected POST, so a token has to be obtainable before
    anyone is logged in. That is safe because the token proves nothing about identity —
    it only proves the request was made by script running on our own origin.
    """
    if not config.csrf_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CSRF protection is not configured on this server.",
        )
    token = csrf.issue()
    cookies.set_csrf_cookie(response, token)
    return {"csrf": token}


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db.get_session),
    _auth: None = Depends(deps.require_auth_available),
    _db: None = Depends(deps.require_db_healthy),
    _csrf: None = Depends(deps.require_csrf),
) -> TokenResponse:
    """Authenticate and start a session.

    Every failure below returns the same 401 with the same body, and takes comparable
    time. The rate-limit rejection is the one exception — it returns 429 with
    Retry-After, because telling a locked-out user to come back later is useful to them
    and tells an attacker only what they already know.
    """
    ip = deps.client_ip(request)
    username = body.username.strip()

    decision = await ratelimit.check(session, username, ip)
    if not decision.allowed:
        logger.warning(
            "Login for %r from %s refused by rate limit (%s).", username, ip, decision.reason
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    user = (
        await session.execute(select(User).where(User.username == username))
    ).scalars().one_or_none()

    # verify_or_dummy, not an if/else on `user`. When the account does not exist it
    # still runs a full argon2 verify against a dummy hash, so the response time does
    # not reveal which usernames are real.
    ok = passwords.verify_or_dummy(body.password, user.password_hash if user else None)

    # An inactive account is treated exactly like a wrong password — same status, same
    # body, same timing. Saying "this account is disabled" confirms it exists.
    if not ok or user is None or not user.is_active:
        await ratelimit.record_attempt(session, username, ip, succeeded=False)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    # Transparent hash upgrade. This is the only place the plaintext exists, so it is
    # the only place a stored hash can be re-made at stronger parameters.
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(body.password)
        logger.info("Upgraded the stored password hash for user %s.", user.id)

    raw_refresh, family_id = await refresh.start_family(
        session, user.id, user_agent=deps.user_agent(request), ip=ip
    )
    access_token, expires_in = tokens.mint_access_token(user.id, family_id, user.token_version)

    user.last_login_at = utcnow()
    await ratelimit.record_attempt(session, username, ip, succeeded=True)
    await session.commit()

    cookies.set_refresh_cookie(response, raw_refresh)
    csrf_token = csrf.issue(family_id)
    cookies.set_csrf_cookie(response, csrf_token)

    logger.info("User %s logged in from %s.", user.username, ip)
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        csrf=csrf_token,
        user=_user_out(user),
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _rejected_refresh() -> JSONResponse:
    """A 401 that also clears the refresh cookie.

    Built as an explicit JSONResponse rather than by raising HTTPException, because
    FastAPI's exception handler constructs a *fresh* response and discards the one the
    handler was given — so cookie mutations made before a raise are silently thrown
    away. The symptom is subtle: the client keeps a cookie the server has already
    rejected and retries it forever, and if the rejection was reuse detection then
    every retry looks like a new attack in the logs.
    """
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Session expired."}
    )
    cookies.clear_refresh_cookie(response)
    return response


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db.get_session),
    _auth: None = Depends(deps.require_auth_available),
    _db: None = Depends(deps.require_db_healthy),
    _csrf: None = Depends(deps.require_csrf),
) -> TokenResponse:
    """Rotate the refresh token and mint a fresh access token.

    On ANY failure the refresh cookie is cleared. Leaving a rejected cookie in place
    would have the client retry it forever — and if the rejection was reuse detection,
    every retry looks like another attack.
    """
    presented = request.cookies.get(cookies.refresh_cookie_name())

    try:
        new_refresh, consumed = await refresh.rotate(
            session, presented or "",
            user_agent=deps.user_agent(request), ip=deps.client_ip(request),
        )
    except refresh.RefreshError:
        # RefreshReuseError is a subclass and is deliberately not distinguished here:
        # refresh.rotate() has already revoked the family and logged the detail. The
        # response must not tell the caller which kind of failure it was.
        return _rejected_refresh()

    user = (
        await session.execute(select(User).where(User.id == consumed.user_id))
    ).scalars().one_or_none()

    if user is None or not user.is_active:
        # The account was deleted or deactivated while the session was live. Tear the
        # family down rather than leaving a rotating credential for a dead account.
        await refresh.revoke_family(session, consumed.family_id, reason="user_inactive")
        await session.commit()
        return _rejected_refresh()

    access_token, expires_in = tokens.mint_access_token(
        user.id, consumed.family_id, user.token_version
    )
    await session.commit()

    cookies.set_refresh_cookie(response, new_refresh)
    csrf_token = csrf.issue(consumed.family_id)
    cookies.set_csrf_cookie(response, csrf_token)

    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        csrf=csrf_token,
        user=_user_out(user),
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db.get_session),
    _db: None = Depends(deps.require_db_healthy),
    _csrf: None = Depends(deps.require_csrf),
) -> dict:
    """End this device's session.

    Authenticates from the refresh cookie, NOT from an access token, so it still works
    after the access token has expired — which is the common case, because people log
    out after leaving a tab open overnight.

    Always returns 200, even for an unknown or absent cookie. Logging out is not a
    thing that should be able to fail, and reporting "no such session" would confirm
    which tokens are real.
    """
    presented = request.cookies.get(cookies.refresh_cookie_name())
    if presented:
        await refresh.revoke_one(session, presented, reason="logout")
        await session.commit()

    cookies.clear_refresh_cookie(response)
    cookies.clear_csrf_cookie(response)
    return {"status": "logged out"}


@router.post("/logout-all")
async def logout_all(
    response: Response,
    user: User = Depends(deps.current_user_unrestricted),
    session: AsyncSession = Depends(db.get_session),
    _csrf: None = Depends(deps.require_csrf),
) -> dict:
    """End every session for this account, on every device.

    Two steps, and BOTH are required:

    1. Revoke every refresh family — stops new access tokens being minted.
    2. Bump ``token_version`` — invalidates every access token already out there.

    Doing only the first leaves a window of up to the access-token TTL in which a
    stolen token still works, which defeats the point of the button. ``tv`` exists
    precisely to close that window.
    """
    await refresh.revoke_all_for_user(session, user.id, reason="logout_all")
    user.token_version += 1
    await session.commit()

    # A chat stream already in flight runs to its end: authentication is checked when a
    # request starts, not while it streams. Every request after this one is refused.
    logger.info("User %s logged out of all sessions.", user.username)

    cookies.clear_refresh_cookie(response)
    cookies.clear_csrf_cookie(response)
    return {"status": "all sessions ended"}


# ---------------------------------------------------------------------------
# Signed-in devices
# ---------------------------------------------------------------------------

def _session_out(device: AuthSession, *, current_id: str | None, now: datetime) -> SessionOut:
    browser, system = useragent.describe(device.user_agent)
    seen = max(0, int((now - device.last_seen_at).total_seconds()))
    current = device.id == current_id
    active_window = int(config.get("security.access_token_ttl_seconds") or 900)
    return SessionOut(
        id=device.id,
        current=current,
        active=current or seen <= active_window,
        browser=browser,
        os=system,
        ip=device.ip,
        user_agent=device.user_agent,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        expires_at=device.expires_at,
        seen_seconds_ago=seen,
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    request: Request,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> list[SessionOut]:
    """Every device this account is signed in on, this one first."""
    current_id = getattr(request.state, "session_id", None)
    now = utcnow()
    devices = [
        _session_out(device, current_id=current_id, now=now)
        for device in await refresh.live_sessions(session, user.id)
    ]
    # Stable: everything else keeps its newest-activity-first order.
    devices.sort(key=lambda d: not d.current)
    return devices


@router.delete("/sessions/{session_id}")
async def end_session(
    request: Request,
    response: Response,
    session_id: str = Path(max_length=36),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
    _csrf: None = Depends(deps.require_csrf),
) -> dict:
    """Sign one device out. Its next request is refused, not merely its next refresh.

    404 for a device that is not yours, already signed out, or never existed — one
    answer for all three, so an id cannot be probed for. Ending the device you are on is
    allowed and is the same as /logout, cookies and all.
    """
    current = session_id == getattr(request.state, "session_id", None)
    reason = "logout" if current else "signed_out_from_another_device"
    if not await refresh.revoke_session(session, user.id, session_id, reason=reason):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such session.")
    await session.commit()

    if current:
        cookies.clear_refresh_cookie(response)
        cookies.clear_csrf_cookie(response)
    logger.info("User %s signed out session %s.", user.username, session_id)
    return {"status": "signed out"}


# ---------------------------------------------------------------------------
# Identity and password
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(deps.current_user_unrestricted)) -> UserOut:
    """Who am I.

    Uses the unrestricted dependency so a bootstrap credential can render the
    "you must change your password" screen instead of getting a 409 it cannot explain.
    """
    return _user_out(user)


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    response: Response,
    request: Request,
    user: User = Depends(deps.current_user_unrestricted),
    session: AsyncSession = Depends(db.get_session),
    _csrf: None = Depends(deps.require_csrf),
) -> dict:
    """Change your own password.

    Requires the current password even though the caller is already authenticated:
    that is what stops a stolen access token, or an XSS holding one, from locking the
    real owner out of their own account.

    On success every *other* session is ended. The device doing the change keeps
    working — logging someone out for successfully securing their account trains them
    not to.
    """
    if not passwords.verify_password(body.current_password, user.password_hash):
        logger.warning("Failed password-change attempt for user %s.", user.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect."
        )

    try:
        passwords.validate_password(body.new_password, username=user.username)
    except passwords.PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current one.",
        )

    user.password_hash = passwords.hash_password(body.new_password)
    user.must_change_password = False

    # A password change is a "my account may be compromised" action, so every other
    # session dies. token_version is bumped for the same reason as logout-all: without
    # it, an attacker's existing access token survives the password change.
    current_session_id = getattr(request.state, "session_id", None)
    await refresh.revoke_all_for_user(session, user.id, reason="password_changed")
    user.token_version += 1
    await session.commit()

    # Re-establish this device only. The client gets a fresh pair and stays logged in.
    raw_refresh, family_id = await refresh.start_family(
        session, user.id, user_agent=deps.user_agent(request), ip=deps.client_ip(request)
    )
    access_token, expires_in = tokens.mint_access_token(user.id, family_id, user.token_version)
    await session.commit()

    cookies.set_refresh_cookie(response, raw_refresh)
    csrf_token = csrf.issue(family_id)
    cookies.set_csrf_cookie(response, csrf_token)

    logger.info(
        "User %s changed their password; all other sessions ended (was session %s).",
        user.username, current_session_id,
    )
    return {
        "status": "password changed",
        "access_token": access_token,
        "expires_in": expires_in,
        "csrf": csrf_token,
    }
