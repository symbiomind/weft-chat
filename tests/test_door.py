"""
The MCP door: keys, the MCP tools, and the one-time upload address.

The assertions that would matter:

* ``test_a_caller_without_the_key_cannot_even_list_the_tools`` — the gate is in front
  of the whole protocol, not inside the tools.
* ``test_minting_again_retires_the_old_key`` — a leaked key is fixed by one click.
* ``test_an_agent_shows_a_picture_and_looks_at_it_again`` — the whole round trip, ending
  with the picture in the right user's folder and the agent seeing it as an image.
* ``test_an_upload_address_works_once`` — the address is the credential.
* ``test_an_invite_delivers_a_working_key_once`` — the key reaches the agent without
  passing through the chat, and what the chat keeps is a dead link.
"""

import asyncio
import base64
import dataclasses
import io
import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db as db_module
from app import door
from app.models import WeftKey
from tests.test_background_routes import picture
from tests.test_provider_routes import signed_in
from tests.test_weft_routes import add_weft

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def fresh_tickets():
    for table in (door._tickets, door._asked, door._invites):
        table.clear()
    yield
    for table in (door._tickets, door._asked, door._invites):
        table.clear()


@pytest_asyncio.fixture
async def door_client(app_config, session, monkeypatch):
    """The routes a door needs, and the door itself with its session manager running.

    The key gate opens its own database session rather than taking a dependency, so it
    is pointed at the test database here.
    """
    from fastapi import FastAPI

    from app.routes import attachments as attachment_routes
    from app.routes import auth as auth_routes
    from app.routes import door as door_routes
    from app.routes import notifications as notification_routes
    from app.routes import push as push_routes
    from app.routes import wefts as weft_routes

    app = FastAPI()
    for router in (attachment_routes.router, auth_routes.router, weft_routes.router,
                   door_routes.router, door_routes.upload_router,
                   notification_routes.router, push_routes.router):
        app.include_router(router)
    server = door.build_server()
    door.mount(app, server)

    async def _session_override():
        yield session

    app.dependency_overrides[db_module.get_session] = _session_override
    monkeypatch.setattr(
        db_module, "_sessionmaker", async_sessionmaker(session.bind, expire_on_commit=False)
    )

    # Held open in a task of its own: pytest-asyncio may tear a fixture down in a
    # different task from the one that set it up, and the manager's task group must be
    # left by the task that entered it.
    running, stop = asyncio.Event(), asyncio.Event()

    async def hold():
        async with server.session_manager.run():
            running.set()
            await stop.wait()

    holder = asyncio.create_task(hold())
    await running.wait()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://weft.test",
            headers={"Origin": "https://weft.test"},
        ) as ac:
            csrf = (await ac.get("/api/auth/csrf")).json()["csrf"]
            ac.headers["X-Weft-CSRF"] = csrf
            yield ac
    finally:
        stop.set()
        await holder


async def weft_with_key(client, session, name="Jarvis"):
    await signed_in(client, session)
    weft = await add_weft(client, name)
    minted = await client.post(f"/api/wefts/{weft['id']}/door")
    assert minted.status_code == 201
    return weft, minted.json()["key"]


async def rpc(client, key, method, params=None):
    """One JSON-RPC call to the door, as an agent's MCP client would make it."""
    headers = dict(MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return await client.post(
        door.PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=headers,
    )


async def call(client, key, tool, **arguments):
    response = await rpc(client, key, "tools/call", {"name": tool, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def text_of(result) -> str:
    return " ".join(part["text"] for part in result["content"] if part["type"] == "text")


def address_in(result) -> str:
    return re.search(r"https://weft\.test/api/door/upload/[A-Za-z0-9_-]+", text_of(result))[0]


async def put(client, address, data):
    # The agent's curl carries no cookie, no CSRF token and no bearer.
    return await client.put(address, content=data,
                            headers={"Origin": "", "X-Weft-CSRF": "", "Authorization": ""})


# ---- keys -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_door_is_shut_until_a_key_is_minted(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")

    shut = (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()
    assert shut["open"] is False
    assert shut["mcp_url"] == "https://weft.test/api/mcp"

    minted = (await door_client.post(f"/api/wefts/{weft['id']}/door")).json()
    assert minted["key"].startswith("weft_")
    assert minted["hint"] == minted["key"][-4:]

    shown = (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()
    assert shown["open"] is True
    assert "key" not in shown
    stored = (await session.execute(select(WeftKey))).scalars().one()
    assert minted["key"] not in stored.token_hash


@pytest.mark.asyncio
async def test_the_pick_menu_says_which_wefts_have_a_door(door_client, session):
    # What the Pick's plug reads: the door is set up, whether or not an agent has used it.
    weft, _ = await weft_with_key(door_client, session, "Jarvis")
    other = await add_weft(door_client, "Friday")

    listed = {w["name"]: w["has_door"] for w in (await door_client.get("/api/wefts")).json()}
    assert listed == {"Friday": False, "Jarvis": True}
    assert (await door_client.get(f"/api/wefts/{other['id']}")).json()["has_door"] is False

    await door_client.delete(f"/api/wefts/{weft['id']}/door")
    assert (await door_client.get(f"/api/wefts/{weft['id']}")).json()["has_door"] is False


@pytest.mark.asyncio
async def test_minting_again_retires_the_old_key(door_client, session):
    weft, old = await weft_with_key(door_client, session)

    new = (await door_client.post(f"/api/wefts/{weft['id']}/door")).json()["key"]

    assert (await rpc(door_client, old, "tools/list")).status_code == 401
    assert (await rpc(door_client, new, "tools/list")).status_code == 200


@pytest.mark.asyncio
async def test_closing_the_door_stops_the_key(door_client, session):
    weft, key = await weft_with_key(door_client, session)

    closed = await door_client.delete(f"/api/wefts/{weft['id']}/door")
    assert closed.status_code == 204

    assert (await rpc(door_client, key, "tools/list")).status_code == 401
    assert (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()["open"] is False


@pytest.mark.asyncio
async def test_every_door_can_be_closed_at_once(door_client, session):
    weft, first = await weft_with_key(door_client, session, "Jarvis")
    other = await add_weft(door_client, "Friday")
    second = (await door_client.post(f"/api/wefts/{other['id']}/door")).json()["key"]

    assert (await door_client.delete("/api/door/keys")).status_code == 204

    for key in (first, second):
        assert (await rpc(door_client, key, "tools/list")).status_code == 401


@pytest.mark.asyncio
async def test_deleting_the_weft_takes_its_key(door_client, session):
    weft, key = await weft_with_key(door_client, session)

    await door_client.delete(f"/api/wefts/{weft['id']}")

    assert (await session.execute(select(WeftKey))).scalars().all() == []
    assert (await rpc(door_client, key, "tools/list")).status_code == 401


@pytest.mark.asyncio
async def test_another_users_weft_has_no_door_to_open(door_client, session):
    await signed_in(door_client, session, username="bob")
    bobs = await add_weft(door_client, "Jarvis")
    await signed_in(door_client, session, username="alice")

    assert (await door_client.post(f"/api/wefts/{bobs['id']}/door")).status_code == 404
    assert (await door_client.get(f"/api/wefts/{bobs['id']}/door")).status_code == 404


@pytest.mark.asyncio
async def test_a_key_needs_the_browsers_csrf_to_be_minted(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")

    response = await door_client.post(f"/api/wefts/{weft['id']}/door",
                                      headers={"X-Weft-CSRF": "forged"})
    assert response.status_code == 403


# ---- the gate and the tools -------------------------------------------------


@pytest.mark.asyncio
async def test_a_caller_without_the_key_cannot_even_list_the_tools(door_client, session):
    await weft_with_key(door_client, session)

    for key in (None, "weft_not-a-real-key", "Bearer-shaped nonsense"):
        response = await rpc(door_client, key, "tools/list")
        assert response.status_code == 401
        assert response.content == b""


@pytest.mark.asyncio
async def test_the_key_lists_four_tools_and_is_noted_as_used(door_client, session):
    weft, key = await weft_with_key(door_client, session)

    response = await rpc(door_client, key, "tools/list")
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {"upload", "view", "notify", "status"}

    shown = (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()
    assert shown["last_used_at"] is not None


@pytest.mark.asyncio
async def test_status_names_the_weft(door_client, session):
    _, key = await weft_with_key(door_client, session, "Jarvis")

    result = await call(door_client, key, "status")
    assert '"Jarvis"' in text_of(result)
    assert key[-4:] in text_of(result)


@pytest.mark.asyncio
async def test_an_agent_shows_a_picture_and_looks_at_it_again(door_client, session, tmp_path):
    _, key = await weft_with_key(door_client, session)
    user_id = (await door_client.get("/api/auth/me")).json()["id"]

    offered = await call(door_client, key, "upload", name="dashboard")
    stored = await put(door_client, address_in(offered), picture(size=(3000, 1500)))

    assert stored.status_code == 200
    digest = stored.json()["hash"]
    assert stored.json()["reference"] == (
        f"![dashboard](https://weft.test/api/attachments/{digest})"
    )
    assert (tmp_path / "data-attachments" / user_id / f"{digest}.webp").is_file()

    seen = await call(door_client, key, "view", reference=stored.json()["reference"])
    image = next(part for part in seen["content"] if part["type"] == "image")
    assert image["mimeType"] == "image/webp"
    assert Image.open(io.BytesIO(base64.b64decode(image["data"]))).size == (2048, 1024)


@pytest.mark.asyncio
async def test_the_user_sees_what_the_agent_uploaded(door_client, session):
    _, key = await weft_with_key(door_client, session)

    stored = await put(door_client, address_in(await call(door_client, key, "upload")),
                       picture())

    fetched = await door_client.get(f"/api/attachments/{stored.json()['hash']}")
    assert fetched.status_code == 200


@pytest.mark.asyncio
async def test_view_cannot_reach_another_users_picture(door_client, session):
    _, alices_key = await weft_with_key(door_client, session, "Jarvis")
    stored = await put(door_client, address_in(await call(door_client, alices_key, "upload")),
                       picture())

    await signed_in(door_client, session, username="bob")
    bobs = await add_weft(door_client, "Friday")
    bobs_key = (await door_client.post(f"/api/wefts/{bobs['id']}/door")).json()["key"]

    seen = await call(door_client, bobs_key, "view", reference=stored.json()["hash"])
    assert seen["isError"] is True


# ---- the upload address -----------------------------------------------------


@pytest.mark.asyncio
async def test_an_upload_address_works_once(door_client, session):
    _, key = await weft_with_key(door_client, session)
    address = address_in(await call(door_client, key, "upload"))

    assert (await put(door_client, address, picture())).status_code == 200
    assert (await put(door_client, address, picture())).status_code == 404


@pytest.mark.asyncio
async def test_what_is_not_a_picture_is_refused_and_uses_the_address(door_client, session):
    _, key = await weft_with_key(door_client, session)
    address = address_in(await call(door_client, key, "upload"))

    assert (await put(door_client, address, b"<svg/>")).status_code == 422
    assert (await put(door_client, address, picture())).status_code == 404


@pytest.mark.asyncio
async def test_closing_the_door_closes_its_open_addresses(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    address = address_in(await call(door_client, key, "upload"))

    await door_client.delete(f"/api/wefts/{weft['id']}/door")

    assert (await put(door_client, address, picture())).status_code == 404


@pytest.mark.asyncio
async def test_a_weft_can_only_hold_so_many_open_addresses(door_client, session):
    _, key = await weft_with_key(door_client, session)

    for _ in range(door.MAX_OPEN_TICKETS):
        assert not (await call(door_client, key, "upload")).get("isError")
    refused = await call(door_client, key, "upload")

    assert refused["isError"] is True
    assert "waiting" in text_of(refused)


@pytest.mark.asyncio
async def test_a_lapsed_address_is_refused(door_client, session):
    _, key = await weft_with_key(door_client, session)
    address = address_in(await call(door_client, key, "upload"))

    # Aged in place rather than by patching the clock: asyncio reads the same clock.
    for digest, ticket in door._tickets.items():
        door._tickets[digest] = dataclasses.replace(ticket, expires=0)

    assert (await put(door_client, address, picture())).status_code == 404


# ---- invites and the guide --------------------------------------------------


async def invite_for(client, weft_id):
    response = await client.post(f"/api/wefts/{weft_id}/door/invite")
    assert response.status_code == 200
    return response.json()


async def read(client, url):
    # The agent's fetch carries no Weft login of any kind.
    return await client.get(url, headers={"Origin": "", "X-Weft-CSRF": "", "Authorization": ""})


def key_in(guide: str) -> str:
    return re.search(r"Bearer (weft_[A-Za-z0-9_-]+)", guide)[1]


@pytest.mark.asyncio
async def test_an_invite_delivers_a_working_key_once(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")

    invite = await invite_for(door_client, weft["id"])
    assert invite["url"].startswith("https://weft.test/api/door/invite/")
    assert invite["expires_in"] == door.INVITE_SECONDS
    assert (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()["open"] is False

    page = await read(door_client, invite["url"])
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/markdown")
    assert '"Jarvis"' in page.text
    assert "https://weft.test/api/mcp" in page.text
    key = key_in(page.text)
    assert (await rpc(door_client, key, "tools/list")).status_code == 200
    assert (await door_client.get(f"/api/wefts/{weft['id']}/door")).json()["open"] is True

    again = await read(door_client, invite["url"])
    assert again.status_code == 410
    assert "weft_" not in again.text


@pytest.mark.asyncio
async def test_a_new_invite_replaces_the_waiting_one(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")

    first = await invite_for(door_client, weft["id"])
    second = await invite_for(door_client, weft["id"])

    assert (await read(door_client, first["url"])).status_code == 410
    assert (await read(door_client, second["url"])).status_code == 200


@pytest.mark.asyncio
async def test_reading_an_invite_replaces_the_old_key(door_client, session):
    weft, old = await weft_with_key(door_client, session)

    page = await read(door_client, (await invite_for(door_client, weft["id"]))["url"])

    assert (await rpc(door_client, old, "tools/list")).status_code == 401
    assert (await rpc(door_client, key_in(page.text), "tools/list")).status_code == 200


@pytest.mark.asyncio
async def test_closing_the_door_withdraws_a_waiting_invite(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")
    invite = await invite_for(door_client, weft["id"])

    await door_client.delete(f"/api/wefts/{weft['id']}/door")

    assert (await read(door_client, invite["url"])).status_code == 410


@pytest.mark.asyncio
async def test_a_lapsed_invite_is_gone(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")
    invite = await invite_for(door_client, weft["id"])

    for digest, waiting in door._invites.items():
        door._invites[digest] = dataclasses.replace(waiting, expires=0)

    assert (await read(door_client, invite["url"])).status_code == 410


@pytest.mark.asyncio
async def test_a_head_request_does_not_use_up_an_invite(door_client, session):
    """A link preview, or a client checking the address, must not burn it."""
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")
    invite = await invite_for(door_client, weft["id"])

    await door_client.head(invite["url"])

    assert (await read(door_client, invite["url"])).status_code == 200


@pytest.mark.asyncio
async def test_no_invite_for_another_users_weft(door_client, session):
    await signed_in(door_client, session, username="bob")
    bobs = await add_weft(door_client, "Jarvis")
    await signed_in(door_client, session, username="alice")

    response = await door_client.post(f"/api/wefts/{bobs['id']}/door/invite")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_hand_made_key_comes_with_its_guide(door_client, session):
    await signed_in(door_client, session)
    weft = await add_weft(door_client, "Jarvis")

    minted = (await door_client.post(f"/api/wefts/{weft['id']}/door")).json()

    assert key_in(minted["guide"]) == minted["key"]
    assert "https://weft.test/api/mcp" in minted["guide"]
