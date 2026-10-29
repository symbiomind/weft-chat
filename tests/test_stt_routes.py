"""
Push to talk: the user's speech-to-text settings, the transcribe relay,
and what a spoken message looks like on the way out and on the way back.

The assertions that would matter:

* ``test_another_users_provider_cannot_be_chosen`` — the relay opens a sealed bearer;
  whose provider it opens is the whole game.
* ``test_the_weft_is_told_a_message_was_spoken`` and its opposite — the marker is
  what lets a weft forgive "irish" for "Iris", and it is the user's to switch off.
* ``test_the_store_remembers_spoken_and_gives_it_back`` — scrollback on another
  device shows the badge and clean words, never the marker.
* ``test_transcribe_posts_multipart_the_openai_way`` — the one upstream call, checked
  against a fake server rather than believed.
"""

import httpx
import pytest

from app import sessions, upstream
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_chat_routes import ready, stub_chat  # noqa: F401 (fixture)
from tests.test_provider_routes import signed_in
from tests.test_session_routes import fake_store, keeping  # noqa: F401 (fixture)
from tests.test_weft_routes import add_weft

STT_SECRET = "whisper-token-41f0"


async def add_stt(client, name="whisper", url="http://whisper.lan:9000/v1", secret=STT_SECRET):
    response = await client.post(
        "/api/providers",
        json={"name": name, "base_url": url, "secret": secret, "kind": "stt"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def choose(client, provider_id, **settings):
    return await client.put("/api/stt", json={"provider_id": provider_id, **settings})


@pytest.fixture
def fake_transcribe(monkeypatch):
    calls = []

    async def fake(base_url, secret, *, audio, content_type, model, language=None):
        calls.append({"base_url": base_url, "secret": secret, "audio": audio,
                      "content_type": content_type, "model": model, "language": language})
        return "good morning Iris"

    monkeypatch.setattr(upstream, "transcribe", fake)
    return calls


async def record(client, audio=b"\x1aE\xdf\xa3 fake webm", content_type="audio/webm",
                 weft_id=None):
    params = {"weft_id": weft_id} if weft_id else None
    return await client.post("/api/stt/transcribe", content=audio, params=params,
                             headers={"Content-Type": content_type})


async def set_ears(client, weft_id, mode, provider_id=None, model=None):
    return await client.put(f"/api/wefts/{weft_id}/stt",
                            json={"mode": mode, "provider_id": provider_id, "model": model})


# ---- settings ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nobody_has_push_to_talk_until_they_choose_a_provider(csrf_client, session):
    await signed_in(csrf_client, session)

    settings = (await csrf_client.get("/api/stt")).json()
    assert settings == {
        "provider_id": None, "provider_name": None, "model": None, "language": None,
        "send_on_release": False, "tell_weft": True, "available": False,
    }


@pytest.mark.asyncio
async def test_choosing_a_provider_turns_it_on(csrf_client, session):
    await signed_in(csrf_client, session)
    stt_id = await add_stt(csrf_client)

    response = await choose(csrf_client, stt_id, model="base", language="en",
                            send_on_release=True, tell_weft=False)
    assert response.status_code == 200
    assert response.json() == {
        "provider_id": stt_id, "provider_name": "whisper", "model": "base", "language": "en",
        "send_on_release": True, "tell_weft": False, "available": True,
    }
    assert (await csrf_client.get("/api/stt")).json()["available"] is True


@pytest.mark.asyncio
async def test_only_a_speech_to_text_provider_can_be_chosen(csrf_client, session):
    await ready(csrf_client, session)
    [chat_provider] = (await csrf_client.get("/api/providers")).json()
    response = await choose(csrf_client, chat_provider["id"])
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_language_must_look_like_one(csrf_client, session):
    await signed_in(csrf_client, session)
    stt_id = await add_stt(csrf_client)
    assert (await choose(csrf_client, stt_id, language="english please")).status_code == 422
    blank = await choose(csrf_client, stt_id, language="  ")
    assert blank.status_code == 200 and blank.json()["language"] is None


@pytest.mark.asyncio
async def test_another_users_provider_cannot_be_chosen(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    theirs = await add_stt(csrf_client)

    await make_user(session, username="carol")
    response = await login(csrf_client, username="carol", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    assert (await choose(csrf_client, theirs)).status_code == 404


@pytest.mark.asyncio
async def test_deleting_the_provider_turns_push_to_talk_off(csrf_client, session):
    await signed_in(csrf_client, session)
    stt_id = await add_stt(csrf_client)
    await choose(csrf_client, stt_id)

    assert (await csrf_client.delete(f"/api/providers/{stt_id}")).status_code in (200, 204)
    settings = (await csrf_client.get("/api/stt")).json()
    assert settings["available"] is False and settings["provider_id"] is None


# ---- transcribing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_recording_comes_back_as_words(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    stt_id = await add_stt(csrf_client)
    await choose(csrf_client, stt_id, language="en")

    response = await record(csrf_client, content_type="audio/webm;codecs=opus")
    assert response.status_code == 200
    assert response.json() == {"text": "good morning Iris"}

    call = fake_transcribe[0]
    assert call["secret"] == STT_SECRET
    assert call["content_type"] == "audio/webm"
    # No model chosen: OpenAI's name for whisper, which servers that ignore it accept.
    assert call["model"] == "whisper-1"
    assert call["language"] == "en"


@pytest.mark.asyncio
async def test_no_provider_means_no_transcribing(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    assert (await record(csrf_client)).status_code == 409
    assert fake_transcribe == []


@pytest.mark.asyncio
async def test_only_audio_is_transcribed(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client))
    assert (await record(csrf_client, content_type="image/png")).status_code == 415
    assert (await record(csrf_client, audio=b"")).status_code == 422


@pytest.mark.asyncio
async def test_a_failing_provider_is_a_502(csrf_client, session, monkeypatch):
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client))

    async def boom(*args, **kwargs):
        raise upstream.UpstreamError("whisper.lan answered 500: decoder exploded")

    monkeypatch.setattr(upstream, "transcribe", boom)
    response = await record(csrf_client)
    assert response.status_code == 502
    assert "decoder exploded" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_refused_recording_is_a_415(csrf_client, session, monkeypatch):
    """415 is the browser's cue to send the same recording again as wav."""
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client))

    async def refused(*args, **kwargs):
        raise upstream.RecordingRefused("ollama.lan answered 400: Failed to load audio file")

    monkeypatch.setattr(upstream, "transcribe", refused)
    response = await record(csrf_client)
    assert response.status_code == 415
    assert "Failed to load" in response.json()["detail"]


def transcription_server(monkeypatch, answer):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        upstream.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(lambda request: answer), **kw),
    )


@pytest.mark.asyncio
async def test_a_400_for_opus_is_a_refused_recording(monkeypatch):
    """Ollama reads wav and mp3 only, and says so with a 400 that names neither."""
    transcription_server(monkeypatch, httpx.Response(400, json={"error": {
        "message": "Failed to load image or audio file",
    }}))

    with pytest.raises(upstream.RecordingRefused):
        await upstream.transcribe(
            "http://ollama.lan/v1", "tok", audio=b"OPUSDATA",
            content_type="audio/webm", model="m",
        )


@pytest.mark.asyncio
async def test_a_400_for_wav_is_a_plain_failure(monkeypatch):
    """wav is the fallback. A server that refuses that too has nothing left to try."""
    transcription_server(monkeypatch, httpx.Response(400, text="no such model"))

    with pytest.raises(upstream.UpstreamError, match="no such model") as caught:
        await upstream.transcribe(
            "http://ollama.lan/v1", "tok", audio=b"RIFFDATA",
            content_type="audio/wav", model="m",
        )
    assert not isinstance(caught.value, upstream.RecordingRefused)


@pytest.mark.asyncio
async def test_transcribe_posts_multipart_the_openai_way(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  good morning Iris \n"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        upstream.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )

    text = await upstream.transcribe(
        "http://whisper.lan:9000/v1/", "tok", audio=b"OPUSDATA",
        content_type="audio/webm", model="base", language="en",
    )
    assert text == "good morning Iris"
    assert seen["url"] == "http://whisper.lan:9000/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert b'name="file"; filename="speech.webm"' in body
    assert b"OPUSDATA" in body
    assert b'name="model"\r\n\r\nbase' in body
    assert b'name="language"\r\n\r\nen' in body


# ---- a weft's own ears ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_weft_hears_with_the_account(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client), model="base")
    weft = await add_weft(csrf_client)
    assert weft["stt_mode"] == "account"

    assert (await record(csrf_client, weft_id=weft["id"])).status_code == 200
    assert fake_transcribe[0]["base_url"] == "http://whisper.lan:9000/v1"
    assert fake_transcribe[0]["model"] == "base"


@pytest.mark.asyncio
async def test_a_weft_with_ears_of_its_own_uses_them(csrf_client, session, fake_transcribe):
    """Trying a new server in one window, beside the usual one. Language stays the
    account's: it is about the user, not the weft."""
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client), language="en")
    ollama = await add_stt(csrf_client, name="ollama", url="http://ollama.lan/v1")
    weft = await add_weft(csrf_client)

    response = await set_ears(csrf_client, weft["id"], "own", ollama, "lfm3-omni:2b")
    assert response.status_code == 200, response.text
    assert response.json()["stt_mode"] == "own"
    assert response.json()["stt_provider_id"] == ollama

    assert (await record(csrf_client, weft_id=weft["id"])).status_code == 200
    assert (await record(csrf_client)).status_code == 200
    first, second = fake_transcribe
    assert (first["base_url"], first["model"]) == ("http://ollama.lan/v1", "lfm3-omni:2b")
    assert first["language"] == "en"
    assert second["base_url"] == "http://whisper.lan:9000/v1"


@pytest.mark.asyncio
async def test_own_ears_without_an_account_provider(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    ollama = await add_stt(csrf_client, name="ollama", url="http://ollama.lan/v1")
    weft = await add_weft(csrf_client)
    await set_ears(csrf_client, weft["id"], "own", ollama)

    assert (await record(csrf_client, weft_id=weft["id"])).status_code == 200
    assert fake_transcribe[0]["model"] == "whisper-1"
    assert (await record(csrf_client)).status_code == 409


@pytest.mark.asyncio
async def test_a_weft_can_be_deaf(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client))
    weft = await add_weft(csrf_client)
    await set_ears(csrf_client, weft["id"], "off")

    response = await record(csrf_client, weft_id=weft["id"])
    assert response.status_code == 409
    assert "off" in response.json()["detail"]
    assert fake_transcribe == []


@pytest.mark.asyncio
async def test_a_deleted_own_provider_hears_nothing(csrf_client, session, fake_transcribe):
    """Not the account's ears instead: that would compare the usual server with itself."""
    await signed_in(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client))
    ollama = await add_stt(csrf_client, name="ollama", url="http://ollama.lan/v1")
    weft = await add_weft(csrf_client)
    await set_ears(csrf_client, weft["id"], "own", ollama)

    assert (await csrf_client.delete(f"/api/providers/{ollama}")).status_code in (200, 204)

    listed = {w["id"]: w for w in (await csrf_client.get("/api/wefts")).json()}
    assert listed[weft["id"]]["stt_mode"] == "own"
    assert listed[weft["id"]]["stt_provider_id"] is None
    response = await record(csrf_client, weft_id=weft["id"])
    assert response.status_code == 409
    assert "gone" in response.json()["detail"]
    assert fake_transcribe == []


@pytest.mark.asyncio
async def test_switching_back_keeps_the_own_provider(csrf_client, session):
    await signed_in(csrf_client, session)
    ollama = await add_stt(csrf_client, name="ollama", url="http://ollama.lan/v1")
    weft = await add_weft(csrf_client)
    await set_ears(csrf_client, weft["id"], "own", ollama, "lfm3-omni:2b")

    back = (await set_ears(csrf_client, weft["id"], "account")).json()
    assert back["stt_mode"] == "account"
    assert (back["stt_provider_id"], back["stt_model"]) == (ollama, "lfm3-omni:2b")


@pytest.mark.asyncio
async def test_own_ears_must_be_a_speech_to_text_provider(csrf_client, session):
    await signed_in(csrf_client, session)
    weft = await add_weft(csrf_client)
    chat = (await csrf_client.post("/api/providers", json={
        "name": "bridge", "base_url": "http://bridge.lan/v1", "secret": "s", "kind": "llm",
    })).json()["id"]

    assert (await set_ears(csrf_client, weft["id"], "own")).status_code == 422
    assert (await set_ears(csrf_client, weft["id"], "own", chat)).status_code == 409
    assert (await set_ears(csrf_client, weft["id"], "sometimes")).status_code == 422


@pytest.mark.asyncio
async def test_another_users_provider_cannot_be_a_wefts_ears(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    theirs = await add_stt(csrf_client)

    await make_user(session, username="carol")
    response = await login(csrf_client, username="carol", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    weft = await add_weft(csrf_client)

    assert (await set_ears(csrf_client, weft["id"], "own", theirs)).status_code == 404


@pytest.mark.asyncio
async def test_another_users_weft_is_not_found(csrf_client, session, fake_transcribe):
    await signed_in(csrf_client, session)
    assert (await record(csrf_client, weft_id="not-mine")).status_code == 404
    assert fake_transcribe == []


# ---- spoken messages ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_weft_is_told_a_message_was_spoken(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)

    await csrf_client.post("/api/chat", json={
        "weft_id": weft_id,
        "messages": [
            {"role": "user", "content": "good morning irish", "spoken": True},
            {"role": "assistant", "content": "Morning! (Iris, I think you meant.)"},
            {"role": "user", "content": "typed this one"},
        ],
    })

    assert [m["content"] for m in stub_chat[0]["payload"]["messages"]] == [
        "[spoken] good morning irish",
        "Morning! (Iris, I think you meant.)",
        "typed this one",
    ]


@pytest.mark.asyncio
async def test_not_told_when_the_user_says_not_to(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    await choose(csrf_client, await add_stt(csrf_client), tell_weft=False)

    await csrf_client.post("/api/chat", json={
        "weft_id": weft_id,
        "messages": [{"role": "user", "content": "good morning", "spoken": True}],
    })
    assert stub_chat[0]["payload"]["messages"][0]["content"] == "good morning"


@pytest.mark.asyncio
async def test_the_store_remembers_spoken_and_gives_it_back(
    csrf_client, session, stub_chat, fake_store,
):
    weft_id, _, key = await keeping(csrf_client, session)

    await csrf_client.post("/api/chat", json={
        "weft_id": weft_id,
        "messages": [{"role": "user", "content": "good morning Iris", "spoken": True}],
    })

    event = fake_store["sessions"][key]["events"][0]
    assert event["text"] == "[spoken] good morning Iris"
    assert event["body"] == {"spoken": True}

    thread = (await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )).json()
    assert thread["messages"][0]["content"] == "good morning Iris"
    assert thread["messages"][0]["spoken"] is True
    assert thread["messages"][1]["spoken"] is False


def test_spoken_without_its_marker_reads_back_clean():
    """Stored while the user had telling the weft turned off: the flag, but no marker."""
    events = [
        {"kind": "message", "role": "user", "text": "hello", "body": {"spoken": True}},
        {"kind": "message", "role": "user", "text": "[spoken] hi", "body": None},
    ]
    assert sessions.messages_from_events(events) == [
        {"role": "user", "content": "hello", "spoken": True},
        # No flag in the body: whatever the text says is just text.
        {"role": "user", "content": "[spoken] hi"},
    ]
