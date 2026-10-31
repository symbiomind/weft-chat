"""
Shared test fixtures.

ind-bridge has no conftest.py and builds each test's world inline. That is a reasonable
default, but three test files now need the same real-sqlite session and a fourth will
need it the moment routes exist, and a database fixture copy-pasted four times is a
database fixture that drifts — the copy that quietly forgets ``_install_sqlite_pragmas``
would make every foreign-key test in that file pass for the wrong reason.

Fixtures live here. Test *data* does not: each test still builds the users and tokens
it needs, visibly, in the test body.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import config
from app.db import _install_sqlite_pragmas
from app.models import Base

#: A complete, secure-by-default configuration for route tests. Cookie flags are left
#: ON (as in production) even though tests speak plain HTTP — the test client does not
#: enforce the Secure attribute, so this exercises the production cookie names,
#: including the __Host- prefix, rather than a weakened dev variant.
TEST_CONFIG = """
server:
  public_origin: "https://weft.test"
  jwt:
    secret: "test-jwt-secret-long-enough-for-hs256-aaaa"
    algorithm: HS256
    issuer: "weft-service"
    audience: "weft-web"
    leeway_seconds: 10
  csrf:
    secret: "test-csrf-secret-long-enough-for-hs256-bbb"
    ttl_seconds: 86400
  vault:
    key: "{vault_key}"
    key_id: "k1"
  security:
    access_token_ttl_seconds: 900
    refresh_token_ttl_days: 30
    session_absolute_ttl_days: 30
    cookie_secure: true
    cookie_prefix_host: true
    cookie_samesite: strict
    allowed_origins:
      - "https://weft.test"
    login_max_attempts: 5
    login_lockout_seconds: 900
    login_ip_window_seconds: 300
    login_ip_max: 20
    password_min_length: 12
  bootstrap:
    owner_username: ""
    owner_password: ""
"""


@pytest.fixture(autouse=True)
def clean_config():
    """Reset the config module around every test.

    config.py holds global state by design (it is read from everywhere), so without
    this a test that loads a config with, say, password_min_length: 4 would silently
    change the rules for every test that ran after it.
    """
    config._SERVER_CFG = {}
    config._config_loaded = False
    yield
    config._SERVER_CFG = {}
    config._config_loaded = False


@pytest.fixture(autouse=True)
def private_data_dirs(tmp_path, monkeypatch):
    """Point every directory the service writes files into at this test's own tmp.

    For EVERY test, not only the ones about pictures. The suite runs inside the dev
    container, where ``data/`` is the live install, and both picture stores have a sweep
    that deletes any file no row in the database references (the speech cache sweeps by
    age, which a test can provoke just as easily). Run against a test database,
    "no row references it" is every file there is. On 2026-10-08 deleting a weft in a
    weft test swept the live install's background pictures, because only the background
    tests had redirected the directory. A test file can still point these somewhere
    else; it cannot forget to point them away from the real ones.
    """
    monkeypatch.setenv("WEFT_BACKGROUNDS_DIR", str(tmp_path / "data-backgrounds"))
    monkeypatch.setenv("WEFT_ATTACHMENTS_DIR", str(tmp_path / "data-attachments"))
    monkeypatch.setenv("WEFT_SPEECH_DIR", str(tmp_path / "data-speech"))
    monkeypatch.setenv("WEFT_PUSH_ICONS_DIR", str(tmp_path / "data-push-icons"))


@pytest_asyncio.fixture
async def session(tmp_path):
    """A real on-disk sqlite database per test, with the production pragmas applied.

    On-disk rather than :memory: on purpose — foreign-key enforcement and WAL are
    exactly the behaviours that differ between a scratch in-memory database and the
    file the service actually runs against, and those are what several tests assert.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    _install_sqlite_pragmas(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()


@pytest_asyncio.fixture
async def app_config(tmp_path):
    """Load TEST_CONFIG into the config module for a route test."""
    import base64
    import os

    path = tmp_path / "config.yml"
    path.write_text(
        TEST_CONFIG.format(vault_key=base64.urlsafe_b64encode(os.urandom(32)).decode()),
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)
    return path


@pytest_asyncio.fixture
async def client(app_config, session):
    """An HTTP client speaking to the real app over ASGI, backed by the test database.

    Builds the app directly rather than importing the module-level instance so each
    test gets a clean route table, and overrides `get_session` so the test can inspect
    the same database the handlers wrote to.

    Lifespan is deliberately NOT run: migrations and bootstrap have their own tests,
    and running them here would mean every route test silently depends on both.
    """
    from fastapi import FastAPI

    from app import db as db_module
    from app.routes import attachments as attachment_routes
    from app.routes import auth as auth_routes
    from app.routes import chat as chat_routes
    from app.routes import guide as guide_routes
    from app.routes import providers as provider_routes
    from app.routes import sessions as session_routes
    from app.routes import speech as speech_routes
    from app.routes import wefts as weft_routes
    from app.routes import stt as stt_routes
    from app.routes import tips as tip_routes
    from app.routes import notifications as notification_routes
    from app.routes import push as push_routes

    app = FastAPI()
    app.include_router(attachment_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(provider_routes.router)
    app.include_router(weft_routes.router)
    app.include_router(session_routes.router)
    app.include_router(speech_routes.router)
    app.include_router(stt_routes.router)
    app.include_router(tip_routes.router)
    app.include_router(guide_routes.router)
    app.include_router(notification_routes.router)
    app.include_router(push_routes.router)
    app.include_router(push_routes.icon_router)

    async def _session_override():
        yield session

    app.dependency_overrides[db_module.get_session] = _session_override

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://weft.test",
        headers={"Origin": "https://weft.test"},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def csrf_client(client):
    """A client that has already fetched a CSRF token and sends it on every request.

    Mirrors what the real web client does once, at startup. Tests that want to prove
    CSRF is enforced use the plain `client` fixture instead.
    """
    response = await client.get("/api/auth/csrf")
    token = response.json()["csrf"]
    client.headers["X-Weft-CSRF"] = token
    return client
