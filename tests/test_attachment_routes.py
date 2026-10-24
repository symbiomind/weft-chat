"""
Pictures on a message: upload, fetch, and what the relay sends.

The assertions that would matter:

* ``test_another_users_picture_is_not_found`` — the directory is the ownership check, so
  knowing a hash must not be enough to read somebody else's picture, or to send it.
* ``test_only_the_newest_message_sends_its_pictures`` — a picture is looked at once;
  every later turn carries its marker, not its bytes.
* ``test_a_picture_with_no_words_still_has_text`` — ind-bridge stores only text, and
  does not store a turn that has none.
* ``test_the_store_keeps_references_and_gives_them_back`` — another device reading the
  session gets the same message back: its words, and its pictures by hash.
"""

import base64
import io

import pytest
from PIL import Image

from app import attachments, sessions
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_background_routes import picture
from tests.test_chat_routes import ready, stub_chat  # noqa: F401 (fixture)
from tests.test_session_routes import fake_store, keeping  # noqa: F401 (fixture)
from tests.test_provider_routes import signed_in


@pytest.fixture(autouse=True)
def attachment_dir(tmp_path, monkeypatch):
    path = tmp_path / "attachments"
    monkeypatch.setenv("WEFT_ATTACHMENTS_DIR", str(path))
    return path


async def upload(client, data, content_type="image/png"):
    return await client.put("/api/attachments", content=data,
                            headers={"Content-Type": content_type})


def chat(weft_id, messages):
    return {"weft_id": weft_id, "messages": messages}


# ---- upload and fetch -------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_then_fetch(csrf_client, session, attachment_dir):
    user = await signed_in(csrf_client, session)

    response = await upload(csrf_client, picture(size=(3000, 1500)))
    assert response.status_code == 200
    digest = response.json()["hash"]

    fetched = await csrf_client.get(f"/api/attachments/{digest}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert "private" in fetched.headers["cache-control"]
    img = Image.open(io.BytesIO(fetched.content))
    # Re-encoded and shrunk to the attachment size, not the background size.
    assert img.format == "WEBP"
    assert img.size == (2048, 1024)
    assert (attachment_dir / user.id / f"{digest}.webp").is_file()


@pytest.mark.asyncio
async def test_what_is_not_a_picture_is_refused(csrf_client, session):
    await signed_in(csrf_client, session)

    svg = await upload(csrf_client, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    assert svg.status_code == 415
    text = await upload(csrf_client, b"hello", "text/plain")
    assert text.status_code == 415
    junk = await upload(csrf_client, b"definitely not a png", "image/png")
    assert junk.status_code == 422


@pytest.mark.asyncio
async def test_signed_out_there_is_no_upload(client):
    response = await client.put("/api/attachments", content=picture(),
                                headers={"Content-Type": "image/png"})
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_another_users_picture_is_not_found(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    digest = (await upload(csrf_client, picture())).json()["hash"]

    await make_user(session, username="carol")
    response = await login(csrf_client, username="carol", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    theirs = await csrf_client.get(f"/api/attachments/{digest}")
    nobodys = await csrf_client.get(f"/api/attachments/{'0' * 64}")
    assert theirs.status_code == nobodys.status_code == 404
    assert theirs.json() == nobodys.json()


@pytest.mark.asyncio
async def test_another_users_picture_cannot_be_sent(csrf_client, session, stub_chat):
    await signed_in(csrf_client, session, username="bob")
    digest = (await upload(csrf_client, picture())).json()["hash"]

    # Alice, with a provider of her own, names Bob's hash.
    weft_id = await ready(csrf_client, session)
    response = await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "look", "attachments": [{"hash": digest, "name": "x.png"}]},
    ]))
    assert response.status_code == 422
    assert stub_chat == []


# ---- what the relay sends ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_newest_message_carries_its_pictures(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]

    response = await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "what's this?",
         "attachments": [{"hash": digest, "name": "cat.png"}]},
    ]))
    assert response.status_code == 200

    content = stub_chat[0]["payload"]["messages"][0]["content"]
    image, text = content
    assert image["type"] == "image_url"
    url = image["image_url"]["url"]
    assert url.startswith("data:image/webp;base64,")
    assert Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).format == "WEBP"
    assert text == {"type": "text", "text": "[image: cat.png] what's this?"}


@pytest.mark.asyncio
async def test_only_the_newest_message_sends_its_pictures(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "what's this?",
         "attachments": [{"hash": digest, "name": "cat.png"}]},
        {"role": "assistant", "content": "A cat."},
        {"role": "user", "content": "what colour?"},
    ]))

    assert stub_chat[0]["payload"]["messages"] == [
        {"role": "user", "content": "[image: cat.png] what's this?"},
        {"role": "assistant", "content": "A cat."},
        {"role": "user", "content": "what colour?"},
    ]


@pytest.mark.asyncio
async def test_a_picture_with_no_words_still_has_text(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "", "attachments": [{"hash": digest, "name": "[odd]\nname.png"}]},
    ]))

    text = stub_chat[0]["payload"]["messages"][0]["content"][-1]
    # Brackets and control characters cannot break the marker open.
    assert text == {"type": "text", "text": "[image: (odd)name.png]"}


@pytest.mark.asyncio
async def test_too_many_pictures_is_refused(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]
    many = [{"hash": digest, "name": "p.png"}] * (attachments.MAX_PER_MESSAGE + 1)

    response = await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "lots", "attachments": many},
    ]))
    assert response.status_code == 422


# ---- the session store -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_store_keeps_references_and_gives_them_back(
    csrf_client, session, stub_chat, fake_store,
):
    weft_id, _, key = await keeping(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "what's this?",
         "attachments": [{"hash": digest, "name": "cat.png"}]},
    ]))

    user_event = fake_store["sessions"][key]["events"][0]
    assert user_event["text"] == "[image: cat.png] what's this?"
    assert user_event["body"] == {"attachments": [{"hash": digest, "name": "cat.png"}]}
    # Never the picture itself.
    assert "base64" not in str(user_event)

    thread = (await csrf_client.get(
        "/api/session/thread", params={"weft_id": weft_id},
    )).json()
    assert thread["messages"][0] == {
        "role": "user", "content": "what's this?",
        "attachments": [{"hash": digest, "name": "cat.png"}], "spoken": False, "turn": 1,
    }
    assert thread["messages"][1]["attachments"] == []


def test_a_malformed_body_from_the_store_is_ignored():
    events = [
        {"kind": "message", "role": "user", "text": "[image: a] hi",
         "body": {"attachments": [{"hash": "../../etc/passwd", "name": "a"}, "junk"]}},
        {"kind": "message", "role": "user", "text": "plain", "body": "not a dict"},
    ]
    assert sessions.messages_from_events(events) == [
        {"role": "user", "content": "[image: a] hi"},
        {"role": "user", "content": "plain"},
    ]


@pytest.mark.asyncio
async def test_the_thread_is_read_with_its_bodies(monkeypatch):
    """The store's text view leaves bodies out, and with them every picture: the thread
    came back as markers on the next read. Found by using it, 2026-10-05."""
    from app import upstream

    seen = {}

    async def call(method, base_url, secret, *parts, params=None, body=None):
        seen.update(params or {})
        return 200, {"events": []}

    monkeypatch.setattr(upstream, "_session_call", call)
    await upstream.session_tail("http://mmce.test", "s", "weft.alice.x", last_turns=10)
    assert seen["view"] == "full"
