"""
The gallery: files that are not pictures, what each one is, and which wefts see them.

The assertions that would matter:

* ``test_a_file_is_kept_exactly_as_sent`` — a song or a PDF is the user's own bytes;
  unlike a picture, nothing about it is changed.
* ``test_markup_and_programs_are_refused`` — nothing the gallery holds may be a page a
  browser would render, or a program.
* ``test_a_file_goes_to_the_weft_as_its_name_only`` — a file never reaches a model as
  bytes; its agent takes it from the gallery.
* ``test_sending_a_file_shares_it_with_that_weft_only`` and
  ``test_an_agents_upload_is_shared_with_its_own_weft`` — what a weft's agent will be
  able to find is what was put in its own window, and nothing else.
"""

import pytest
from sqlalchemy import select

from app import gallery
from app.models import GalleryItem, GalleryShare
from tests.test_attachment_routes import chat, upload
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_background_routes import picture
from tests.test_chat_routes import ready, stub_chat  # noqa: F401 (fixture)
from tests.test_door import (  # noqa: F401 (fixtures)
    address_in, call, door_client, fresh_tickets, put, text_of, weft_with_key,
)
from tests.test_provider_routes import signed_in
from tests.test_session_routes import fake_store, keeping  # noqa: F401 (fixture)
from tests.test_weft_routes import add_weft

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 4


async def put_file(client, data, name, content_type="application/octet-stream"):
    return await client.put("/api/attachments", params={"name": name}, content=data,
                            headers={"Content-Type": content_type})


# ---- what is kept -------------------------------------------------------------


@pytest.mark.parametrize("head, name, mime", [
    (PDF, "a.pdf", "application/pdf"),
    (MP3, "a.mp3", "audio/mpeg"),
    (b"\xff\xfb\x90\x00" + bytes(64), "a.mp3", "audio/mpeg"),
    (b"\xff\xf1\x50\x80" + bytes(64), "a.aac", "audio/aac"),
    (b"OggS\x00\x02" + bytes(64), "a.ogg", "audio/ogg"),
    (b"fLaC" + bytes(64), "a.flac", "audio/flac"),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", "a.wav", "audio/wav"),
    (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00", "a.m4a", "audio/mp4"),
    (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00", "a.mp4", "video/mp4"),
    (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00", "a.mov", "video/quicktime"),
    (b"\x1a\x45\xdf\xa3" + bytes(64), "a.webm", "video/webm"),
    (b"# Notes\n\nSome words.\n", "notes.md", "text/markdown"),
    (b"a,b\n1,2\n", "t.csv", "text/csv"),
    ("plain words, and an emoji 🎶\n".encode(), "readme", "text/plain"),
])
def test_sniff_knows_what_a_file_is_from_its_bytes(head, name, mime):
    assert gallery.sniff(head, name) == mime


@pytest.mark.parametrize("head", [
    b"MZ\x90\x00\x03\x00",                     # a Windows program
    b"\x7fELF\x02\x01\x01",                     # a Linux one
    b"PK\x03\x04\x14\x00",                      # a zip
    b"<!DOCTYPE html><html>",
    b"  <html><body>",
    b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    b"<?xml version='1.0'?><svg/>",
    b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",  # a HEIC still the pipeline could not read
    b"",
])
def test_sniff_refuses_what_it_does_not_keep(head):
    assert gallery.sniff(head, "x.pdf") is None


@pytest.mark.asyncio
async def test_a_file_is_kept_exactly_as_sent(csrf_client, session, tmp_path):
    user = await signed_in(csrf_client, session)

    response = await put_file(csrf_client, PDF, "Floor coating data sheet.pdf")
    assert response.status_code == 200
    out = response.json()
    assert out["kind"] == "file"
    assert out["type"] == "application/pdf"
    assert out["name"] == "Floor coating data sheet.pdf"
    assert out["size"] == len(PDF)

    user_id = (await csrf_client.get("/api/auth/me")).json()["id"]
    assert (tmp_path / "data-attachments" / user_id / f"{out['hash']}.bin").read_bytes() == PDF

    fetched = await csrf_client.get(f"/api/attachments/{out['hash']}")
    assert fetched.status_code == 200
    assert fetched.content == PDF
    assert fetched.headers["content-type"] == "application/pdf"
    assert fetched.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''Floor%20coating%20data%20sheet.pdf"
    )
    assert user


@pytest.mark.asyncio
async def test_about_describes_a_file_without_fetching_it(csrf_client, session):
    await signed_in(csrf_client, session)
    digest = (await put_file(csrf_client, MP3, "Night Drive.mp3")).json()["hash"]

    about = await csrf_client.get(f"/api/attachments/{digest}/about")
    assert about.status_code == 200
    assert about.json() == {"hash": digest, "kind": "file", "name": "Night Drive.mp3",
                            "type": "audio/mpeg", "size": len(MP3)}


@pytest.mark.asyncio
async def test_markup_and_programs_are_refused(csrf_client, session):
    await signed_in(csrf_client, session)

    for data in (b"<!DOCTYPE html><script>alert(1)</script>", b"<svg/>", b"MZ\x90\x00"):
        response = await put_file(csrf_client, data, "x.txt")
        assert response.status_code == 422, data


@pytest.mark.asyncio
async def test_a_file_too_large_is_refused(csrf_client, session, monkeypatch):
    await signed_in(csrf_client, session)
    monkeypatch.setattr(gallery, "MAX_FILE_BYTES", 1024)

    response = await put_file(csrf_client, PDF + bytes(2048), "big.pdf")
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_another_users_file_is_not_found(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    digest = (await put_file(csrf_client, PDF, "bob.pdf")).json()["hash"]

    await make_user(session, username="carol")
    response = await login(csrf_client, username="carol", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    assert (await csrf_client.get(f"/api/attachments/{digest}")).status_code == 404
    assert (await csrf_client.get(f"/api/attachments/{digest}/about")).status_code == 404


@pytest.mark.asyncio
async def test_a_picture_from_before_the_gallery_gets_its_row_when_asked(
    csrf_client, session, tmp_path,
):
    await signed_in(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]
    # As it was before the gallery: the file, and no row.
    await session.execute(GalleryItem.__table__.delete())
    await session.commit()

    about = await csrf_client.get(f"/api/attachments/{digest}/about")
    assert about.status_code == 200
    assert about.json()["kind"] == "picture"
    assert about.json()["type"] == "image/webp"
    item = (await session.execute(select(GalleryItem))).scalar_one()
    assert item.source is None


# ---- what reaches the weft, and who is shown it -------------------------------


async def shares(session) -> set[tuple[str, str]]:
    rows = (await session.execute(select(GalleryShare.hash, GalleryShare.weft_id))).all()
    return {tuple(r) for r in rows}


@pytest.mark.asyncio
async def test_a_file_goes_to_the_weft_as_its_name_only(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]
    cat = (await upload(csrf_client, picture())).json()["hash"]

    response = await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "here you go",
         "attachments": [{"hash": song, "name": "song.mp3"}, {"hash": cat, "name": "cat.png"}]},
    ]))
    assert response.status_code == 200

    image, text = stub_chat[0]["payload"]["messages"][0]["content"]
    assert image["type"] == "image_url"
    assert text == {"type": "text", "text": "[file: song.mp3] [image: cat.png] here you go"}


@pytest.mark.asyncio
async def test_a_file_on_its_own_is_plain_text(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    # The client calls it a picture; the server knows better.
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "", "attachments": [
            {"hash": song, "name": "song.mp3", "kind": "picture"}]},
    ]))

    assert stub_chat[0]["payload"]["messages"] == [
        {"role": "user", "content": "[file: song.mp3]"},
    ]


@pytest.mark.asyncio
async def test_sending_a_file_shares_it_with_that_weft_only(csrf_client, session, stub_chat):
    weft_id = await ready(csrf_client, session)
    other = (await add_weft(csrf_client, "Other"))["id"]
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]
    # Uploaded, not yet sent: no weft has been shown it.
    assert await shares(session) == set()

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "listen", "attachments": [{"hash": song, "name": "song.mp3"}]},
    ]))

    assert await shares(session) == {(song, weft_id)}
    assert other


@pytest.mark.asyncio
async def test_the_store_keeps_a_file_as_a_file(csrf_client, session, stub_chat, fake_store):
    weft_id, _, key = await keeping(csrf_client, session)
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]

    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "listen", "attachments": [{"hash": song, "name": "song.mp3"}]},
    ]))

    event = fake_store["sessions"][key]["events"][0]
    assert event["text"] == "[file: song.mp3] listen"
    assert event["body"] == {"attachments": [{"hash": song, "name": "song.mp3", "kind": "file"}]}

    thread = (await csrf_client.get("/api/session/thread", params={"weft_id": weft_id})).json()
    assert thread["messages"][0]["content"] == "listen"
    assert thread["messages"][0]["attachments"] == [
        {"hash": song, "name": "song.mp3", "kind": "file"},
    ]


@pytest.mark.asyncio
async def test_an_agents_upload_is_shared_with_its_own_weft(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    await add_weft(door_client, "Other")

    offered = await call(door_client, key, "upload", name="Night Drive.mp3")
    stored = await put(door_client, address_in(offered), MP3)

    assert stored.status_code == 200
    out = stored.json()
    assert out["kind"] == "file"
    assert out["type"] == "audio/mpeg"
    # A file is a link, not an image: the window draws it as a card.
    assert out["reference"] == (
        f"[Night Drive.mp3](https://weft.test/api/attachments/{out['hash']})"
    )
    item = await session.get(GalleryItem, ((await door_client.get("/api/auth/me")).json()["id"],
                                           out["hash"]))
    assert (item.source, item.weft_id) == ("weft", weft["id"])
    assert await shares(session) == {(out["hash"], weft["id"])}

    # The user has it too.
    fetched = await door_client.get(f"/api/attachments/{out['hash']}")
    assert fetched.content == MP3


@pytest.mark.asyncio
async def test_an_agent_cannot_view_a_file_as_a_picture(door_client, session):
    _, key = await weft_with_key(door_client, session)
    stored = await put(door_client, address_in(await call(door_client, key, "upload")), PDF)
    assert stored.json()["reference"].startswith("[file](")

    result = await call(door_client, key, "view", reference=stored.json()["hash"])
    assert result["isError"] is True
    assert "not a picture" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_deleting_a_weft_keeps_its_files_and_forgets_its_shares(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    stored = await put(door_client, address_in(await call(door_client, key, "upload")), PDF)
    digest = stored.json()["hash"]

    assert (await door_client.delete(f"/api/wefts/{weft['id']}")).status_code == 204

    session.expire_all()
    item = (await session.execute(select(GalleryItem))).scalar_one()
    assert (item.hash, item.weft_id) == (digest, None)
    assert await shares(session) == set()
    assert (await door_client.get(f"/api/attachments/{digest}")).status_code == 200


# ---- the gallery page ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_gallery_lists_everything_newest_first_with_who_was_shown_it(
    csrf_client, session, stub_chat,
):
    weft_id = await ready(csrf_client, session)
    pdf = (await put_file(csrf_client, PDF, "sheet.pdf")).json()["hash"]
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]
    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "listen", "attachments": [{"hash": song, "name": "song.mp3"}]},
    ]))

    items = (await csrf_client.get("/api/gallery")).json()["items"]
    assert [i["hash"] for i in items] == [song, pdf]
    assert items[0]["shown_to"] == [weft_id]
    assert items[1]["shown_to"] == []
    assert (items[1]["kind"], items[1]["type"], items[1]["source"]) == ("file", "application/pdf", "user")


@pytest.mark.asyncio
async def test_the_gallery_shows_pictures_from_before_it(csrf_client, session):
    await signed_in(csrf_client, session)
    digest = (await upload(csrf_client, picture())).json()["hash"]
    await session.execute(GalleryItem.__table__.delete())
    await session.commit()

    items = (await csrf_client.get("/api/gallery")).json()["items"]
    assert [(i["hash"], i["kind"], i["source"]) for i in items] == [(digest, "picture", None)]


@pytest.mark.asyncio
async def test_deleting_takes_the_file_its_row_and_its_shares(
    csrf_client, session, stub_chat, tmp_path,
):
    weft_id = await ready(csrf_client, session)
    song = (await put_file(csrf_client, MP3, "song.mp3")).json()["hash"]
    await csrf_client.post("/api/chat", json=chat(weft_id, [
        {"role": "user", "content": "x", "attachments": [{"hash": song, "name": "song.mp3"}]},
    ]))

    assert (await csrf_client.delete(f"/api/gallery/{song}")).status_code == 204

    assert (await csrf_client.get("/api/gallery")).json()["items"] == []
    assert await shares(session) == set()
    assert (await csrf_client.get(f"/api/attachments/{song}")).status_code == 404
    assert (await csrf_client.get(f"/api/attachments/{song}/about")).status_code == 404
    assert (await csrf_client.delete(f"/api/gallery/{song}")).status_code == 404


@pytest.mark.asyncio
async def test_one_user_cannot_delete_anothers_file(csrf_client, session):
    await signed_in(csrf_client, session, username="bob")
    digest = (await put_file(csrf_client, PDF, "bob.pdf")).json()["hash"]
    bob_auth = csrf_client.headers["Authorization"]

    await make_user(session, username="carol")
    response = await login(csrf_client, username="carol", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    assert (await csrf_client.delete(f"/api/gallery/{digest}")).status_code == 404
    assert (await csrf_client.get("/api/gallery")).json()["items"] == []

    csrf_client.headers["Authorization"] = bob_auth
    assert (await csrf_client.get(f"/api/attachments/{digest}")).status_code == 200


# ---- the agent's list and grab ---------------------------------------------------


def downloads_in(result) -> str:
    import re
    return re.search(r"https://weft\.test/api/door/download/[A-Za-z0-9_-]+", text_of(result))[0]


async def fetch(client, address):
    return await client.get(address, headers={"Origin": "", "X-Weft-CSRF": "", "Authorization": ""})


@pytest.mark.asyncio
async def test_list_and_grab_reach_what_the_agent_uploaded(door_client, session):
    _, key = await weft_with_key(door_client, session)
    stored = await put(door_client, address_in(await call(door_client, key, "upload",
                                                          name="Night Drive.mp3")), MP3)
    digest = stored.json()["hash"]

    listed = text_of(await call(door_client, key, "list"))
    assert "Night Drive.mp3 | audio/mpeg" in listed
    assert "from you" in listed
    assert digest in listed

    for handle in (digest, "night drive.mp3", stored.json()["reference"]):
        offered = await call(door_client, key, "grab", file=handle)
        address = downloads_in(offered)
        got = await fetch(door_client, address)
        assert got.status_code == 200
        assert got.content == MP3
        assert got.headers["content-type"] == "audio/mpeg"
        assert "Night%20Drive.mp3" in got.headers["content-disposition"]
        # Once only.
        assert (await fetch(door_client, address)).status_code == 404


@pytest.mark.asyncio
async def test_a_weft_cannot_grab_what_it_was_not_given(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    # The user's own upload, sent nowhere yet.
    mine = (await put_file(door_client, PDF, "private.pdf")).json()["hash"]
    # Another weft's agent's upload.
    other = await add_weft(door_client, "Other")
    other_key = (await door_client.post(f"/api/wefts/{other['id']}/door")).json()["key"]
    theirs = (await put(door_client, address_in(await call(door_client, other_key, "upload",
                                                           name="theirs.mp3")), MP3)).json()["hash"]

    assert "Nothing yet" in text_of(await call(door_client, key, "list"))
    for handle in (mine, theirs, "private.pdf", "theirs.mp3"):
        result = await call(door_client, key, "grab", file=handle)
        assert result["isError"] is True, handle
        assert "No such file" in text_of(result)

    # Sent in its chat, it is the weft's to have.
    await gallery.share(session, (await door_client.get("/api/auth/me")).json()["id"],
                        [mine], weft["id"])
    listed = text_of(await call(door_client, key, "list"))
    assert "private.pdf" in listed and "from your user" in listed
    assert "theirs.mp3" not in listed
    got = await fetch(door_client, downloads_in(await call(door_client, key, "grab", file=mine)))
    assert got.content == PDF


@pytest.mark.asyncio
async def test_view_is_limited_to_the_wefts_pictures_but_keeps_old_ones(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    other = await add_weft(door_client, "Other")
    other_key = (await door_client.post(f"/api/wefts/{other['id']}/door")).json()["key"]
    theirs = (await put(door_client, address_in(await call(door_client, other_key, "upload")),
                        picture())).json()["hash"]

    refused = await call(door_client, key, "view", reference=theirs)
    assert refused["isError"] is True

    # A picture from before the gallery: every weft could look at it then, and still can.
    old = (await upload(door_client, picture(colour=(10, 200, 30)))).json()["hash"]
    await session.execute(GalleryItem.__table__.delete().where(GalleryItem.hash == old))
    await session.commit()
    seen = await call(door_client, key, "view", reference=old)
    assert any(part["type"] == "image" for part in seen["content"])
    assert weft


@pytest.mark.asyncio
async def test_grab_names_the_hashes_when_a_name_is_shared(door_client, session):
    _, key = await weft_with_key(door_client, session)
    for data in (MP3, MP3 + b"x"):
        await put(door_client, address_in(await call(door_client, key, "upload",
                                                     name="take.mp3")), data)

    result = await call(door_client, key, "grab", file="take.mp3")
    assert result["isError"] is True
    assert "2 files are called" in text_of(result)


@pytest.mark.asyncio
async def test_list_searches_by_name(door_client, session):
    _, key = await weft_with_key(door_client, session)
    for name, data in (("cover.pdf", PDF), ("song.mp3", MP3)):
        await put(door_client, address_in(await call(door_client, key, "upload", name=name)), data)

    listed = text_of(await call(door_client, key, "list", search="SONG"))
    assert "song.mp3" in listed and "cover.pdf" not in listed
