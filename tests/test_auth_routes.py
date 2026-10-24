"""
Tests for app/routes/auth.py — the HTTP surface of authentication.

**Why this file matters.** The unit tests prove the primitives are correct; these prove
they are actually *wired up*. A perfect `tokens.py` protects nothing if a route forgets
to depend on it, and that class of mistake produces no error anywhere — the endpoint
simply works for everyone.

The assertions that would matter most in an incident:

* `test_login_is_identical_for_unknown_user_and_wrong_password` — the response body,
  status and shape must not distinguish the two, or login enumerates your users.
* `test_inactive_user_gets_the_same_401_as_a_wrong_password` — "this account is
  disabled" confirms the account exists.
* `test_refresh_requires_csrf` — /refresh authenticates from an ambient cookie and is
  the one route a cross-site request could drive.
* `test_reuse_kills_the_family_over_http` — the end-to-end proof of the rotation design.
"""

import pytest
from sqlalchemy import select

from app import cookies, passwords
from app.models import Role, User, utcnow

PASSWORD = "a-perfectly-good-password"


async def make_user(
    session, username="alice", password=PASSWORD, role=Role.user,
    is_active=True, must_change=False,
) -> User:
    user = User(
        username=username,
        display_name=username,
        password_hash=passwords.hash_password(password),
        role=role,
        is_active=is_active,
        must_change_password=must_change,
    )
    session.add(user)
    await session.commit()
    return user


async def login(client, username="alice", password=PASSWORD):
    """Log in, and re-sync the CSRF header the way a real client must.

    Login mints a *new* CSRF token bound to the new session and returns it in the body
    alongside setting the cookie. A client that keeps sending its pre-login token will
    get 403 on every subsequent state-changing request, because the cookie moved on and
    the header did not. That is the server behaving correctly — session binding is the
    point — so the test client mirrors what the web client has to do.
    """
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    if response.status_code == 200:
        client.headers["X-Weft-CSRF"] = response.json()["csrf"]
    return response


async def do_refresh(client):
    """Refresh, re-syncing the CSRF header the way a real client must.

    Every token-issuing response (login, refresh, password change) mints a fresh CSRF
    token and returns it in the body. A client that does not follow along gets 403 on
    its next state-changing request.
    """
    response = await client.post("/api/auth/refresh")
    if response.status_code == 200:
        client.headers["X-Weft-CSRF"] = response.json()["csrf"]
    return response


# ---------------------------------------------------------------------------
# CSRF bootstrap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_csrf_endpoint_sets_a_readable_cookie(client):
    """Readable by design — the double-submit scheme needs same-origin script to copy
    it into a header, which is exactly what a cross-origin attacker cannot do."""
    response = await client.get("/api/auth/csrf")

    assert response.status_code == 200
    token = response.json()["csrf"]
    assert token
    assert response.cookies.get(cookies.csrf_cookie_name()) == token


@pytest.mark.asyncio
async def test_csrf_cookie_uses_the_host_prefix_when_secure(client):
    """__Host- stops a sibling subdomain writing a cookie this service then trusts."""
    await client.get("/api/auth/csrf")
    assert cookies.csrf_cookie_name() == "__Host-weft_csrf"


# ---------------------------------------------------------------------------
# Login — the enumeration-oracle tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_succeeds_with_correct_credentials(csrf_client, session):
    await make_user(session)
    response = await login(csrf_client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["username"] == "alice"
    assert body["access_token"]


@pytest.mark.asyncio
async def test_login_is_identical_for_unknown_user_and_wrong_password(csrf_client, session):
    """Status and body must match exactly. Any difference enumerates accounts."""
    await make_user(session, "alice")

    wrong_password = await login(csrf_client, "alice", "not-the-password")
    unknown_user = await login(csrf_client, "nobody", "not-the-password")

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


@pytest.mark.asyncio
async def test_inactive_user_gets_the_same_401_as_a_wrong_password(csrf_client, session):
    """Saying "this account is disabled" confirms the account exists."""
    await make_user(session, "alice", is_active=False)

    disabled = await login(csrf_client, "alice", PASSWORD)
    unknown = await login(csrf_client, "nobody", PASSWORD)

    assert disabled.status_code == 401
    assert disabled.json() == unknown.json()


@pytest.mark.asyncio
async def test_login_response_never_contains_the_password_hash(csrf_client, session):
    await make_user(session)
    body = (await login(csrf_client)).text

    assert "argon2" not in body
    assert "password_hash" not in body
    assert "token_version" not in body


@pytest.mark.asyncio
async def test_login_sets_an_httponly_refresh_cookie(csrf_client, session):
    """httpOnly is what limits an XSS to a 15-minute credential instead of a 30-day
    one. The header is inspected directly because the cookie jar does not expose
    attributes."""
    await make_user(session)
    response = await login(csrf_client)

    set_cookie = " ".join(response.headers.get_list("set-cookie"))
    assert cookies.refresh_cookie_name() in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.replace("SameSite=Strict", "SameSite=strict")


@pytest.mark.asyncio
async def test_refresh_token_is_not_in_the_response_body(csrf_client, session):
    """It belongs in the cookie only. A copy in the body is a copy JavaScript can read,
    which defeats httpOnly entirely."""
    await make_user(session)
    response = await login(csrf_client)

    raw_cookie = response.cookies.get(cookies.refresh_cookie_name())
    assert raw_cookie
    assert raw_cookie not in response.text


@pytest.mark.asyncio
async def test_login_updates_last_login_at(csrf_client, session):
    user = await make_user(session)
    assert user.last_login_at is None

    await login(csrf_client)
    await session.refresh(user)
    assert user.last_login_at is not None


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_without_csrf_is_rejected(client, session):
    await make_user(session)
    response = await login(client)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_from_a_foreign_origin_is_rejected(csrf_client, session):
    await make_user(session)
    response = await csrf_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_state_changing_request_without_an_origin_header_is_rejected(csrf_client, session):
    """A browser always sends Origin on a POST. A request with none is not a browser
    doing normal work, and allowing it would make the check optional."""
    await make_user(session)
    response = await csrf_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"Origin": ""},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_mismatched_csrf_header_and_cookie_is_rejected(csrf_client, session):
    await make_user(session)
    csrf_client.headers["X-Weft-CSRF"] = "a.b.c.d"
    response = await login(csrf_client)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_refresh_requires_csrf(csrf_client, session):
    """THE CSRF test. /refresh authenticates purely from an ambient cookie, which makes
    it the one route a cross-site request could drive to completion."""
    await make_user(session)
    await login(csrf_client)

    del csrf_client.headers["X-Weft-CSRF"]
    response = await csrf_client.post("/api/auth/refresh")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Authenticated access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_requires_a_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_me_returns_the_authenticated_user(csrf_client, session):
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


@pytest.mark.asyncio
async def test_tampered_token_is_rejected(csrf_client, session):
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token[:-1]}X"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_non_bearer_authorization_scheme_is_rejected(csrf_client, session):
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.get(
        "/api/auth/me", headers={"Authorization": f"Basic {token}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deactivating_a_user_invalidates_their_live_token(csrf_client, session):
    """The reason the auth dependency re-reads the database on every request. With the
    active flag cached in the token this would keep working until it expired."""
    user = await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    assert (await csrf_client.get("/api/auth/me", headers=auth)).status_code == 200

    user.is_active = False
    await session.commit()

    assert (await csrf_client.get("/api/auth/me", headers=auth)).status_code == 401


@pytest.mark.asyncio
async def test_bumping_token_version_invalidates_a_live_token(csrf_client, session):
    """`tv` is what makes logout-everywhere immediate rather than eventual."""
    user = await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    assert (await csrf_client.get("/api/auth/me", headers=auth)).status_code == 200

    user.token_version += 1
    await session.commit()

    assert (await csrf_client.get("/api/auth/me", headers=auth)).status_code == 401


# ---------------------------------------------------------------------------
# Rotation and reuse over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_rotates_the_cookie(csrf_client, session):
    await make_user(session)
    first = (await login(csrf_client)).cookies.get(cookies.refresh_cookie_name())

    response = await csrf_client.post("/api/auth/refresh")
    assert response.status_code == 200

    second = response.cookies.get(cookies.refresh_cookie_name())
    assert second and second != first


@pytest.mark.asyncio
async def test_reuse_kills_the_family_over_http(csrf_client, session):
    """The end-to-end proof: replay a spent cookie and the legitimate successor dies
    with it."""
    await make_user(session)
    first = (await login(csrf_client)).cookies.get(cookies.refresh_cookie_name())

    rotated = await do_refresh(csrf_client)
    assert rotated.status_code == 200
    current = rotated.cookies.get(cookies.refresh_cookie_name())

    # Put the spent cookie back in the jar — this is the attacker replaying a value
    # they captured earlier.
    csrf_client.cookies.set(cookies.refresh_cookie_name(), first)
    assert (await csrf_client.post("/api/auth/refresh")).status_code == 401

    # The client's current, legitimate token is now dead too: the whole family went.
    csrf_client.cookies.set(cookies.refresh_cookie_name(), current)
    assert (await csrf_client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_failed_refresh_clears_the_cookie(csrf_client, session):
    """Otherwise the client retries a rejected token forever — and if the rejection was
    reuse detection, every retry looks like a fresh attack."""
    await make_user(session)
    await login(csrf_client)

    csrf_client.cookies.set(cookies.refresh_cookie_name(), "a-token-that-was-never-issued")
    response = await csrf_client.post("/api/auth/refresh")

    assert response.status_code == 401
    set_cookie = " ".join(response.headers.get_list("set-cookie"))
    assert cookies.refresh_cookie_name() in set_cookie
    # An expiry in the past is how a deletion is spelled on the wire.
    assert 'Max-Age=0' in set_cookie or 'expires=Thu, 01 Jan 1970' in set_cookie.lower()


@pytest.mark.asyncio
async def test_refresh_without_a_cookie_is_401(csrf_client):
    assert (await csrf_client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_refresh_for_a_deactivated_user_tears_the_family_down(csrf_client, session):
    user = await make_user(session)
    await login(csrf_client)

    user.is_active = False
    await session.commit()

    assert (await csrf_client.post("/api/auth/refresh")).status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_works_without_an_access_token(csrf_client, session):
    """People log out after leaving a tab open overnight, by which point the access
    token expired hours ago. Requiring one fails exactly when it is most wanted."""
    await make_user(session)
    refresh_cookie = (await login(csrf_client)).cookies.get(cookies.refresh_cookie_name())

    response = await csrf_client.post("/api/auth/logout")
    assert response.status_code == 200

    # Logout clears BOTH cookies, so the client has to re-bootstrap CSRF before it can
    # make another state-changing request — exactly as a freshly loaded page would.
    token = (await csrf_client.get("/api/auth/csrf")).json()["csrf"]
    csrf_client.headers["X-Weft-CSRF"] = token

    # Even presenting the pre-logout refresh token, the family is revoked.
    csrf_client.cookies.set(cookies.refresh_cookie_name(), refresh_cookie)
    assert (await csrf_client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_logout_without_a_session_still_succeeds(csrf_client):
    """Logging out is not a thing that should be able to fail, and "no such session"
    would confirm which tokens are real."""
    assert (await csrf_client.post("/api/auth/logout")).status_code == 200


@pytest.mark.asyncio
async def test_logout_all_bumps_token_version_and_kills_other_devices(csrf_client, session):
    """Revoking refresh tokens alone would leave a 15-minute window in which a stolen
    access token still works. Both halves are required."""
    user = await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]
    before = user.token_version

    response = await csrf_client.post(
        "/api/auth/logout-all", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    await session.refresh(user)
    assert user.token_version == before + 1

    assert (await csrf_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )).status_code == 401


# ---------------------------------------------------------------------------
# Signed-in devices
# ---------------------------------------------------------------------------

def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def second_device(client, session):
    """Sign the same account in again from a fresh cookie jar, as another device would,
    and hand back its access token. The jar is restored, so the client stays device one."""
    jar = dict(client.cookies)
    csrf_header = client.headers["X-Weft-CSRF"]
    client.cookies.clear()
    client.headers["X-Weft-CSRF"] = (await client.get("/api/auth/csrf")).json()["csrf"]
    client.headers["User-Agent"] = "Mozilla/5.0 (Linux; Android 14) Chrome/152.0.0.0 Mobile"
    token = (await login(client)).json()["access_token"]
    del client.headers["User-Agent"]
    client.cookies.clear()
    for name, value in jar.items():
        client.cookies.set(name, value)
    client.headers["X-Weft-CSRF"] = csrf_header
    return token


@pytest.mark.asyncio
async def test_sessions_lists_every_device_this_one_first(csrf_client, session):
    await make_user(session)
    mine = (await login(csrf_client)).json()["access_token"]
    await second_device(csrf_client, session)

    response = await csrf_client.get("/api/auth/sessions", headers=bearer(mine))
    assert response.status_code == 200
    devices = response.json()
    assert [d["current"] for d in devices] == [True, False]
    assert devices[1]["browser"] == "Chrome" and devices[1]["os"] == "Android"
    assert all(d["active"] for d in devices)
    assert all(d["seen_seconds_ago"] >= 0 for d in devices)


@pytest.mark.asyncio
async def test_sessions_never_shows_another_accounts_devices(csrf_client, session):
    await make_user(session)
    await make_user(session, username="bob")
    await login(csrf_client, username="bob")
    mine = (await login(csrf_client)).json()["access_token"]

    devices = (await csrf_client.get("/api/auth/sessions", headers=bearer(mine))).json()
    assert len(devices) == 1 and devices[0]["current"]


@pytest.mark.asyncio
async def test_signing_out_another_device_refuses_its_very_next_request(csrf_client, session):
    """The point of the device row: without it the other device's access token kept
    working until it expired, up to fifteen minutes later."""
    await make_user(session)
    mine = (await login(csrf_client)).json()["access_token"]
    theirs = await second_device(csrf_client, session)
    assert (await csrf_client.get("/api/auth/me", headers=bearer(theirs))).status_code == 200

    devices = (await csrf_client.get("/api/auth/sessions", headers=bearer(mine))).json()
    other = next(d["id"] for d in devices if not d["current"])
    response = await csrf_client.delete(f"/api/auth/sessions/{other}", headers=bearer(mine))
    assert response.status_code == 200
    # Not this device's cookies: it signed someone else out, not itself.
    assert cookies.refresh_cookie_name() not in response.headers.get("set-cookie", "")

    assert (await csrf_client.get("/api/auth/me", headers=bearer(theirs))).status_code == 401
    assert (await csrf_client.get("/api/auth/me", headers=bearer(mine))).status_code == 200


@pytest.mark.asyncio
async def test_signing_out_this_device_from_the_list_is_a_logout(csrf_client, session):
    await make_user(session)
    mine = (await login(csrf_client)).json()["access_token"]
    devices = (await csrf_client.get("/api/auth/sessions", headers=bearer(mine))).json()

    response = await csrf_client.delete(
        f"/api/auth/sessions/{devices[0]['id']}", headers=bearer(mine)
    )
    assert response.status_code == 200
    cleared = response.headers.get_list("set-cookie")
    assert any(c.startswith(f"{cookies.refresh_cookie_name()}=") for c in cleared)

    assert (await csrf_client.get("/api/auth/me", headers=bearer(mine))).status_code == 401


@pytest.mark.asyncio
async def test_someone_elses_device_is_a_404_and_stays_signed_in(csrf_client, session):
    await make_user(session)
    await make_user(session, username="bob")
    bobs = (await login(csrf_client, username="bob")).json()["access_token"]
    bob_device = (await csrf_client.get("/api/auth/sessions", headers=bearer(bobs))).json()[0]
    mine = (await login(csrf_client)).json()["access_token"]

    for target in (bob_device["id"], "no-such-device"):
        response = await csrf_client.delete(f"/api/auth/sessions/{target}", headers=bearer(mine))
        assert response.status_code == 404

    assert (await csrf_client.get("/api/auth/me", headers=bearer(bobs))).status_code == 200


@pytest.mark.asyncio
async def test_signing_out_a_device_requires_csrf(csrf_client, session):
    await make_user(session)
    mine = (await login(csrf_client)).json()["access_token"]
    devices = (await csrf_client.get("/api/auth/sessions", headers=bearer(mine))).json()
    del csrf_client.headers["X-Weft-CSRF"]

    response = await csrf_client.delete(
        f"/api/auth/sessions/{devices[0]['id']}", headers=bearer(mine)
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_password_change_requires_the_current_password(csrf_client, session):
    """What stops a stolen access token, or an XSS holding one, from locking the real
    owner out of their own account."""
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.post(
        "/api/auth/password",
        json={"current_password": "wrong", "new_password": "a-brand-new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_password_change_succeeds_and_clears_the_must_change_flag(csrf_client, session):
    await make_user(session, must_change=True)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    user = (await session.execute(select(User))).scalars().one()
    await session.refresh(user)
    assert user.must_change_password is False
    assert passwords.verify_password("a-brand-new-password", user.password_hash)


@pytest.mark.asyncio
async def test_password_change_enforces_policy(csrf_client, session):
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_password_change_rejects_reusing_the_same_password(csrf_client, session):
    await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_password_change_ends_other_sessions(csrf_client, session):
    """A password change is a "my account may be compromised" action."""
    user = await make_user(session)
    token = (await login(csrf_client)).json()["access_token"]
    before = user.token_version

    await csrf_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )

    await session.refresh(user)
    assert user.token_version == before + 1
    assert (await csrf_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )).status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_account_may_change_its_password_despite_the_gate(csrf_client, session):
    """must_change_password blocks everything except this and logout. If it blocked
    this too the account would be permanently unusable."""
    await make_user(session, must_change=True)
    token = (await login(csrf_client)).json()["access_token"]

    response = await csrf_client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lockout_after_the_configured_number_of_failures(csrf_client, session):
    await make_user(session)

    for _ in range(5):
        assert (await login(csrf_client, "alice", "wrong")).status_code == 401

    locked = await login(csrf_client, "alice", "wrong")
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


@pytest.mark.asyncio
async def test_lockout_applies_to_the_correct_password_too(csrf_client, session):
    """Otherwise an attacker learns they found the password by seeing a different
    response, even while locked out."""
    await make_user(session)
    for _ in range(5):
        await login(csrf_client, "alice", "wrong")

    assert (await login(csrf_client, "alice", PASSWORD)).status_code == 429


@pytest.mark.asyncio
async def test_lockout_applies_to_usernames_that_do_not_exist(csrf_client):
    """If only real accounts could be locked out, the 429 itself would confirm the
    account exists."""
    for _ in range(5):
        await login(csrf_client, "ghost", "wrong")

    assert (await login(csrf_client, "ghost", "wrong")).status_code == 429


@pytest.mark.asyncio
async def test_one_users_lockout_does_not_affect_another(csrf_client, session):
    """Otherwise anyone can deny service to anyone by failing their login five times."""
    await make_user(session, "alice")
    await make_user(session, "bob")

    for _ in range(6):
        await login(csrf_client, "alice", "wrong")

    assert (await login(csrf_client, "bob", PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_a_successful_login_resets_the_failure_counter(csrf_client, session):
    """Counting since the last success, rather than within a window, is what stops an
    attacker pacing guesses to sit under the limit forever — but it must also let a
    legitimate user who mistypes a few times recover by getting it right."""
    await make_user(session)

    for _ in range(4):
        await login(csrf_client, "alice", "wrong")
    assert (await login(csrf_client, "alice", PASSWORD)).status_code == 200

    for _ in range(4):
        assert (await login(csrf_client, "alice", "wrong")).status_code == 401


@pytest.mark.asyncio
async def test_attempts_are_recorded_for_the_audit_trail(csrf_client, session):
    from app.models import LoginAttempt

    await make_user(session)
    await login(csrf_client, "alice", "wrong")
    await login(csrf_client, "alice", PASSWORD)

    attempts = (await session.execute(select(LoginAttempt))).scalars().all()
    assert {a.succeeded for a in attempts} == {False, True}
    assert all(a.username == "alice" for a in attempts)
