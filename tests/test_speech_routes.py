"""
Tests for the voice: assigning one to a weft, and spending it at /api/speech.

The assertions that would matter:

* ``test_speech_opens_the_speech_providers_key_not_the_chat_providers`` — two different
  rows, two different keys, and only one of them should ever be opened here. If this
  route ever reached for the chat provider's bearer it would be sending somebody's LLM
  key to a speech server.
* ``test_a_voice_cannot_point_at_another_users_speech_provider`` — otherwise a weft
  could be aimed at a stranger's provider and spend their key.
* ``test_the_browser_does_not_choose_the_voice`` — the request names a weft, never a
  provider and a voice. That is what stops the endpoint being a general-purpose proxy
  to any speech provider the account owns.
* ``test_a_voice_needs_all_three_parts`` — a voice with no provider has nowhere to go.
"""

import pytest

from app import upstream
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_provider_routes import add_provider, signed_in
from tests.test_weft_routes import add_weft

SPEECH_SECRET = "sk-kokoro-9999"


async def add_speech_provider(client, name="kokoro", url="http://kokoro.lan:8880/v1"):
    return await client.post("/api/providers", json={
        "name": name, "base_url": url, "secret": SPEECH_SECRET, "kind": "tts",
    })


async def with_a_pick(csrf_client, session, model="iris"):
    """A signed-in user with one LLM provider and one weft bound to it. Returns the weft."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]
    return (await add_weft(csrf_client, "Iris", provider_id, model))["id"]


async def give_it_a_voice(csrf_client, weft_id, speech_id,
                          voice="af_bella", tts_model="kokoro"):
    return await csrf_client.put(f"/api/wefts/{weft_id}/voice", json={
        "tts_provider_id": speech_id,
        "tts_model": tts_model,
        "tts_voice": voice,
    })


@pytest.fixture
def stub_speak(monkeypatch):
    """Stub the outbound speech call, recording everything it was handed."""
    calls = []

    async def fake(base_url, secret, *, model, voice, text):
        calls.append({
            "base_url": base_url, "secret": secret,
            "model": model, "voice": voice, "text": text,
        })
        return "audio/mpeg", b"ID3fake-audio-bytes"

    monkeypatch.setattr(upstream, "speak", fake)
    return calls


@pytest.fixture
def stub_voices(monkeypatch):
    async def fake_voices(base_url, secret):
        return ["af_bella", "am_michael"]

    async def fake_models(base_url, secret):
        return [{"id": "kokoro", "owned_by": ""}]

    monkeypatch.setattr(upstream, "fetch_voices", fake_voices)
    monkeypatch.setattr(upstream, "fetch_models", fake_models)


# ---------------------------------------------------------------------------
# Speech providers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_speech_provider_is_just_a_provider_with_a_kind(csrf_client, session):
    """Same table, same vault, same ownership rules. The only difference is what it is
    asked for at the other end."""
    await signed_in(csrf_client, session)
    body = (await add_speech_provider(csrf_client)).json()

    assert body["kind"] == "tts"
    assert body["hint"] == "9999"


@pytest.mark.asyncio
async def test_kind_cannot_be_changed_by_an_edit(csrf_client, session):
    """Flipping an existing provider between llm and tts would leave its selections and
    any voice pointing at an endpoint that no longer answers what they need."""
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    response = await csrf_client.patch(
        f"/api/providers/{provider_id}", json={"kind": "tts", "name": "Renamed"}
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "llm"          # the kind is simply ignored
    assert response.json()["name"] == "Renamed"      # the rest of the patch still lands


@pytest.mark.asyncio
async def test_voices_are_listed_for_a_speech_provider(csrf_client, session, stub_voices):
    await signed_in(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]

    body = (await csrf_client.get(f"/api/providers/{speech_id}/voices")).json()

    assert body["voices"] == ["af_bella", "am_michael"]
    assert body["models"] == ["kokoro"]


@pytest.mark.asyncio
async def test_an_llm_provider_has_no_voices(csrf_client, session, stub_voices):
    await signed_in(csrf_client, session)
    provider_id = (await add_provider(csrf_client)).json()["id"]

    response = await csrf_client.get(f"/api/providers/{provider_id}/voices")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_an_endpoint_with_no_voices_list_is_still_usable(csrf_client, session,
                                                               monkeypatch):
    """OpenAI itself has no /audio/voices. Refusing to configure such a provider would
    make a working endpoint unconfigurable, so an empty list is a normal answer."""
    await signed_in(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]

    async def none_at_all(base_url, secret):
        return []

    async def no_models(base_url, secret):
        raise upstream.UpstreamError("nope")

    monkeypatch.setattr(upstream, "fetch_voices", none_at_all)
    monkeypatch.setattr(upstream, "fetch_models", no_models)

    response = await csrf_client.get(f"/api/providers/{speech_id}/voices")

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": speech_id, "provider_name": "kokoro", "models": [], "voices": [],
    }


# ---------------------------------------------------------------------------
# Giving a weft a voice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_weft_can_be_given_a_voice(csrf_client, session):
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]

    response = await give_it_a_voice(csrf_client, weft_id, speech_id)

    assert response.status_code == 200
    assert response.json()["has_voice"] is True
    assert [p["has_voice"] for p in (await csrf_client.get("/api/wefts")).json()] == [True]


@pytest.mark.asyncio
async def test_a_thread_is_silent_until_it_is_given_a_voice(csrf_client, session):
    """The default, and the right one. A window should not start making noise on its own."""
    await with_a_pick(csrf_client, session)

    assert [p["has_voice"] for p in (await csrf_client.get("/api/wefts")).json()] == [False]


@pytest.mark.asyncio
async def test_a_voice_can_be_cleared(csrf_client, session):
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id)

    response = await csrf_client.put(f"/api/wefts/{weft_id}/voice", json={})

    assert response.status_code == 200
    assert response.json()["has_voice"] is False


@pytest.mark.asyncio
async def test_a_voice_needs_all_three_parts(csrf_client, session):
    """A voice with no provider has nowhere to go; a provider with no voice has nothing
    to say. There is no half state to store."""
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]

    response = await csrf_client.put(f"/api/wefts/{weft_id}/voice", json={
        "tts_provider_id": speech_id, "tts_voice": "af_bella",   # no tts_model
    })

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_llm_provider_cannot_be_used_as_a_voice(csrf_client, session):
    weft_id = await with_a_pick(csrf_client, session)
    provider_id = (await csrf_client.get(f"/api/wefts/{weft_id}")).json()["provider_id"]

    response = await give_it_a_voice(csrf_client, weft_id, provider_id)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_voice_cannot_point_at_another_users_speech_provider(csrf_client,
                                                                     session):
    """Otherwise a weft could be aimed at a stranger's provider, and every reply would
    be spoken on their key."""
    await signed_in(csrf_client, session, username="bob")
    bobs_speech_id = (await add_speech_provider(csrf_client)).json()["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    weft_id = (await add_weft(csrf_client, "Iris"))["id"]

    response = await give_it_a_voice(csrf_client, weft_id, bobs_speech_id)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_speech_provider_silences_its_threads(csrf_client, session):
    """SET NULL, not CASCADE: removing a voice must not remove the weft."""
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id)

    await csrf_client.delete(f"/api/providers/{speech_id}")

    picks = (await csrf_client.get("/api/wefts")).json()
    assert len(picks) == 1                 # the thread survives
    assert picks[0]["has_voice"] is False  # it just stops speaking


# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_speech_returns_audio(csrf_client, session, stub_speak):
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id)

    response = await csrf_client.post("/api/speech", json={
        "weft_id": weft_id, "text": "Hello m8",
    })

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"ID3fake-audio-bytes"


@pytest.mark.asyncio
async def test_the_browser_does_not_choose_the_voice(csrf_client, session, stub_speak):
    """The request names a weft. The voice is resolved from what was configured, so this
    endpoint can only ever buy speech somebody deliberately set up."""
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id, voice="af_bella")

    await csrf_client.post("/api/speech", json={
        "weft_id": weft_id, "text": "Hello m8",
        # These are not fields on the request schema. If they ever became fields, this
        # endpoint would be a proxy to any speech provider the account owns.
        "tts_voice": "someone_elses_voice", "tts_provider_id": "anything",
    })

    assert stub_speak[0]["voice"] == "af_bella"
    assert stub_speak[0]["base_url"] == "http://kokoro.lan:8880/v1"


@pytest.mark.asyncio
async def test_speech_opens_the_speech_providers_key_not_the_chat_providers(
    csrf_client, session, stub_speak,
):
    """Two rows, two keys. Reaching for the wrong one would post an LLM bearer to a
    speech server — a key sent somewhere it was never meant to go."""
    from tests.test_provider_routes import SECRET as CHAT_SECRET

    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id)

    await csrf_client.post("/api/speech", json={
        "weft_id": weft_id, "text": "Hello",
    })

    assert stub_speak[0]["secret"] == SPEECH_SECRET
    assert stub_speak[0]["secret"] != CHAT_SECRET


@pytest.mark.asyncio
async def test_a_thread_with_no_voice_is_a_409(csrf_client, session, stub_speak):
    weft_id = await with_a_pick(csrf_client, session)

    response = await csrf_client.post("/api/speech", json={
        "weft_id": weft_id, "text": "Hello",
    })

    assert response.status_code == 409
    assert stub_speak == []


@pytest.mark.asyncio
async def test_an_upstream_speech_failure_is_a_502(csrf_client, session, monkeypatch):
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id)

    async def boom(base_url, secret, *, model, voice, text):
        raise upstream.UpstreamError("Could not reach http://kokoro.lan:8880/v1 (ConnectError).")

    monkeypatch.setattr(upstream, "speak", boom)
    response = await csrf_client.post("/api/speech", json={
        "weft_id": weft_id, "text": "Hello",
    })

    assert response.status_code == 502
    assert "ConnectError" in response.json()["detail"]


@pytest.mark.asyncio
async def test_another_users_thread_cannot_be_spoken(csrf_client, session, stub_speak):
    await signed_in(csrf_client, session, username="bob")
    bobs_provider = (await add_provider(csrf_client)).json()["id"]
    bobs_weft = (await add_weft(csrf_client, "Iris", bobs_provider, "iris"))["id"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    response = await csrf_client.post("/api/speech", json={
        "weft_id": bobs_weft, "text": "Hello",
    })

    assert response.status_code == 404
    assert stub_speak == []


@pytest.mark.asyncio
async def test_speech_requires_authentication(csrf_client, session):
    response = await csrf_client.post("/api/speech", json={
        "weft_id": "x", "text": "Hello",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_speech_requires_csrf(client, session, stub_speak):
    user = await make_user(session)
    client.headers["X-Weft-CSRF"] = (await client.get("/api/auth/csrf")).json()["csrf"]
    response = await client.post(
        "/api/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    del client.headers["X-Weft-CSRF"]

    response = await client.post("/api/speech", json={
        "weft_id": "x", "text": "Hello",
    })

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Kept clips
# ---------------------------------------------------------------------------

async def a_voiced_weft(csrf_client, session, voice="af_bella"):
    weft_id = await with_a_pick(csrf_client, session)
    speech_id = (await add_speech_provider(csrf_client)).json()["id"]
    await give_it_a_voice(csrf_client, weft_id, speech_id, voice=voice)
    return weft_id, speech_id


@pytest.mark.asyncio
async def test_the_same_words_are_not_bought_twice(csrf_client, session, stub_speak):
    """The replay that finds a reply's clips already made — on a reload, or on another
    device — must not pay the speech provider for them again."""
    weft_id, _ = await a_voiced_weft(csrf_client, session)

    first = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})
    again = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    assert len(stub_speak) == 1
    assert first.headers["x-weft-speech-cache"] == "miss"
    assert again.headers["x-weft-speech-cache"] == "hit"
    assert again.content == first.content
    assert again.headers["content-type"] == "audio/mpeg"
    # Kept on the server is not the same as kept in a browser.
    assert again.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_a_kept_clip_does_not_open_the_vault(csrf_client, session, stub_speak,
                                                   monkeypatch):
    from app import vault

    weft_id, _ = await a_voiced_weft(csrf_client, session)
    await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    def opened(*args, **kwargs):
        raise AssertionError("a cache hit should not need the speech provider's key")

    monkeypatch.setattr(vault, "open_secret", opened)
    response = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_new_voice_says_old_words_afresh(csrf_client, session, stub_speak):
    """The voice is part of the key: change it and an old reply is heard in the new one."""
    weft_id, speech_id = await a_voiced_weft(csrf_client, session, voice="af_bella")
    await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    await give_it_a_voice(csrf_client, weft_id, speech_id, voice="am_michael")
    response = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    assert response.headers["x-weft-speech-cache"] == "miss"
    assert [call["voice"] for call in stub_speak] == ["af_bella", "am_michael"]


@pytest.mark.asyncio
async def test_a_failed_clip_is_not_kept(csrf_client, session, monkeypatch):
    weft_id, _ = await a_voiced_weft(csrf_client, session)
    calls = []

    async def flaky(base_url, secret, *, model, voice, text):
        calls.append(text)
        if len(calls) == 1:
            raise upstream.UpstreamError("http://kokoro.lan:8880/v1 answered 502: down")
        return "audio/mpeg", b"ID3second-time-lucky"

    monkeypatch.setattr(upstream, "speak", flaky)
    failed = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})
    rebuilt = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    assert failed.status_code == 502
    assert rebuilt.status_code == 200
    assert rebuilt.content == b"ID3second-time-lucky"


@pytest.mark.asyncio
async def test_a_ttl_of_zero_keeps_nothing(csrf_client, session, stub_speak, monkeypatch):
    from app import speech_cache

    monkeypatch.setattr(speech_cache, "ttl_seconds", lambda: 0)
    weft_id, _ = await a_voiced_weft(csrf_client, session)

    await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})
    response = await csrf_client.post("/api/speech", json={"weft_id": weft_id, "text": "Hi"})

    assert response.headers["x-weft-speech-cache"] == "miss"
    assert len(stub_speak) == 2
    assert not speech_cache.directory().exists()
