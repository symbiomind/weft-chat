"""
Tests for app/routes/providers.py — the vault's HTTP surface. The wefts that use the
providers are tested in tests/test_weft_routes.py.

app/vault.py's own tests prove the cryptography. These prove the wiring, which is where
this class of feature actually leaks:

* ``test_no_response_ever_contains_the_plaintext_secret`` — the whole product promise in
  one assertion. Every provider response is searched for the token that was submitted.
* ``test_another_users_provider_is_not_found`` / ``..._cannot_be_edited`` /
  ``..._cannot_be_deleted`` / ``..._models_are_not_listed`` — ownership scoping on every
  verb, because it only takes one handler forgetting the ``user_id`` clause.
* ``test_writes_require_csrf`` — these are state-changing routes on a cookie-bearing
  origin.
* ``test_editing_without_a_secret_keeps_the_stored_one`` — the reason the edit form can
  leave the token field blank at all.
* ``test_deleting_a_provider_leaves_its_wefts_standing`` — a provider is a binding, not
  a parent.

The upstream call is stubbed. Its own parsing and failure mapping are covered in
tests/test_upstream.py; what matters here is that the route opens the right secret,
hands it over, and turns an UpstreamError into a 502 instead of a 500.
"""

import pytest
from sqlalchemy import select

from app import upstream, vault
from app.models import ProviderSecret, Weft
from tests.test_auth_routes import PASSWORD, login, make_user

SECRET = "sk-or-v1-topsecret-7f3a"


async def signed_in(csrf_client, session, username="alice"):
    user = await make_user(session, username=username)
    response = await login(csrf_client, username=username)
    assert response.status_code == 200
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return user


async def add_provider(client, name="My connection", url="https://api.example.test/v1",
                       secret=SECRET):
    return await client.post(
        "/api/providers", json={"name": name, "base_url": url, "secret": secret}
    )


@pytest.fixture
def stub_models(monkeypatch):
    """Replace the outbound call, and record what it was given.

    Recording the secret is the point: it proves the route decrypted the right row,
    which no assertion on the response body can show.
    """
    calls = []

    async def fake(base_url, secret):
        calls.append((base_url, secret))
        return [
            {"id": "zeta-model", "owned_by": "them"},
            {"id": "alpha-model", "owned_by": "them"},
        ]

    monkeypatch.setattr(upstream, "fetch_models", fake)
    return calls


# ---------------------------------------------------------------------------
# The promise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_response_ever_contains_the_plaintext_secret(csrf_client, session,
                                                              stub_models):
    """The product promise, asserted against every provider response there is.

    A new endpoint that returns the secret would have to be added *and* this test
    deleted. That is the level of deliberateness the decision deserves.
    """
    await signed_in(csrf_client, session)
    created = await add_provider(csrf_client)
    provider_id = created.json()["id"]

    bodies = [
        created.text,
        (await csrf_client.get("/api/providers")).text,
        (await csrf_client.patch(f"/api/providers/{provider_id}",
                                 json={"name": "Renamed"})).text,
        (await csrf_client.get(f"/api/providers/{provider_id}/models")).text,
        (await csrf_client.get("/api/wefts")).text,
    ]

    for body in bodies:
        assert SECRET not in body


@pytest.mark.asyncio
async def test_the_stored_secret_is_not_plaintext_in_the_database(csrf_client, session):
    await signed_in(csrf_client, session)
    await add_provider(csrf_client)

    row = (await session.execute(select(ProviderSecret))).scalars().one()
    assert SECRET.encode() not in row.secret_ciphertext
    assert len(row.secret_nonce) == vault.NONCE_BYTES
    assert row.key_id == "k1"


@pytest.mark.asyncio
async def test_a_read_returns_only_the_hint(csrf_client, session):
    await signed_in(csrf_client, session)
    body = (await add_provider(csrf_client)).json()

    assert body["hint"] == "7f3a"
    assert body["has_secret"] is True
    assert "secret" not in body


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_returns_the_provider(csrf_client, session):
    await signed_in(csrf_client, session)
    response = await add_provider(csrf_client)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "My connection"
    assert body["base_url"] == "https://api.example.test/v1"
    assert body["weft_count"] == 0


@pytest.mark.asyncio
async def test_a_trailing_slash_is_normalised_away(csrf_client, session):
    """So that ``{base_url}/models`` never becomes ``//models`` — which some gateways
    404 and others route somewhere surprising."""
    await signed_in(csrf_client, session)
    body = (await add_provider(csrf_client, url="https://api.example.test/v1/")).json()
    assert body["base_url"] == "https://api.example.test/v1"


@pytest.mark.asyncio
async def test_a_provider_with_no_secret_is_allowed(csrf_client, session):
    """A llama.cpp or a bridge on the private network needs no bearer, and inventing a
    placeholder would mean sending a bogus Authorization header upstream."""
    await signed_in(csrf_client, session)
    body = (await add_provider(csrf_client, secret="")).json()

    assert body["has_secret"] is True  # sealed empty string, not a NULL column
    assert body["hint"] is None


@pytest.mark.asyncio
async def test_a_non_http_url_is_rejected(csrf_client, session):
    """file:// would read the container's filesystem."""
    await signed_in(csrf_client, session)
    response = await add_provider(csrf_client, url="file:///etc/passwd")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_url_with_credentials_is_rejected(csrf_client, session):
    """We show base_url back to the user in the clear and log it. A token in there
    would be a secret stored outside the vault."""
    await signed_in(csrf_client, session)
    response = await add_provider(csrf_client, url="https://user:pass@api.example.test/v1")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_names_are_rejected(csrf_client, session):
    """Two providers called the same thing would make every list of them ambiguous."""
    await signed_in(csrf_client, session)
    assert (await add_provider(csrf_client)).status_code == 201
    assert (await add_provider(csrf_client)).status_code == 409


@pytest.mark.asyncio
async def test_two_users_may_use_the_same_provider_name(csrf_client, session):
    """The uniqueness is per user, not global — otherwise the first person to name one
    'OpenRouter' takes the name away from everyone else."""
    await signed_in(csrf_client, session, username="alice")
    assert (await add_provider(csrf_client)).status_code == 201

    await make_user(session, username="bob")
    response = await login(csrf_client, username="bob", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    assert (await add_provider(csrf_client)).status_code == 201


# ---------------------------------------------------------------------------
# Edit and delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_editing_without_a_secret_keeps_the_stored_one(csrf_client, session,
                                                             stub_models):
    """Why the edit form can leave the token field blank.

    Nobody can read the secret back, so the form has nothing to prefill it with. An
    edit that cleared the token whenever the field was empty would silently break every
    provider the moment someone renamed one.
    """
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    response = await csrf_client.patch(
        f"/api/providers/{provider_id}", json={"name": "Renamed"}
    )
    assert response.status_code == 200
    assert response.json()["hint"] == "7f3a"

    await csrf_client.get(f"/api/providers/{provider_id}/models")
    assert stub_models[-1][1] == SECRET


@pytest.mark.asyncio
async def test_an_empty_secret_clears_the_token(csrf_client, session, stub_models):
    """The other half of the three-state field: null leaves it, "" clears it."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    response = await csrf_client.patch(
        f"/api/providers/{provider_id}", json={"secret": ""}
    )
    assert response.json()["hint"] is None

    await csrf_client.get(f"/api/providers/{provider_id}/models")
    assert stub_models[-1][1] == ""


@pytest.mark.asyncio
async def test_replacing_a_secret_uses_a_fresh_nonce(csrf_client, session):
    """Re-encrypting in place with the row's existing nonce would be the GCM
    catastrophe. The only safe update is a full re-seal."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    before = (await session.execute(select(ProviderSecret))).scalars().one().secret_nonce

    await csrf_client.patch(f"/api/providers/{provider_id}", json={"secret": "sk-new-9999"})
    await session.refresh(
        (await session.execute(select(ProviderSecret))).scalars().one()
    )
    after = (await session.execute(select(ProviderSecret))).scalars().one().secret_nonce

    assert before != after


@pytest.mark.asyncio
async def test_delete_removes_the_provider(csrf_client, session):
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    assert (await csrf_client.delete(f"/api/providers/{provider_id}")).status_code == 204
    assert (await csrf_client.get("/api/providers")).json() == []


@pytest.mark.asyncio
async def test_deleting_a_provider_leaves_its_wefts_standing(csrf_client, session):
    """Deleting a provider used to cascade away every pick on it, and
    with them their colours, voices, pictures and history settings. Now it unbinds."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    weft = (await csrf_client.post("/api/wefts", json={
        "name": "Jarvis", "provider_id": provider_id, "model_id": "alpha-model",
    })).json()
    await csrf_client.put(f"/api/wefts/{weft['id']}/colours",
                          json={"colour_a": "#112233", "colour_b": "#445566"})

    await csrf_client.delete(f"/api/providers/{provider_id}")

    [left] = (await csrf_client.get("/api/wefts")).json()
    assert left["name"] == "Jarvis"
    assert left["provider_id"] is None and left["provider_name"] is None
    assert (left["colour_a"], left["colour_b"]) == ("#112233", "#445566")
    assert len((await session.execute(select(Weft))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def another_users_provider(csrf_client, session):
    """Create a provider as bob, then leave the client signed in as alice."""
    await signed_in(csrf_client, session, username="bob")
    provider_id = (await add_provider(csrf_client)).json()["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return provider_id


@pytest.mark.asyncio
async def test_another_users_provider_is_not_listed(csrf_client, session):
    await another_users_provider(csrf_client, session)
    assert (await csrf_client.get("/api/providers")).json() == []


@pytest.mark.asyncio
async def test_another_users_provider_cannot_be_edited(csrf_client, session):
    provider_id = await another_users_provider(csrf_client, session)
    response = await csrf_client.patch(
        f"/api/providers/{provider_id}", json={"base_url": "https://evil.test/v1"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_another_users_provider_cannot_be_deleted(csrf_client, session):
    provider_id = await another_users_provider(csrf_client, session)
    assert (await csrf_client.delete(f"/api/providers/{provider_id}")).status_code == 404


@pytest.mark.asyncio
async def test_another_users_provider_models_are_not_listed(csrf_client, session,
                                                            stub_models):
    """The one route that decrypts. Reaching it for someone else's row would be the
    exfiltration path the vault's associated data exists as a second line against."""
    provider_id = await another_users_provider(csrf_client, session)
    response = await csrf_client.get(f"/api/providers/{provider_id}/models")

    assert response.status_code == 404
    assert stub_models == []


@pytest.mark.asyncio
async def test_providers_require_authentication(csrf_client, session):
    """No bearer at all. Every route here holds somebody's key material."""
    for method, path in (
        ("get", "/api/providers"),
        ("get", "/api/wefts"),
    ):
        assert (await getattr(csrf_client, method)(path)).status_code == 401


@pytest.mark.asyncio
async def test_writes_require_csrf(client, session):
    """The plain client sends no CSRF header. These are state-changing routes on an
    origin that carries cookies."""
    user = await make_user(session)

    # Login is itself a CSRF-protected POST, so the client has to hold a token to get
    # in at all. It then drops it — which is exactly the state a real client would be
    # in if it forgot to re-sync after a token-issuing response.
    client.headers["X-Weft-CSRF"] = (await client.get("/api/auth/csrf")).json()["csrf"]
    response = await client.post(
        "/api/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    del client.headers["X-Weft-CSRF"]

    assert (await add_provider(client)).status_code == 403


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_models_are_fetched_with_the_decrypted_secret(csrf_client, session,
                                                            stub_models):
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    response = await csrf_client.get(f"/api/providers/{provider_id}/models")

    assert response.status_code == 200
    assert stub_models == [("https://api.example.test/v1", SECRET)]
    assert [m["id"] for m in response.json()["models"]] == ["alpha-model", "zeta-model"]


@pytest.mark.asyncio
async def test_models_a_weft_uses_are_listed_first_with_its_name(csrf_client, session,
                                                                 stub_models):
    """The engine picker's useful question is "which one is Jarvis on", which four
    hundred alphabetical models otherwise bury."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    await csrf_client.post("/api/wefts", json={
        "name": "Jarvis", "provider_id": provider_id, "model_id": "zeta-model",
    })

    models = (await csrf_client.get(f"/api/providers/{provider_id}/models")).json()["models"]

    assert [m["id"] for m in models] == ["zeta-model", "alpha-model"]
    assert models[0]["wefts"] == ["Jarvis"]
    assert models[1]["wefts"] == []


@pytest.mark.asyncio
async def test_the_provider_list_counts_the_wefts_it_answers(csrf_client, session):
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    for name in ("One", "Two"):
        await csrf_client.post("/api/wefts", json={
            "name": name, "provider_id": provider_id, "model_id": "alpha-model",
        })
    await csrf_client.post("/api/wefts", json={"name": "Unbound"})

    [provider] = (await csrf_client.get("/api/providers")).json()
    assert provider["weft_count"] == 2


@pytest.mark.asyncio
async def test_an_upstream_failure_is_a_502_not_a_500(csrf_client, session, monkeypatch):
    """This service is fine; the thing it was asked to talk to is not. A 500 would send
    the user looking for a bug in Weft."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    async def boom(base_url, secret):
        raise upstream.UpstreamError("Could not reach https://api.example.test/v1 (ConnectError).")

    monkeypatch.setattr(upstream, "fetch_models", boom)
    response = await csrf_client.get(f"/api/providers/{provider_id}/models")

    assert response.status_code == 502
    assert "ConnectError" in response.json()["detail"]
