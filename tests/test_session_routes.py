"""
Tests for the session store: a weft that keeps its thread in memory-mcp-ce.

The assertions that would matter:

* ``test_the_chat_relay_appends_the_exchange`` — the one place a thread is written, and
  what it writes: both halves, the user's words first.
* ``test_a_failing_store_costs_the_saved_turn_not_the_reply`` — the store is a
  convenience; the reply the user already read must never depend on it.
* ``test_another_users_store_cannot_hold_my_thread`` — the store is opened with a token
  from the vault. Whose token is the whole game, as it is for a voice.
* ``test_a_typed_session_id_joins_without_claiming`` — a typed id is how a weft joins a
  session somebody else writes, so it must not be re-created or re-owned.

The store's HTTP is stubbed; the relay's own upstream is stubbed as in
tests/test_chat_routes.py.
"""

import pytest

from app import sessions, upstream
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_chat_routes import deltas, frames, say, stub_chat  # noqa: F401 (fixture)
from tests.test_provider_routes import SECRET, add_provider, signed_in
from tests.test_weft_routes import add_weft, set_history

STORE_SECRET = "mmce-session-token-9c1e"


async def add_store(client, name="Sessions", url="http://mmce.test:5005"):
    return await client.post(
        "/api/providers",
        json={"name": name, "base_url": url, "secret": STORE_SECRET, "kind": "session"},
    )


async def ready(csrf_client, session, model="bridge/iris"):
    """Alice, a weft bound to a chat provider, and a session store. Returns both ids."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    weft_id = (await add_weft(csrf_client, "Iris", provider_id, model))["id"]
    store_id = (await add_store(csrf_client)).json()["id"]
    return weft_id, store_id


@pytest.fixture
def fake_store(monkeypatch):
    """An in-memory session store behind the upstream functions, recording every call."""
    state = {"sessions": {}, "calls": [], "fail": None}

    def check(secret):
        assert secret == STORE_SECRET
        if state["fail"]:
            raise upstream.SessionStoreError(state["fail"])

    async def handshake(base_url, secret, session_id):
        check(secret)
        state["calls"].append(("handshake", session_id))
        found = state["sessions"].get(session_id)
        return None if found is None else {"last_seq": len(found["events"]), **found}

    async def ensure(base_url, secret, session_id, *, agent, user):
        check(secret)
        state["calls"].append(("ensure", session_id, agent, user))
        state["sessions"].setdefault(session_id, {"agent": agent, "user": user, "events": []})
        return state["sessions"][session_id]

    async def append(base_url, secret, session_id, events, attempts=3):
        check(secret)
        state["calls"].append(("append", session_id))
        stored = state["sessions"][session_id]["events"]
        start = len(stored) + 1
        # One append is one exchange, so one turn, as the relay writes them.
        turn = max((e.get("turn") or 0 for e in stored), default=0) + 1
        stored.extend({**e, "seq": start + i, "turn": turn} for i, e in enumerate(events))
        return {"last_seq": len(stored)}

    async def tail(base_url, secret, session_id, *, last_turns):
        check(secret)
        state["calls"].append(("tail", session_id, last_turns))
        found = state["sessions"].get(session_id)
        if found is None:
            return None
        return {"events": found["events"], "has_more": False, "last_seq": len(found["events"])}

    async def since(base_url, secret, session_id, *, after_seq, limit):
        check(secret)
        state["calls"].append(("since", session_id, after_seq))
        found = state["sessions"].get(session_id)
        if found is None:
            return None
        later = [e for i, e in enumerate(found["events"], start=1) if i > after_seq]
        return {"events": later[:limit], "has_more": len(later) > limit,
                "last_seq": len(found["events"])}

    monkeypatch.setattr(upstream, "session_since", since)
    monkeypatch.setattr(upstream, "session_handshake", handshake)
    monkeypatch.setattr(upstream, "ensure_session", ensure)
    monkeypatch.setattr(upstream, "append_session_events", append)
    monkeypatch.setattr(upstream, "session_tail", tail)
    return state


# ---------------------------------------------------------------------------
# The derived id
# ---------------------------------------------------------------------------

def test_a_derived_id_is_stable_and_in_the_stores_alphabet():
    first = sessions.derived_session_id("alice", "0123-4567")
    again = sessions.derived_session_id("alice", "0123-4567")

    assert first == again == "weft.alice.0123-4567"
    import re
    assert re.match(sessions.SESSION_ID_PATTERN, first)


def test_a_derived_id_tells_two_wefts_apart():
    """Two wefts on the same engine are two threads, in the window and here."""
    assert sessions.derived_session_id("alice", "a") != sessions.derived_session_id("alice", "b")


def test_a_derived_id_fits_the_store_however_odd_the_username():
    derived = sessions.derived_session_id("a l/i c e" * 40, "0123456789")
    assert len(derived) <= 128
    import re
    assert re.match(sessions.SESSION_ID_PATTERN, derived)


# ---------------------------------------------------------------------------
# Choosing a store
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_weft_can_keep_its_thread_in_a_store(csrf_client, session):
    weft_id, store_id = await ready(csrf_client, session)

    response = await set_history(
        csrf_client, weft_id, "session", session_provider_id=store_id,
    )

    assert response.status_code == 200
    weft = response.json()
    assert weft["history"] == "session"
    assert weft["session_provider_id"] == store_id
    assert weft["session_id"] is None
    assert weft["session_key"] == f"weft.alice.{weft_id}"

    menu = (await csrf_client.get("/api/wefts")).json()
    assert menu[0]["session_key"] == weft["session_key"]


@pytest.mark.asyncio
async def test_session_without_a_store_is_refused(csrf_client, session):
    weft_id, _ = await ready(csrf_client, session)
    response = await set_history(csrf_client, weft_id, "session")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_chat_provider_is_not_a_store(csrf_client, session):
    weft_id, _ = await ready(csrf_client, session)
    provider_id = (await csrf_client.get(f"/api/wefts/{weft_id}")).json()["provider_id"]
    response = await set_history(
        csrf_client, weft_id, "session", session_provider_id=provider_id,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_another_users_store_cannot_hold_my_thread(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    bobs_store = (await add_store(csrf_client)).json()["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    weft_id = (await add_weft(csrf_client, "Iris"))["id"]

    response = await set_history(
        csrf_client, weft_id, "session", session_provider_id=bobs_store,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_typed_session_id_must_be_in_the_stores_alphabet(csrf_client, session):
    weft_id, store_id = await ready(csrf_client, session)
    response = await set_history(
        csrf_client, weft_id, "session",
        session_provider_id=store_id, session_id="has/a/slash",
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_store_has_no_models(csrf_client, session):
    _, store_id = await ready(csrf_client, session)
    assert (await csrf_client.get(f"/api/providers/{store_id}/models")).status_code == 409


@pytest.mark.asyncio
async def test_the_store_survives_moving_away_from_session(csrf_client, session):
    """Choosing `memory` keeps the store chosen, so choosing `session` again finds it."""
    weft_id, store_id = await ready(csrf_client, session)
    await set_history(
        csrf_client, weft_id, "session", session_provider_id=store_id,
    )
    response = await set_history(
        csrf_client, weft_id, "memory", session_provider_id=store_id,
    )
    assert response.json()["session_provider_id"] == store_id


# ---------------------------------------------------------------------------
# Writing: the chat relay
# ---------------------------------------------------------------------------

async def keeping(csrf_client, session, session_id=None):
    weft_id, store_id = await ready(csrf_client, session)
    response = await set_history(
        csrf_client, weft_id, "session",
        session_provider_id=store_id, session_id=session_id,
    )
    assert response.status_code == 200
    return weft_id, store_id, response.json()["session_key"]


@pytest.mark.asyncio
async def test_the_chat_relay_appends_the_exchange(csrf_client, session, stub_chat, fake_store):
    weft_id, _, key = await keeping(csrf_client, session)

    response = await csrf_client.post(
        "/api/chat", json=say(weft_id, text="hi Iris")
    )

    assert deltas(response.text) == "Hello, m8"
    assert not [f for f in frames(response.text) if "error" in f]
    stored = fake_store["sessions"][key]
    assert stored["agent"] == "bridge/iris"
    assert stored["user"] == "alice"
    assert [(e["seq"], e["role"], e["text"]) for e in stored["events"]] == [
        (1, "user", "hi Iris"),
        (2, "assistant", "Hello, m8"),
    ]
    assert stored["events"][1]["model"] == "bridge/iris"


@pytest.mark.asyncio
async def test_a_weft_that_keeps_nothing_writes_nothing(csrf_client, session, stub_chat,
                                                        fake_store):
    weft_id, _ = await ready(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id))
    assert fake_store["calls"] == []


@pytest.mark.asyncio
async def test_a_failing_store_costs_the_saved_turn_not_the_reply(
    csrf_client, session, stub_chat, fake_store,
):
    weft_id, _, _ = await keeping(csrf_client, session)
    fake_store["fail"] = "store is down"

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert response.status_code == 200
    assert deltas(response.text) == "Hello, m8"
    errors = [f["error"] for f in frames(response.text) if "error" in f]
    assert len(errors) == 1 and "not saved to the session store" in errors[0]
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_an_empty_reply_is_not_kept(csrf_client, session, monkeypatch, fake_store):
    weft_id, _, _ = await keeping(csrf_client, session)

    async def silent(base_url, secret, payload, **_):
        async def gen():
            if False:
                yield ""
        return gen()

    monkeypatch.setattr(upstream, "stream_chat", silent)
    await csrf_client.post("/api/chat", json=say(weft_id))
    assert not [c for c in fake_store["calls"] if c[0] == "append"]


@pytest.mark.asyncio
async def test_a_typed_session_id_joins_without_claiming(
    csrf_client, session, stub_chat, fake_store,
):
    """An existing session is joined as it stands — not re-created, not re-owned."""
    fake_store["sessions"]["harness.iris"] = {"agent": "iris", "user": None, "events": []}
    weft_id, _, key = await keeping(csrf_client, session, session_id="harness.iris")
    assert key == "harness.iris"

    await csrf_client.post("/api/chat", json=say(weft_id))

    assert not [c for c in fake_store["calls"] if c[0] == "ensure"]
    joined = fake_store["sessions"]["harness.iris"]
    assert joined["agent"] == "iris" and joined["user"] is None
    assert len(joined["events"]) == 2


@pytest.mark.asyncio
async def test_deleting_the_store_leaves_the_weft_keeping_nothing(
    csrf_client, session, stub_chat, fake_store,
):
    weft_id, store_id, _ = await keeping(csrf_client, session)
    await csrf_client.delete(f"/api/providers/{store_id}")

    response = await csrf_client.post("/api/chat", json=say(weft_id))

    assert deltas(response.text) == "Hello, m8"
    assert fake_store["calls"] == []
    menu = (await csrf_client.get("/api/wefts")).json()
    assert menu[0]["session_provider_id"] is None


@pytest.mark.asyncio
async def test_the_store_token_never_appears_in_any_response(
    csrf_client, session, stub_chat, fake_store,
):
    weft_id, store_id, _ = await keeping(csrf_client, session)
    responses = [
        await csrf_client.get("/api/providers"),
        await csrf_client.get("/api/wefts"),
        await csrf_client.post("/api/chat", json=say(weft_id)),
        await csrf_client.get(
            "/api/session/thread", params={"weft_id": weft_id},
        ),
    ]
    for response in responses:
        assert STORE_SECRET not in response.text
        assert SECRET not in response.text


# ---------------------------------------------------------------------------
# Reading: the thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_thread_reads_back_messages_only(csrf_client, session, fake_store):
    weft_id, _, key = await keeping(csrf_client, session)
    fake_store["sessions"][key] = {"agent": "bridge/iris", "user": "alice", "events": [
        {"kind": "message", "role": "system", "text": "be nice"},
        {"kind": "message", "role": "user", "text": "weather?"},
        {"kind": "tool_call", "role": "assistant", "text": "weather()"},
        {"kind": "reasoning", "role": "assistant", "text": None},
        {"kind": "message", "role": "assistant", "text": "sunny"},
    ]}

    response = await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_key"] == key
    assert body["messages"] == [
        {"role": "user", "content": "weather?", "attachments": [], "spoken": False, "turn": None},
        {"role": "assistant", "content": "sunny", "attachments": [], "spoken": False, "turn": None},
    ]
    assert body["full"] is True
    # As many turns as the weft's window holds.
    assert ("tail", key, 200) in fake_store["calls"]


@pytest.mark.asyncio
async def test_a_session_not_yet_created_reads_as_empty(csrf_client, session, fake_store):
    weft_id, _, _ = await keeping(csrf_client, session)
    response = await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )
    assert response.status_code == 200
    assert response.json()["messages"] == []


@pytest.mark.asyncio
async def test_a_weft_without_a_store_has_no_thread_to_read(csrf_client, session, fake_store):
    weft_id, _ = await ready(csrf_client, session)
    response = await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_store_that_is_down_is_a_502(csrf_client, session, fake_store):
    weft_id, _, _ = await keeping(csrf_client, session)
    fake_store["fail"] = "store is down"
    response = await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )
    assert response.status_code == 502
    assert "store is down" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Catching up: only what came after the window's last read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_catch_up_reads_only_what_came_after(csrf_client, session, stub_chat, fake_store):
    """The store decrypts every event it returns, so a window that holds the thread
    asks for the new exchange, never the whole tail again."""
    weft_id, _, key = await keeping(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id))
    await csrf_client.post("/api/chat", json=say(weft_id))
    fake_store["calls"].clear()

    response = await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id, "after_seq": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full"] is False
    assert body["last_seq"] == 4
    assert [(m["role"], m["turn"]) for m in body["messages"]] == [("user", 2), ("assistant", 2)]
    assert [c[0] for c in fake_store["calls"]] == ["since"]


@pytest.mark.asyncio
async def test_a_catch_up_with_nothing_new_is_empty(csrf_client, session, stub_chat, fake_store):
    weft_id, _, _ = await keeping(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id))

    body = (await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id, "after_seq": 2},
    )).json()

    assert body == {**body, "full": False, "messages": [], "last_seq": 2}


@pytest.mark.asyncio
async def test_a_session_that_went_backwards_is_read_whole(csrf_client, session, stub_chat,
                                                           fake_store):
    """A window that read up to seq 9 of a session that now ends at 2 (cleared, or
    replaced by a join) cannot add to what it holds: it gets the tail to replace it."""
    weft_id, _, key = await keeping(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id))

    body = (await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id, "after_seq": 9},
    )).json()

    assert body["full"] is True
    assert len(body["messages"]) == 2
    assert ("tail", key, 200) in fake_store["calls"]


@pytest.mark.asyncio
async def test_too_much_to_catch_up_is_read_whole(csrf_client, session, monkeypatch,
                                                  stub_chat, fake_store):
    from app.routes import sessions as session_routes
    monkeypatch.setattr(session_routes, "CATCH_UP_EVENTS", 1)
    weft_id, _, key = await keeping(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(weft_id))

    body = (await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id, "after_seq": 0},
    )).json()

    assert body["full"] is True
    assert ("tail", key, 200) in fake_store["calls"]


# ---------------------------------------------------------------------------
# The ping: has another device moved the thread on?
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_head_reports_where_the_session_ends(csrf_client, session, stub_chat, fake_store):
    weft_id, _, key = await keeping(csrf_client, session)
    params = {"weft_id": weft_id}

    before = await csrf_client.get("/api/session/head", params=params)
    assert before.status_code == 200
    assert before.json() == {"session_key": key, "last_seq": 0}

    await csrf_client.post("/api/chat", json=say(weft_id))

    assert (await csrf_client.get("/api/session/head", params=params)).json()["last_seq"] == 2
    # /thread reports the same number, so the window can compare the two.
    assert (await csrf_client.get("/api/session/thread", params=params)).json()["last_seq"] == 2


@pytest.mark.asyncio
async def test_head_reads_no_events(csrf_client, session, fake_store):
    """The ping must stay cheap: a summary, never the tail."""
    weft_id, _, _ = await keeping(csrf_client, session)
    await csrf_client.get(
        "/api/session/head", params={"weft_id": weft_id},
    )
    assert [c[0] for c in fake_store["calls"]] == ["handshake"]


@pytest.mark.asyncio
async def test_head_on_a_weft_without_a_store_is_a_409(csrf_client, session, fake_store):
    weft_id, _ = await ready(csrf_client, session)
    response = await csrf_client.get(
        "/api/session/head", params={"weft_id": weft_id},
    )
    assert response.status_code == 409
