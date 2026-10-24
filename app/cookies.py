"""
Cookie naming and attributes, in one place.

Every cookie this service sets goes through here so that the security attributes cannot
drift between the route that sets one and the route that clears it — a logout that
clears a cookie with different attributes than the login set does not clear it at all,
and the user stays logged in while being told they are not.

----

**The ``__Host-`` versus ``Path`` conflict, resolved.**

The original design called for the refresh cookie to be both ``__Host-weft_refresh``
and scoped to ``Path=/api/auth``. Those are mutually exclusive: the ``__Host-`` prefix
is only honoured by browsers when the cookie is ``Secure``, has **no** ``Domain``, and
has ``Path=/`` exactly. A ``__Host-`` cookie with any other path is silently dropped —
and "silently" is the problem. Login would appear to succeed and refresh would never
work, with no error anywhere.

We keep the prefix and give up the path scoping, because they defend different things
and the prefix defends the worse one:

* ``Path=/api/auth`` only stops the cookie riding along on requests for static assets.
  Mildly nice — fewer copies in proxy logs — but not a boundary, since anything that
  can read those requests can read the auth requests too.
* ``__Host-`` stops a *sibling subdomain* from writing a cookie that this service will
  then read. Without it, an attacker who controls or MITMs any subdomain of the parent
  domain (including over plain HTTP) can inject a refresh cookie of their choosing.
  That is session fixation, and it is a real boundary.

So: prefix on, ``Path=/``.

----

**The local-development trap.** ``__Host-`` requires ``Secure``, and browsers refuse
``Secure`` cookies over plain ``http://``. On an HTTP dev box the prefixed cookie is
rejected by the browser with no console error, and login looks broken for reasons that
are invisible. ``security.cookie_prefix_host`` and ``security.cookie_secure`` exist to
be turned off together for that case, both default to true, and ``app/config.py`` warns
loudly on every boot when either is off.
"""

from fastapi import Response

from app import config, csrf

_REFRESH_BASE = "weft_refresh"

#: The refresh cookie must be readable by the refresh endpoint only in the sense that
#: JS can never see it. Path is "/" because of the __Host- constraint above.
COOKIE_PATH = "/"


def _prefixed(name: str) -> str:
    """Apply the ``__Host-`` prefix when the configuration allows it.

    The prefix is part of the cookie's *name*, so turning it on or off renames the
    cookie. A config flip therefore orphans any cookie already in a browser, which is
    harmless — the client falls back to logging in again — but is worth knowing when
    you flip it and everyone is logged out.
    """
    if config.get("security.cookie_prefix_host") and config.get("security.cookie_secure"):
        return f"__Host-{name}"
    return name


def refresh_cookie_name() -> str:
    return _prefixed(_REFRESH_BASE)


def csrf_cookie_name() -> str:
    return _prefixed(csrf.COOKIE_NAME)


def _samesite() -> str:
    value = str(config.get("security.cookie_samesite") or "strict").lower()
    # "none" would disable the browser's own CSRF protection and requires Secure to be
    # honoured at all. Refuse it rather than quietly weakening the service.
    return value if value in ("strict", "lax") else "strict"


def set_refresh_cookie(response: Response, token: str) -> None:
    """Set the refresh cookie. httpOnly — JavaScript must never be able to read this.

    An XSS in the app can still *use* the cookie by calling /api/auth/refresh from the
    page, but it cannot exfiltrate the token itself to another origin. That is the
    difference between an attacker who has a session while the tab is open and one who
    has a 30-day credential they can take home.
    """
    max_age = int(config.get("security.refresh_token_ttl_days") or 30) * 86400
    response.set_cookie(
        key=refresh_cookie_name(),
        value=token,
        max_age=max_age,
        path=COOKIE_PATH,
        httponly=True,
        secure=bool(config.get("security.cookie_secure")),
        samesite=_samesite(),
    )


def clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh cookie.

    Attributes must match what set_refresh_cookie used — a browser matches a deletion
    against name, path and domain, so clearing with a different path silently leaves
    the cookie in place. This is why both functions live next to each other.
    """
    response.delete_cookie(
        key=refresh_cookie_name(),
        path=COOKIE_PATH,
        httponly=True,
        secure=bool(config.get("security.cookie_secure")),
        samesite=_samesite(),
    )


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set the CSRF cookie. Deliberately NOT httpOnly.

    The entire double-submit mechanism depends on same-origin JavaScript being able to
    read this value and echo it in a header. A cross-origin attacker's script cannot,
    which is exactly the asymmetry being exploited. Making this httpOnly would break
    the scheme, not harden it.
    """
    response.set_cookie(
        key=csrf_cookie_name(),
        value=token,
        max_age=int(config.get("csrf.ttl_seconds") or 86400),
        path=COOKIE_PATH,
        httponly=False,
        secure=bool(config.get("security.cookie_secure")),
        samesite=_samesite(),
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        key=csrf_cookie_name(),
        path=COOKIE_PATH,
        httponly=False,
        secure=bool(config.get("security.cookie_secure")),
        samesite=_samesite(),
    )
