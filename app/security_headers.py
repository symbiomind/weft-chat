"""
Security response headers, including the CSP.

**Why there are no nonces here.** The original plan called for a per-response nonce,
because the prototypes carried one inline ``<style>`` and one inline ``<script>``. When
the client moved to ES modules every style and script became an external same-origin
file, which means ``'self'`` alone covers them. That is strictly better: no per-response
HTML rewriting, no nonce to leak or mismanage, and the browser gets to cache the assets
across page loads. The policy is also easier to read, which matters for the one header
most likely to be loosened by someone in a hurry.

**What the policy actually buys.** This service holds other people's provider API keys
and authenticates with cookies. The single worst outcome is script execution on this
origin, because that inherits the session. So:

* ``script-src 'self'`` — no inline script, no ``eval``, no CDN. An injected
  ``<script>`` tag does not run, and neither does an injected event-handler attribute.
* ``frame-ancestors 'none'`` — nobody frames this page, which is clickjacking's
  precondition. This is the modern replacement for ``X-Frame-Options``.
* ``base-uri 'none'`` — stops an injected ``<base>`` re-pointing every relative URL on
  the page (including ``/js/main.js``) at an attacker's host.
* ``form-action 'self'`` — an injected form cannot post the user's input elsewhere.
* ``object-src 'none'`` — no Flash-era plugin surface.

``connect-src 'self'`` covers same-origin WebSockets too: a ``wss://`` connection to the
page's own host matches ``'self'`` under CSP 3, so ``/ws`` needs no extra allowance.
"""

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import config

logger = logging.getLogger(__name__)

#: The policy, as ordered directives. A list of pairs rather than one long string so a
#: diff shows which directive changed rather than one enormous modified line.
_POLICY: tuple[tuple[str, str], ...] = (
    ("default-src", "'self'"),
    ("script-src", "'self'"),
    ("style-src", "'self'"),
    # data: for inline SVG and generated images; blob: for canvas exports and, later,
    # audio the TTS layer builds client-side.
    ("img-src", "'self' data: blob:"),
    ("font-src", "'self'"),
    ("media-src", "'self' blob:"),
    ("connect-src", "'self'"),
    ("frame-ancestors", "'none'"),
    ("base-uri", "'none'"),
    ("object-src", "'none'"),
    ("form-action", "'self'"),
)

#: Features this app does not use. Denying them means an injected script cannot turn
#: them on either.
_PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer", "camera", "display-capture", "geolocation",
        "gyroscope", "magnetometer", "payment", "usb", "interest-cohort",
    )
) + (
    # Used: replies are read aloud as they arrive, which is autoplay.
    # Same origin only, so an embedded frame still cannot.
    ", autoplay=(self)"
)

#: Docs paths, which need a relaxed policy because FastAPI serves Swagger UI from a CDN.
_DOCS_PATHS = frozenset({"/api/docs", "/api/redoc", "/api/openapi.json"})


def content_security_policy() -> str:
    return "; ".join(f"{name} {value}" for name, value in _POLICY)


def _docs_policy() -> str:
    """A deliberately weaker policy, applied ONLY to the docs paths and ONLY when
    ``security.expose_docs`` is explicitly enabled.

    FastAPI serves Swagger UI from cdn.jsdelivr.net, so the strict policy blocks it
    entirely. Rather than weaken the policy for the whole application, the holes are
    confined to two paths that are off by default and documented as a dev convenience.
    The honest alternative is self-hosting those assets; until someone needs docs on a
    deployed instance, this is the smaller change.
    """
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )


def headers_for(path: str) -> dict[str, str]:
    """Build the header set for one response."""
    expose_docs = bool(config.get("security.expose_docs"))

    policy = _docs_policy() if (expose_docs and path in _DOCS_PATHS) else content_security_policy()

    headers = {
        "Content-Security-Policy": policy,
        # Stops a browser guessing a content type. Without it, a text file that happens
        # to start with markup can be sniffed as HTML and executed on this origin —
        # which is the whole attack against user-uploaded files.
        "X-Content-Type-Options": "nosniff",
        # URLs can carry invite tokens in a fragment and session context in a path.
        # Nothing outbound needs to know where the user came from.
        "Referrer-Policy": "no-referrer",
        # Severs window.opener between this page and anything it opens, so a popup
        # cannot navigate the app to a phishing page.
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": _PERMISSIONS_POLICY,
        # CACHING, and both values are deliberate.
        #
        # API responses carry access tokens, usernames and provider hints, so `no-store`
        # — do not write them to disk, do not hand them to an intermediary, do not leave
        # them for whoever uses the machine next.
        #
        # The static app gets `no-cache`, which does NOT mean "do not cache". It means
        # "cache it, but always ask first". With the ETag that Starlette already sends,
        # a revalidation is one conditional request answering 304 with no body.
        #
        # Without this header there is no header at all, and a browser then caches
        # heuristically — it invents a freshness lifetime and serves from memory without
        # asking. For an app with no build step and no content-hashed filenames that is
        # a genuine hazard, and it is worse in an installed PWA, where there is no
        # address bar to hard-refresh from. The symptom is a change that is definitely
        # deployed, definitely served, and definitely not running (where
        # it cost a morning of "I am unsure if anything changed").
        "Cache-Control": "no-store" if path.startswith(("/api", "/ws")) else "no-cache",
    }

    # HSTS only when we believe we are actually behind TLS. Sending it over plain HTTP
    # is not merely useless: if someone browses a dev instance at http://localhost and
    # the header sticks, their browser will refuse plain HTTP to that host afterwards.
    if config.get("security.cookie_secure") and config.get("security.hsts"):
        directives = "max-age=31536000"
        if config.get("security.hsts_include_subdomains"):
            directives += "; includeSubDomains"
        headers["Strict-Transport-Security"] = directives

    return headers


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware.

    Not ``BaseHTTPMiddleware``: that wraps the response in a way that breaks streaming
    and interferes with WebSocket scopes, and this service will be doing both. Editing
    the headers on the ``http.response.start`` message is the cheapest correct place.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket handshakes carry no response headers to decorate. Their
            # defence is the Origin check at accept time, not a CSP.
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        extra = headers_for(path)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {name.lower() for name, _ in headers}
                for name, value in extra.items():
                    # Never clobber a header a handler set deliberately.
                    if name.lower().encode() not in existing:
                        headers.append((name.lower().encode(), value.encode()))
            await send(message)

        await self.app(scope, receive, send_with_headers)


def warn_if_docs_exposed() -> None:
    if config.get("security.expose_docs"):
        logger.warning(
            "security.expose_docs is TRUE — /api/docs is served with a RELAXED "
            "Content-Security-Policy that permits scripts from cdn.jsdelivr.net, "
            "because that is where FastAPI loads Swagger UI from. Swagger's "
            "'Try it out' also fires authenticated requests from the browser. "
            "Development convenience only; turn it off anywhere reachable."
        )
