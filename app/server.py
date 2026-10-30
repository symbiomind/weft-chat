"""
weft-service — application entry point.

Assembles the FastAPI app: lifespan, middleware, routers, and the static mount that
serves the Weft web app. Owns exactly one route of its own (``GET /health``);
everything else arrives from ``app/routes/``.

**Route ordering is a security property here, not a style preference.** The static
mount is registered LAST and matches everything, so any API route added outside the
reserved prefixes would be shadowed by a file in ``web/`` — or, worse, would shadow one.
``_assert_no_route_shadowing`` walks the table at startup and says so out loud.
"""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import (
    backgrounds, bootstrap, config, db, door, housekeeping, push_icons, security_headers,
    speech_cache,
)

logger = logging.getLogger(__name__)

#: Every API route must live under one of these. The static mount owns the rest.
RESERVED_PREFIXES = ("/api", "/ws", "/health")

#: Where the built (well — hand-written) web app lives.
WEB_ROOT = os.getenv("WEFT_WEB_ROOT", "web")


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # These three are conversational at INFO and drown out anything useful during a
    # request. Measured against ind-bridge's experience of the same libraries.
    for noisy in ("httpcore.http11", "httpcore.connection", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Alembic announces every autogenerate plugin it loads at INFO — seven lines on
    # every single boot, saying nothing an operator acts on. The migration lines
    # themselves (alembic.runtime.migration) stay at INFO because "Running upgrade"
    # is exactly what you want to see in a deploy log.
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown.

    Order matters: config first (everything reads it), then — once they exist —
    migrations, then bootstrap, then background tasks. Nothing in here may raise:
    a failure sets a degradation flag and the service still serves /health.
    """
    config.load_config()
    housekeeper: asyncio.Task | None = None

    # Migrations run before anything serves. sqlite has no concurrent-DDL story, so a
    # migration racing a request is not a thing to discover in production. A failure
    # here sets a flag rather than raising: the service comes up degraded so you can
    # curl /health and read what went wrong.
    await run_in_threadpool(db.run_migrations)

    # Warm the engine at startup rather than on the first request. Two reasons: it
    # opens a connection, which is what applies the sqlite pragmas (they are
    # per-connection, and a lazy engine means WAL and foreign_keys are not actually on
    # until someone happens to query); and an unreachable database becomes a startup
    # log line instead of a surprise 500 on whoever logs in first.
    if not db.migration_failed() and not await db.healthcheck():
        logger.error("Database is unreachable at startup — routes will fail.")
    elif not db.migration_failed():
        # Only after migrations succeeded: creating a user against a half-built schema
        # would fail in a much more confusing way than not trying.
        async with db.get_sessionmaker()() as session:
            await bootstrap.ensure_owner(session)
            # Pictures orphaned by a pick or provider deleted while nothing was looking
            # (the cascades are in the database; the files are not).
            await backgrounds.sweep(session)
            await push_icons.sweep(session)
        # Dead refresh tokens and old login attempts: once now, then on an
        # interval for as long as the service runs.
        housekeeper = asyncio.create_task(housekeeping.run(), name="auth-housekeeping")

    # Spoken clips past their age, or all of them if the cache has been turned off
    #. Files only, so it does not wait on the database.
    await run_in_threadpool(speech_cache.sweep)

    report = config.degradation_report()
    if report["auth"] != "ok":
        logger.error(
            "WEFT_JWT_SECRET is not set — every auth route will return 503. "
            "The static app is still served. See .env.example for how to generate one."
        )
    if report["vault"] != "ok":
        logger.error(
            "The provider vault is unavailable — /api/providers/* will return 503."
        )

    security_headers.warn_if_docs_exposed()
    _assert_no_route_shadowing(app)

    logger.info(
        "%s listening on port %s | %s",
        config.get("name"), config.get("port"),
        " ".join(f"{k}={v}" for k, v in report.items()),
    )

    # The MCP door's session manager. Starlette runs only the outermost
    # app's lifespan, so the one the door's own app carries never would.
    async with app.state.door.session_manager.run():
        yield

    if housekeeper is not None:
        housekeeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await housekeeper

    await db.dispose_engine()
    logger.info("weft-service shutting down.")


def _assert_no_route_shadowing(app: FastAPI) -> None:
    """Log loudly if any API route escaped the reserved prefixes.

    This is cheap insurance against a failure that is silent and slow: someone adds
    ``@router.get("/settings")`` a year from now, it works in tests (which hit the
    router directly), and in production the static mount answers it instead — or the
    route quietly shadows ``web/settings.html``. Either way nobody notices until a
    user does.
    """
    stray: list[str] = []

    def walk(routes, prefix: str = "") -> None:
        """Recurse, because this FastAPI version does not flatten included routers.

        ``app.routes`` holds one opaque ``_IncludedRouter`` per ``include_router`` call.
        It has no ``path`` and no ``routes`` — the real thing is behind
        ``original_router``, with any include-time prefix on ``include_context``.

        The original loop read ``path``, got None, and skipped. So this check — written
        to catch a route escaping the reserved prefixes and colliding with the static
        mount — has been walking past EVERY router-provided route since it was written,
        while cheerfully logging "route table clean". It only ever inspected /health.

        Caught 2026-09-13 while adding /api/chat: printing the table to confirm the new
        route existed showed four opaque wrappers where the routes should have been. The
        equivalent assertion in tests/test_openapi_spec.py was never fooled, because it
        reads the generated spec and that is flat — which is why nothing was actually
        broken, and also why nothing complained.
        """
        for route in routes:
            # The static mount itself is the one legitimate catch-all. Checked first:
            # a Mount also carries `routes` and an empty `path`.
            if isinstance(getattr(route, "app", None), StaticFiles):
                continue

            included = getattr(route, "original_router", None)
            if included is not None:
                context = getattr(route, "include_context", None)
                walk(included.routes, prefix + (getattr(context, "prefix", "") or ""))
                continue

            nested = getattr(route, "routes", None)
            path = getattr(route, "path", None)
            if nested and not path:
                walk(nested, prefix)
                continue

            if not path or path == "/":
                continue
            full = prefix + path
            if not full.startswith(RESERVED_PREFIXES):
                stray.append(full)

    walk(app.routes)

    if stray:
        logger.error(
            "ROUTE SHADOWING RISK: %d route(s) outside the reserved prefixes %s: %s. "
            "The static mount matches everything, so these are ambiguous. Move them "
            "under /api.",
            len(stray), RESERVED_PREFIXES, ", ".join(sorted(stray)),
        )
    else:
        logger.debug("Route table clean: every API route is under %s.", RESERVED_PREFIXES)


def create_app() -> FastAPI:
    """Build the application. A function, not a module global, so tests can build a
    fresh instance against a fresh config without import-order games."""
    # Logging first: create_app runs at import, before lifespan, and anything config
    # complains about here would otherwise land on a bare root logger with no
    # timestamp or level — which is how the two most security-relevant warnings this
    # service emits end up looking like stray print() output.
    _configure_logging()

    # load_config is idempotent, so this is the one real parse; lifespan's call is a
    # no-op. It happens here because docs_url must be decided in the constructor.
    config.load_config()
    expose_docs = bool(config.get("security.expose_docs"))

    app = FastAPI(
        title="weft-service",
        version="0.1.0",
        lifespan=lifespan,
        # /docs is a map of the auth surface. Off unless explicitly asked for.
        docs_url="/api/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if expose_docs else None,
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        """Always 200, always cheap, never authenticated.

        Carries the degradation report so a misconfigured deploy is visible from a
        curl instead of requiring a log dive. It deliberately exposes no counts,
        versions, or paths — an unauthenticated caller learns only which subsystems
        are up, which they would discover from a 503 anyway.
        """
        if db.migration_failed():
            db_status = "migration_failed"
        else:
            db_status = "ok" if await db.healthcheck() else "unreachable"

        return JSONResponse({
            "status": "ok",
            **config.degradation_report(),
            "db": db_status,
        })

    # Security headers wrap everything, including the static mount — the CSP has to
    # be on the HTML document itself to govern the page it describes.
    app.add_middleware(security_headers.SecurityHeadersMiddleware)

    # ---- routers go here, BEFORE the static mount ----
    from app.routes import attachments as attachment_routes
    from app.routes import auth as auth_routes
    from app.routes import chat as chat_routes
    from app.routes import door as door_routes
    from app.routes import notifications as notification_routes
    from app.routes import providers as provider_routes
    from app.routes import push as push_routes
    from app.routes import sessions as session_routes
    from app.routes import speech as speech_routes
    from app.routes import stt as stt_routes
    from app.routes import tips as tip_routes
    from app.routes import wefts as weft_routes

    app.include_router(attachment_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(provider_routes.router)
    app.include_router(weft_routes.router)
    app.include_router(session_routes.router)
    app.include_router(speech_routes.router)
    app.include_router(stt_routes.router)
    app.include_router(tip_routes.router)
    app.include_router(notification_routes.router)
    app.include_router(push_routes.router)
    app.include_router(push_routes.icon_router)
    app.include_router(door_routes.router)
    app.include_router(door_routes.upload_router)

    # The MCP door, served beside the routers rather than through one: it is an ASGI app
    # of its own, with its own protocol, behind its own key check. See app/door.py.
    app.state.door = door.build_server()
    door.mount(app, app.state.door)

    # ---- static mount, LAST, matches everything ----
    if os.path.isdir(WEB_ROOT):
        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    else:
        logger.error(
            "Web root %r does not exist — the API works but no UI is served. "
            "Check the ./web bind mount in docker-compose.yml.",
            WEB_ROOT,
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _configure_logging()
    config.load_config()

    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=int(config.get("port") or 5010),
        # SECURITY: we sit behind a reverse proxy that terminates TLS and sets
        # X-Forwarded-*. proxy_headers lets Secure-cookie and origin decisions see the
        # real scheme and client IP.
        #
        # forwarded_allow_ips should name the proxy, NOT '*'. It is '*' here only
        # because the container publishes to 127.0.0.1 (see docker-compose.example.yml),
        # which is the assumption that makes it safe. Publish that port more widely and
        # this line becomes a hole: an attacker sets X-Forwarded-Proto and the service
        # believes it.
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("WEFT_FORWARDED_ALLOW_IPS", "*"),
        # ONE WORKER, and do not raise it without reading this.
        #
        # The WebSocket connection registry is an in-process dict. With a second
        # worker, a "log out everywhere" handled by worker B walks B's registry — which
        # does not contain the socket worker A is holding. The user's HTTP requests
        # start failing immediately (they re-read token_version from the shared
        # database) while their live socket stays open and keeps receiving pushed
        # messages. A silent, partial revocation failure is the worst shape this bug
        # could take.
        #
        # The periodic re-validation sweep bounds the exposure at roughly its interval
        # rather than leaving it unbounded, but "revocation takes up to a minute" is
        # not what the rest of this design promises.
        #
        # Going multi-worker needs a pub/sub fan-out (redis) so revocation reaches
        # every worker's sockets. That is a hard new dependency for a service that
        # otherwise needs only sqlite, which is why it has not been done: the workload
        # is socket-bound rather than CPU-bound, so a second worker buys little here.
        workers=1,
    )
