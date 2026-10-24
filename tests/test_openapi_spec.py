"""
Tests for the committed OpenAPI spec (docs/openapi.json).

Two jobs, and the second is the one that earns this file its place.

**1. Drift.** `test_committed_spec_matches_the_app` fails when the golden file
disagrees with the live application. That is what makes generated documentation
trustworthy: it cannot silently go stale, so a reader can believe it.

**2. Policy.** The rest assert things about the *shape* of the API that no single route
would notice it had broken. These are cheap, and they catch a specific class of mistake
that is otherwise invisible in review:

* A response model that starts leaking `password_hash` or `token_version` because
  someone added a field to a shared serializer. The route tests check the endpoints
  they know about; this checks every schema at once, including ones added later.
* A route registered outside `/api` that the static mount would shadow (or that would
  shadow a file in `web/`). The runtime has the same check, but a failing test is seen
  before a deploy and a log line is seen after.

The policy tests read the *live* spec rather than the committed file on purpose — they
should fail the moment the code is wrong, not only once somebody regenerates.
"""

import json
import os

import pytest

from scripts.export_openapi import build_spec, canonical

SPEC_PATH = os.getenv("WEFT_OPENAPI_PATH", "docs/openapi.json")

#: Field names that must never appear in any response schema. Request bodies legitimately
#: carry passwords; responses never carry these.
FORBIDDEN_RESPONSE_FIELDS = {"password_hash", "token_version", "secret_ciphertext",
                             "secret_nonce", "token_hash"}

#: Mirrors app.server.RESERVED_PREFIXES. Anything outside these is ambiguous against the
#: catch-all static mount.
RESERVED_PREFIXES = ("/api", "/ws", "/health")


@pytest.fixture(scope="module")
def spec() -> dict:
    return build_spec()


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

def test_committed_spec_exists():
    assert os.path.exists(SPEC_PATH), (
        f"{SPEC_PATH} is missing. Generate it with:\n"
        f"  docker exec dev-weft-service python3 scripts/export_openapi.py"
    )


def test_committed_spec_matches_the_app(spec):
    """The whole point of a golden file.

    If this fails, the API changed. That is not necessarily wrong — it is just
    something that must be looked at, and the diff is the thing to look at.
    """
    with open(SPEC_PATH, encoding="utf-8") as handle:
        committed = handle.read()

    assert committed == canonical(spec), (
        f"{SPEC_PATH} is stale — the API surface has changed.\n\n"
        f"Regenerate:\n"
        f"  docker exec dev-weft-service python3 scripts/export_openapi.py\n\n"
        f"Then read the diff before committing it. This file IS the contract, and a\n"
        f"change here is a change other people's clients will feel."
    )


def test_committed_spec_is_canonical_json():
    """Guards against someone hand-editing the golden file. It is generated output; an
    edit here is a lie that survives until the next regeneration."""
    with open(SPEC_PATH, encoding="utf-8") as handle:
        raw = handle.read()

    assert raw == canonical(json.loads(raw)), (
        f"{SPEC_PATH} is not canonically formatted — it looks hand-edited. "
        f"Regenerate it instead of editing it."
    )


# ---------------------------------------------------------------------------
# Policy — what no individual route would notice
# ---------------------------------------------------------------------------

def _response_schema_names(spec: dict) -> set[str]:
    """Schemas reachable from any response body."""
    names: set[str] = set()
    for operations in spec["paths"].values():
        for operation in operations.values():
            for response in (operation.get("responses") or {}).values():
                for media in (response.get("content") or {}).values():
                    ref = (media.get("schema") or {}).get("$ref")
                    if ref:
                        names.add(ref.rsplit("/", 1)[-1])
    return names


def test_no_response_schema_exposes_internal_fields(spec):
    """The leak test.

    A shared serializer gaining one extra field is a two-character diff that no
    reviewer flags. Checking every response schema at once catches it, including for
    endpoints written long after this test.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    offenders: list[str] = []

    for name in _response_schema_names(spec):
        for field in (schemas.get(name, {}).get("properties") or {}):
            if field in FORBIDDEN_RESPONSE_FIELDS:
                offenders.append(f"{name}.{field}")

    assert not offenders, (
        f"Response schema(s) expose internal fields: {', '.join(sorted(offenders))}. "
        f"These must never leave the server."
    )


def test_every_route_lives_under_a_reserved_prefix(spec):
    """The static mount matches everything, so a route outside these prefixes is
    ambiguous: either it shadows a file in web/, or web/ shadows it."""
    stray = [p for p in spec["paths"] if not p.startswith(RESERVED_PREFIXES)]
    assert not stray, (
        f"Route(s) outside {RESERVED_PREFIXES}: {stray}. "
        f"Move them under /api or the static mount will fight them."
    )


def test_state_changing_operations_are_not_get(spec):
    """A state-changing operation behind GET is CSRF-exempt by our own rules (the CSRF
    dependency only guards POST/PUT/PATCH/DELETE) and is pre-fetchable by browsers and
    link scanners. `logout` as a GET is the classic instance of this bug."""
    suspicious = []
    for path, operations in spec["paths"].items():
        for verb in operations:
            if verb.lower() != "get":
                continue
            if any(word in path for word in ("logout", "login", "delete", "revoke", "password")):
                suspicious.append(f"GET {path}")

    assert not suspicious, (
        f"State-changing operation(s) exposed as GET: {suspicious}. "
        f"These bypass CSRF protection and can be triggered by a prefetch."
    )


def test_health_is_the_only_unprefixed_route(spec):
    """/health is deliberately outside /api so uptime checks and container health probes
    have a stable, conventional path. It should stay the only exception."""
    unprefixed = [p for p in spec["paths"] if not p.startswith("/api")]
    assert unprefixed == ["/health"], (
        f"Expected /health to be the only non-/api route, found: {unprefixed}"
    )


def test_auth_routes_are_all_under_one_prefix(spec):
    """Keeps the refresh cookie's Path and the CSRF surface reasoning in one place."""
    auth_paths = [p for p in spec["paths"] if "auth" in p]
    assert auth_paths, "expected some auth routes"
    assert all(p.startswith("/api/auth/") for p in auth_paths)


def test_spec_declares_no_external_servers(spec):
    """FastAPI adds no `servers` block by default. If one appears, someone hardcoded a
    hostname into the contract — which would then be wrong for every other deployment."""
    assert "servers" not in spec or not spec["servers"]


# ---------------------------------------------------------------------------
# The runtime twin of test_every_route_lives_under_a_reserved_prefix
# ---------------------------------------------------------------------------

def test_the_startup_shadowing_guard_actually_sees_router_routes(caplog):
    """The guard above reads the generated spec, which is flat. The one in
    ``app.server`` walks the live route table, which is not — and for a week it walked
    past every router-provided route while logging "route table clean", because this
    FastAPI version wraps each ``include_router`` in an opaque object with no ``path``.

    This test is the reason that cannot happen again: it plants a route outside the
    reserved prefixes and requires the guard to notice. Without the recursion, it
    reports nothing and passes vacuously — so the assertion is on the *complaint*, not
    on its absence.
    """
    import logging

    from fastapi import APIRouter

    from app.server import _assert_no_route_shadowing, create_app

    app = create_app()

    stray = APIRouter()

    @stray.get("/settings")
    async def _settings():  # pragma: no cover - never called
        return {}

    app.include_router(stray)

    with caplog.at_level(logging.ERROR, logger="app.server"):
        _assert_no_route_shadowing(app)

    # getMessage(), not .message: the latter only exists after a formatter has run,
    # and caplog does not run one.
    assert any("/settings" in record.getMessage() for record in caplog.records), (
        "The startup guard did not notice a route outside the reserved prefixes. "
        "It is probably failing to recurse into included routers again."
    )


def test_the_startup_shadowing_guard_is_quiet_on_a_clean_table():
    """The other half: a guard that complains about everything is a guard nobody reads."""
    import logging

    from app.server import _assert_no_route_shadowing, create_app

    logger = logging.getLogger("app.server")
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        _assert_no_route_shadowing(create_app())
    finally:
        logger.removeHandler(handler)

    assert records == []
