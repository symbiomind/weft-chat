"""
Tests for app/routes/wefts.py — wefts as first-class rows.

The ones that carry the design:

* ``test_unbinding_keeps_everything_the_weft_owns`` and
  ``test_a_new_engine_keeps_the_same_thread`` — a weft's whole
  configuration once died with a tick box. A weft's engine is now a binding, and nothing that
  changes the binding touches the rest of the row.
* ``test_names_are_unique_per_user_whatever_their_case`` — "warns if the user already has
  a Jarvis".
* ``test_another_users_weft_is_not_found_on_any_verb`` — ownership on every route, because
  it only takes one handler forgetting the ``user_id`` clause.

The history, colours and headers tests are the ones that used to live with the provider
routes when these settings hung off a pick; the rules did not change, only the address.
"""

import pytest
from sqlalchemy import select

from app.models import Weft
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_provider_routes import add_provider, signed_in


async def add_weft(client, name="Jarvis", provider_id=None, model_id=None):
    """Add a weft and return its body. Bound when both engine halves are given."""
    response = await client.post("/api/wefts", json={
        "name": name, "provider_id": provider_id, "model_id": model_id,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def bound_weft(client, name="Jarvis", model_id="alpha-model"):
    """A provider, and a weft bound to one of its models. Returns (weft, provider_id)."""
    provider_id = (await add_provider(client)).json()["id"]
    return await add_weft(client, name, provider_id, model_id), provider_id


async def set_history(client, weft_id, history, send_turns=1, window_turns=200,
                      session_provider_id=None, session_id=None):
    return await client.put(f"/api/wefts/{weft_id}/history", json={
        "history": history,
        "send_turns": send_turns,
        "window_turns": window_turns,
        "session_provider_id": session_provider_id,
        "session_id": session_id,
    })


async def set_colours(client, weft_id, colour_a, colour_b):
    return await client.put(f"/api/wefts/{weft_id}/colours",
                            json={"colour_a": colour_a, "colour_b": colour_b})


async def set_headers(client, weft_id, rows):
    return await client.put(f"/api/wefts/{weft_id}/headers", json={
        "headers": [{"name": n, "value": v} for n, v in rows],
    })


async def as_alice_after_bob_made_one(csrf_client, session):
    """Bob makes a weft, then the client is signed in as alice. Returns bob's weft id."""
    await signed_in(csrf_client, session, username="bob")
    weft_id = (await add_weft(csrf_client))["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return weft_id


# ---------------------------------------------------------------------------
# Adding, naming, deleting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_weft_is_added_by_name_alone_and_is_on_the_menu(csrf_client, session):
    await signed_in(csrf_client, session)

    weft = await add_weft(csrf_client, "Jarvis")

    assert weft["name"] == "Jarvis"
    assert weft["provider_id"] is None and weft["model_id"] is None
    assert [w["name"] for w in (await csrf_client.get("/api/wefts")).json()] == ["Jarvis"]


@pytest.mark.asyncio
async def test_a_new_weft_keeps_nothing_and_sends_twenty_turns(csrf_client, session):
    """The defaults a weft is born with. `memory`, because keeping a copy of somebody's
    conversation is a thing to be asked for; 20 turns, because `everything` was a bill
    charged again every turn."""
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)

    assert weft["history"] == "memory"
    assert (weft["send_turns"], weft["window_turns"]) == (20, 200)
    assert weft["has_voice"] is False
    assert weft["colour_a"] is None and weft["background"] is None


@pytest.mark.asyncio
async def test_names_are_unique_per_user_whatever_their_case(csrf_client, session):
    await signed_in(csrf_client, session)
    await add_weft(csrf_client, "Jarvis")

    for clash in ("Jarvis", "jarvis", "  JARVIS  "):
        response = await csrf_client.post("/api/wefts", json={"name": clash})
        assert response.status_code == 409, clash
        assert "“Jarvis”" in response.json()["detail"]


@pytest.mark.asyncio
async def test_two_users_may_each_have_a_jarvis(csrf_client, session):
    await as_alice_after_bob_made_one(csrf_client, session)
    assert (await csrf_client.post("/api/wefts", json={"name": "Jarvis"})).status_code == 201


@pytest.mark.asyncio
async def test_a_blank_name_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    assert (await csrf_client.post("/api/wefts", json={"name": "   "})).status_code == 422
    assert (await csrf_client.post("/api/wefts", json={"name": ""})).status_code == 422


@pytest.mark.asyncio
async def test_inner_whitespace_is_tidied(csrf_client, session):
    await signed_in(csrf_client, session)
    assert (await add_weft(csrf_client, "  Mister   Jarvis "))["name"] == "Mister Jarvis"


@pytest.mark.asyncio
async def test_a_weft_can_be_renamed_and_keeps_its_thread(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client, "Jarvis")

    response = await csrf_client.patch(f"/api/wefts/{weft['id']}", json={"name": "Friday"})

    assert response.status_code == 200
    assert response.json()["name"] == "Friday"
    assert response.json()["session_key"] == weft["session_key"]


@pytest.mark.asyncio
async def test_a_rename_onto_another_wefts_name_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    await add_weft(csrf_client, "Jarvis")
    friday = await add_weft(csrf_client, "Friday")

    response = await csrf_client.patch(f"/api/wefts/{friday['id']}", json={"name": "jarvis"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_changing_only_the_case_of_its_own_name_is_allowed(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client, "jarvis")
    response = await csrf_client.patch(f"/api/wefts/{weft['id']}", json={"name": "Jarvis"})
    assert response.status_code == 200
    assert response.json()["name"] == "Jarvis"


@pytest.mark.asyncio
async def test_the_menu_is_sorted_by_name(csrf_client, session):
    await signed_in(csrf_client, session)
    for name in ("vega", "Atlas", "iris"):
        await add_weft(csrf_client, name)
    assert [w["name"] for w in (await csrf_client.get("/api/wefts")).json()] == [
        "Atlas", "iris", "vega",
    ]


@pytest.mark.asyncio
async def test_a_weft_can_be_read_alone(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    response = await csrf_client.get(f"/api/wefts/{weft['id']}")
    assert response.status_code == 200
    assert response.json() == weft


@pytest.mark.asyncio
async def test_deleting_a_weft_removes_it(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)

    assert (await csrf_client.delete(f"/api/wefts/{weft['id']}")).status_code == 204
    assert (await csrf_client.get("/api/wefts")).json() == []
    assert (await csrf_client.get(f"/api/wefts/{weft['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_the_automatic_session_id_is_the_wefts_own(csrf_client, session):
    """weft.<user>.<weft id>: neither the provider nor the model is in it any more, so
    neither can move it."""
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert weft["session_key"] == f"weft.alice.{weft['id']}"
    assert weft["session_id"] is None


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_weft_can_be_added_already_bound(csrf_client, session):
    await signed_in(csrf_client, session)
    weft, provider_id = await bound_weft(csrf_client)

    assert weft["provider_id"] == provider_id
    assert weft["provider_name"] == "My connection"
    assert weft["model_id"] == "alpha-model"


@pytest.mark.asyncio
async def test_a_weft_can_be_bound_later_and_rebound(csrf_client, session):
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    other_id = (await add_provider(csrf_client, name="Other")).json()["id"]
    weft = await add_weft(csrf_client)

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={
        "provider_id": provider_id, "model_id": "alpha-model",
    })
    assert response.status_code == 200
    assert response.json()["model_id"] == "alpha-model"

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={
        "provider_id": other_id, "model_id": "zeta-model",
    })
    assert (response.json()["provider_name"], response.json()["model_id"]) == (
        "Other", "zeta-model",
    )


@pytest.mark.asyncio
async def test_an_engine_is_both_halves_or_neither(csrf_client, session):
    await signed_in(csrf_client, session)
    weft, provider_id = await bound_weft(csrf_client)

    half = await csrf_client.put(f"/api/wefts/{weft['id']}/engine",
                                 json={"provider_id": provider_id})
    assert half.status_code == 422
    half = await csrf_client.put(f"/api/wefts/{weft['id']}/engine",
                                 json={"model_id": "alpha-model"})
    assert half.status_code == 422

    neither = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={})
    assert neither.status_code == 200
    assert neither.json()["provider_id"] is None and neither.json()["model_id"] is None


@pytest.mark.asyncio
async def test_only_a_chat_provider_can_be_an_engine(csrf_client, session):
    await signed_in(csrf_client, session)
    speech_id = (await csrf_client.post("/api/providers", json={
        "name": "Speech", "base_url": "https://speech.example.test/v1", "kind": "tts",
    })).json()["id"]
    weft = await add_weft(csrf_client)

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={
        "provider_id": speech_id, "model_id": "kokoro",
    })
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_another_users_provider_cannot_be_an_engine(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    bobs = (await add_provider(csrf_client)).json()["id"]
    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    weft = await add_weft(csrf_client)

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={
        "provider_id": bobs, "model_id": "alpha-model",
    })
    assert response.status_code == 404
    response = await csrf_client.post("/api/wefts", json={
        "name": "Sneaky", "provider_id": bobs, "model_id": "alpha-model",
    })
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_two_wefts_may_share_an_engine(csrf_client, session):
    """Same model, different headers or voice: two wefts, not one."""
    await signed_in(csrf_client, session)
    weft, provider_id = await bound_weft(csrf_client, "One")
    await add_weft(csrf_client, "Two", provider_id, "alpha-model")

    assert len((await csrf_client.get("/api/wefts")).json()) == 2


@pytest.mark.asyncio
async def test_unbinding_keeps_everything_the_weft_owns(csrf_client, session):
    """Losing a weft's settings with its engine, made impossible rather than fixed."""
    await signed_in(csrf_client, session)
    weft, _ = await bound_weft(csrf_client)
    await set_colours(csrf_client, weft["id"], "#112233", "#445566")
    await set_history(csrf_client, weft["id"], "local", send_turns=5, window_turns=50)
    await set_headers(csrf_client, weft["id"], [("X-One", "1")])

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={})

    after = response.json()
    assert (after["colour_a"], after["colour_b"]) == ("#112233", "#445566")
    assert (after["history"], after["send_turns"], after["window_turns"]) == ("local", 5, 50)
    assert after["headers"] == [{"name": "X-One", "value": "1"}]


@pytest.mark.asyncio
async def test_a_new_engine_keeps_the_same_thread(csrf_client, session):
    """Same weft, new engine: the session id does not follow the provider."""
    await signed_in(csrf_client, session)
    weft, _ = await bound_weft(csrf_client)
    other_id = (await add_provider(csrf_client, name="Other")).json()["id"]

    response = await csrf_client.put(f"/api/wefts/{weft['id']}/engine", json={
        "provider_id": other_id, "model_id": "something-else",
    })
    assert response.json()["session_key"] == weft["session_key"]


# ---------------------------------------------------------------------------
# Ownership and the guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_another_users_weft_is_not_found_on_any_verb(csrf_client, session):
    weft_id = await as_alice_after_bob_made_one(csrf_client, session)

    assert (await csrf_client.get("/api/wefts")).json() == []
    for method, path, body in (
        ("get", f"/api/wefts/{weft_id}", None),
        ("patch", f"/api/wefts/{weft_id}", {"name": "Mine now"}),
        ("delete", f"/api/wefts/{weft_id}", None),
        ("put", f"/api/wefts/{weft_id}/engine", {}),
        ("put", f"/api/wefts/{weft_id}/colours", {"colour_a": None, "colour_b": None}),
        ("put", f"/api/wefts/{weft_id}/headers", {"headers": []}),
        ("put", f"/api/wefts/{weft_id}/voice", {}),
        ("delete", f"/api/wefts/{weft_id}/background", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        response = await csrf_client.request(method.upper(), path, **kwargs)
        assert response.status_code == 404, (method, path)
    assert (await set_history(csrf_client, weft_id, "memory")).status_code == 404


@pytest.mark.asyncio
async def test_wefts_require_authentication(csrf_client, session):
    assert (await csrf_client.get("/api/wefts")).status_code == 401
    assert (await csrf_client.post("/api/wefts", json={"name": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_weft_writes_require_csrf(client, session):
    user = await make_user(session)
    client.headers["X-Weft-CSRF"] = (await client.get("/api/auth/csrf")).json()["csrf"]
    response = await client.post(
        "/api/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    del client.headers["X-Weft-CSRF"]

    assert (await client.post("/api/wefts", json={"name": "Jarvis"})).status_code == 403


@pytest.mark.asyncio
async def test_the_menu_makes_no_upstream_call(csrf_client, session, monkeypatch):
    """Opening the chat costs one query, not a round trip to every gateway."""
    from app import upstream

    async def boom(*args, **kwargs):
        raise AssertionError("the menu called upstream")

    monkeypatch.setattr(upstream, "fetch_models", boom)
    await signed_in(csrf_client, session)
    await bound_weft(csrf_client)

    assert (await csrf_client.get("/api/wefts")).status_code == 200


# ---------------------------------------------------------------------------
# History, sending and the window (the history dials)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_history_is_per_weft(csrf_client, session):
    await signed_in(csrf_client, session)
    one, provider_id = await bound_weft(csrf_client, "One")
    two = await add_weft(csrf_client, "Two", provider_id, "alpha-model")

    await set_history(csrf_client, one["id"], "local")

    menu = {w["name"]: w for w in (await csrf_client.get("/api/wefts")).json()}
    assert menu["One"]["history"] == "local"
    assert menu["Two"]["history"] == "memory"
    assert two["id"] != one["id"]


@pytest.mark.asyncio
async def test_a_nonsense_history_value_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_history(csrf_client, weft["id"], "locl")).status_code == 422


@pytest.mark.asyncio
async def test_the_dials_are_stored_as_typed(csrf_client, session):
    """The beginner sentence and the Advanced number are ONE value."""
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)

    response = await set_history(csrf_client, weft["id"], "local", send_turns=7,
                                 window_turns=40)

    assert response.status_code == 200, response.text
    assert (response.json()["send_turns"], response.json()["window_turns"]) == (7, 40)


@pytest.mark.asyncio
async def test_zero_sending_turns_is_allowed_and_means_no_cap(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    response = await set_history(csrf_client, weft["id"], "memory", send_turns=0)
    assert response.status_code == 200
    assert response.json()["send_turns"] == 0


@pytest.mark.asyncio
async def test_a_window_of_zero_turns_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_history(csrf_client, weft["id"], "local",
                              window_turns=0)).status_code == 422


@pytest.mark.asyncio
async def test_a_wild_dial_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_history(csrf_client, weft["id"], "local",
                              window_turns=10_001)).status_code == 422
    assert (await set_history(csrf_client, weft["id"], "local",
                              send_turns=-1)).status_code == 422


@pytest.mark.asyncio
async def test_sending_more_than_the_window_holds_is_stored_not_clamped(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    response = await set_history(csrf_client, weft["id"], "local", send_turns=50,
                                 window_turns=10)
    assert (response.json()["send_turns"], response.json()["window_turns"]) == (50, 10)


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_colours_are_stored_lowercase(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)

    response = await set_colours(csrf_client, weft["id"], "#ABCDEF", "#123456")

    assert response.status_code == 200
    assert (response.json()["colour_a"], response.json()["colour_b"]) == ("#abcdef", "#123456")


@pytest.mark.asyncio
async def test_clearing_colours_returns_to_the_theme(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    await set_colours(csrf_client, weft["id"], "#abcdef", "#123456")

    response = await set_colours(csrf_client, weft["id"], None, None)
    assert (response.json()["colour_a"], response.json()["colour_b"]) == (None, None)


@pytest.mark.asyncio
async def test_half_a_pair_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_colours(csrf_client, weft["id"], "#abcdef", None)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["red", "#abc", "#abcdeg", "abcdef", "#abcdef0"])
async def test_anything_but_six_hex_digits_is_refused(csrf_client, session, bad):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_colours(csrf_client, weft["id"], bad, "#000000")).status_code == 422


@pytest.mark.asyncio
async def test_colours_survive_the_other_settings(csrf_client, session):
    """A config write with the wrong blast radius is the bug class that once wiped a weft's settings."""
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    await set_colours(csrf_client, weft["id"], "#abcdef", "#123456")

    await set_history(csrf_client, weft["id"], "local")
    await set_headers(csrf_client, weft["id"], [("X-One", "1")])
    await csrf_client.patch(f"/api/wefts/{weft['id']}", json={"name": "Renamed"})

    after = (await csrf_client.get(f"/api/wefts/{weft['id']}")).json()
    assert (after["colour_a"], after["colour_b"]) == ("#abcdef", "#123456")


# ---------------------------------------------------------------------------
# Extra request headers (2026-10-06)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_headers_are_per_weft_and_kept_in_order(csrf_client, session):
    await signed_in(csrf_client, session)
    one = await add_weft(csrf_client, "One")
    two = await add_weft(csrf_client, "Two")

    response = await set_headers(csrf_client, one["id"], [
        ("X-Session-Id", "demo"), ("X-Session-Key", "agent:main:api:demo"),
    ])
    assert response.status_code == 200
    assert response.json()["headers"] == [
        {"name": "X-Session-Id", "value": "demo"},
        {"name": "X-Session-Key", "value": "agent:main:api:demo"},
    ]
    assert (await csrf_client.get(f"/api/wefts/{two['id']}")).json()["headers"] == []


@pytest.mark.asyncio
async def test_an_empty_list_removes_the_headers(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    await set_headers(csrf_client, weft["id"], [("X-One", "1")])

    response = await set_headers(csrf_client, weft["id"], [])
    assert response.json()["headers"] == []
    row = (await session.execute(select(Weft))).scalars().one()
    await session.refresh(row)
    assert row.headers is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "Authorization", "authorization", "Proxy-Authorization", "Cookie", "Host",
    "Content-Length", "Content-Type", "Transfer-Encoding", "Connection", "Accept",
])
async def test_headers_weft_sets_itself_are_refused(csrf_client, session, name):
    """A header adds to what Weft sends and never replaces it: above all, never a second,
    unsealed place to keep a bearer token."""
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_headers(csrf_client, weft["id"], [(name, "x")])).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", [
    ("X-Bad Name", "v"),
    ("X-Colon:", "v"),
    ("X-Inject", "a\r\nX-Evil: 1"),
    ("X-Unicode", "café"),
    ("X-Long", "v" * 1025),
])
async def test_malformed_headers_are_refused(csrf_client, session, name, value):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    assert (await set_headers(csrf_client, weft["id"], [(name, value)])).status_code == 422


@pytest.mark.asyncio
async def test_a_header_twice_is_refused_whatever_its_case(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    response = await set_headers(csrf_client, weft["id"], [
        ("X-Session-Id", "a"), ("x-session-id", "b"),
    ])
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_blank_row_is_dropped_not_refused(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    response = await set_headers(csrf_client, weft["id"], [
        ("  ", "left over"), (" X-Trimmed ", "  value  "),
    ])
    assert response.status_code == 200
    assert response.json()["headers"] == [{"name": "X-Trimmed", "value": "value"}]
