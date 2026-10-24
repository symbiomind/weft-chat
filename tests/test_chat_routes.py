"""
Tests for app/routes/chat.py — the relay that turns a weft into a connection.

The assertions that would matter:

* ``test_another_users_weft_cannot_be_used_to_chat`` — this route decrypts a stored key
  and posts it somewhere. Ownership is the whole game.
* ``test_the_provider_key_never_appears_in_the_stream`` — the reason the relay exists
  rather than the browser talking to the gateway directly.
* ``test_a_failure_before_the_first_token_is_a_502`` and
  ``test_a_failure_mid_stream_arrives_as_an_error_frame`` — the pair. Once a 200 is on
  the wire there is no status code left to change, so the two failures have to be
  reported two different ways, and a client has to handle both.

The upstream call is stubbed; its own parsing lives in tests/test_upstream.py.
"""

import json

import pytest

from app import upstream
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_provider_routes import SECRET, add_provider, signed_in
from tests.test_weft_routes import add_weft


def frames(text: str) -> list[dict]:
    """Decode our own SSE contract back into objects, ignoring the [DONE] sentinel."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        out.append(json.loads(payload))
    return out


def deltas(text: str) -> str:
    return "".join(f["delta"] for f in frames(text) if "delta" in f)


@pytest.fixture
def stub_chat(monkeypatch):
    """Stub the outbound chat call and record the url, secret and payload it got."""
    calls = []

    async def fake(base_url, secret, payload, *, extra_headers=None):
        calls.append({"base_url": base_url, "secret": secret, "payload": payload,
                      "extra_headers": extra_headers})

        async def gen():
            for piece in ("Hello", ", ", "m8"):
                yield piece

        return gen()

    monkeypatch.setattr(upstream, "stream_chat", fake)
    return calls


async def ready(csrf_client, session, model="iris"):
    """A signed-in user with one provider and one weft bound to it. Returns the weft id."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    return (await add_weft(csrf_client, "Iris", provider_id, model))["id"]


def say(weft_id, text="hello"):
    return {"weft_id": weft_id, "messages": [{"role": "user", "content": text}]}


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_picked_thread_answers(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert deltas(response.text) == "Hello, m8"
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_the_upstream_call_uses_the_decrypted_secret_and_the_picked_model(
    csrf_client, session, stub_chat,
):
    weft_id = await ready(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id, text="hello"))

    call = stub_chat[0]
    assert call["base_url"] == "https://api.example.test/v1"
    assert call["secret"] == SECRET
    assert call["payload"]["model"] == "iris"
    assert call["payload"]["stream"] is True
    assert call["payload"]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_a_wefts_headers_go_with_its_chat_request(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    await csrf_client.put(f"/api/wefts/{weft_id}/headers", json={
        "headers": [{"name": "X-Session-Id", "value": "demo"}],
    })

    await csrf_client.post("/api/chat", json=say(weft_id))

    assert stub_chat[0]["extra_headers"] == [["X-Session-Id", "demo"]]


@pytest.mark.asyncio
async def test_the_provider_key_never_appears_in_the_stream(csrf_client, session,
                                                            stub_chat):
    """The reason this route exists at all: the browser gets the answer, never the key."""
    weft_id = await ready(csrf_client, session)
    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_the_whole_conversation_is_forwarded(csrf_client, session, stub_chat):
    """The OpenAI protocol is stateless, so a plain provider only knows what we send.

    A bridge holds its own session and may ignore this; a gateway that does not would
    otherwise answer every turn as if it were the first.
    """
    weft_id = await ready(csrf_client, session)
    await csrf_client.post("/api/chat", json={
        "weft_id": weft_id,
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
        ],
    })

    assert len(stub_chat[0]["payload"]["messages"]) == 3


@pytest.mark.asyncio
async def test_a_newline_in_a_delta_does_not_break_the_frame(csrf_client, session,
                                                             monkeypatch):
    """SSE terminates a frame on a blank line, so an unescaped newline inside one would
    truncate the reply — and only for models that use paragraphs."""
    weft_id = await ready(csrf_client, session)

    async def fake(base_url, secret, payload, **_):
        async def gen():
            yield "line one\n\nline two"
        return gen()

    monkeypatch.setattr(upstream, "stream_chat", fake)
    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert deltas(response.text) == "line one\n\nline two"


# ---------------------------------------------------------------------------
# Failure, in its two different shapes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failure_before_the_first_token_is_a_502(csrf_client, session,
                                                         monkeypatch):
    weft_id = await ready(csrf_client, session)

    async def boom(base_url, secret, payload, **_):
        raise upstream.UpstreamError("Could not reach https://api.example.test/v1 (ConnectError).")

    monkeypatch.setattr(upstream, "stream_chat", boom)
    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 502
    assert "ConnectError" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_failure_mid_stream_arrives_as_an_error_frame(csrf_client, session,
                                                              monkeypatch):
    """The status line is long gone by now. The only honest place left to report a
    failure is inside the stream, next to the half-finished reply it belongs to."""
    weft_id = await ready(csrf_client, session)

    async def fake(base_url, secret, payload, **_):
        async def gen():
            yield "Half a sent"
            raise upstream.UpstreamError("the provider hung up")
        return gen()

    monkeypatch.setattr(upstream, "stream_chat", fake)
    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 200
    assert deltas(response.text) == "Half a sent"
    assert [f["error"] for f in frames(response.text) if "error" in f] == [
        "the provider hung up"
    ]
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_an_unexpected_mid_stream_exception_still_closes_the_stream(
    csrf_client, session, monkeypatch,
):
    """A generator holding a live socket can raise anything. Whatever it is, the client
    must get an explanation and a [DONE] rather than a reply that simply stops."""
    weft_id = await ready(csrf_client, session)

    async def fake(base_url, secret, payload, **_):
        async def gen():
            yield "start"
            raise RuntimeError("something nobody predicted")
        return gen()

    monkeypatch.setattr(upstream, "stream_chat", fake)
    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert deltas(response.text) == "start"
    assert any("broke mid-reply" in f.get("error", "") for f in frames(response.text))
    assert response.text.rstrip().endswith("data: [DONE]")


# ---------------------------------------------------------------------------
# Who may talk down which thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_weft_with_no_engine_is_refused_with_the_way_out(csrf_client, session,
                                                                 stub_chat):
    """On the menu but nobody to answer: added a moment ago, or its provider deleted."""
    await signed_in(csrf_client, session)
    weft_id = (await add_weft(csrf_client, "Jarvis"))["id"]

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 409
    assert "Jarvis has no engine" in response.json()["detail"]
    assert stub_chat == []


@pytest.mark.asyncio
async def test_deleting_the_provider_stops_the_weft_answering(csrf_client, session,
                                                              stub_chat):
    weft_id = await ready(csrf_client, session)
    provider_id = (await csrf_client.get(f"/api/wefts/{weft_id}")).json()["provider_id"]
    await csrf_client.delete(f"/api/providers/{provider_id}")

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_the_wefts_current_engine_is_what_answers(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    other = (await add_provider(csrf_client, name="Other",
                                url="https://other.example.test/v1")).json()["id"]
    await csrf_client.put(f"/api/wefts/{weft_id}/engine",
                          json={"provider_id": other, "model_id": "kimi"})

    await csrf_client.post("/api/chat", json=say(weft_id))

    assert stub_chat[0]["base_url"] == "https://other.example.test/v1"
    assert stub_chat[0]["payload"]["model"] == "kimi"


@pytest.mark.asyncio
async def test_another_users_weft_cannot_be_used_to_chat(csrf_client, session, stub_chat):
    """This route decrypts a key and posts it somewhere. If ownership scoping is ever
    dropped here, the endpoint becomes an exfiltration tool."""
    await signed_in(csrf_client, session, username="bob")
    provider_id = (await add_provider(csrf_client)).json()["id"]
    weft_id = (await add_weft(csrf_client, "Iris", provider_id, "iris"))["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 404
    assert stub_chat == []


@pytest.mark.asyncio
async def test_chat_requires_authentication(csrf_client, session):
    response = await csrf_client.post("/api/chat", json=say("any-id"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_requires_csrf(client, session, stub_chat):
    user = await make_user(session)
    client.headers["X-Weft-CSRF"] = (await client.get("/api/auth/csrf")).json()["csrf"]
    response = await client.post(
        "/api/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    del client.headers["X-Weft-CSRF"]

    assert (await client.post("/api/chat", json=say("any-id"))).status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_role_is_rejected(csrf_client, session, stub_chat):
    """Roles are forwarded verbatim into somebody else's parser."""
    weft_id = await ready(csrf_client, session)
    response = await csrf_client.post("/api/chat", json={
        "weft_id": weft_id,
        "messages": [{"role": "root", "content": "hi"}],
    })

    assert response.status_code == 422
    assert stub_chat == []


@pytest.mark.asyncio
async def test_an_empty_conversation_is_rejected(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    response = await csrf_client.post("/api/chat", json={
        "weft_id": weft_id, "messages": [],
    })

    assert response.status_code == 422
    assert stub_chat == []
