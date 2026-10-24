"""
Notifications: an agent reports in through the door, the user reads and
dismisses.

The assertions that would matter:

* ``test_an_agent_reports_in_and_the_user_dismisses_it`` — the whole round trip, from
  the ``notify`` tool to the counts the window polls and back to nothing.
* ``test_another_user_sees_none_of_it`` — a notification is its weft's user's alone.
* ``test_a_looping_agent_is_held_to_its_hourly_rate`` and
  ``test_only_the_newest_wait`` — the two bounds on what a stuck agent can pile up.
"""

import pytest
from sqlalchemy import func, select

from app import notifications
from app.models import Notification
from tests.test_door import call, door_client, text_of, weft_with_key  # noqa: F401
from tests.test_provider_routes import signed_in
from tests.test_weft_routes import add_weft


@pytest.fixture(autouse=True)
def fresh_rate():
    notifications._sent.clear()
    yield
    notifications._sent.clear()


async def counts(client) -> dict:
    response = await client.get("/api/notifications/counts")
    assert response.status_code == 200, response.text
    return response.json()["wefts"]


async def listed(client, weft_id) -> list:
    response = await client.get("/api/notifications", params={"weft": weft_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_an_agent_reports_in_and_the_user_dismisses_it(door_client, session):
    weft, key = await weft_with_key(door_client, session)

    result = await call(door_client, key, "notify", title="Build finished",
                        body="All green.\n\n![chart](https://weft.test/api/attachments/" + "a" * 64 + ")")
    assert not result.get("isError")
    assert "Delivered" in text_of(result)
    assert "1 from this weft waiting" in text_of(result)

    assert await counts(door_client) == {weft["id"]: 1}
    [note] = await listed(door_client, weft["id"])
    assert note["title"] == "Build finished"
    assert note["body"].startswith("All green.")
    assert "/api/attachments/" in note["body"]

    gone = await door_client.delete(f"/api/notifications/{note['id']}")
    assert gone.status_code == 204
    assert await counts(door_client) == {}
    assert await listed(door_client, weft["id"]) == []


@pytest.mark.asyncio
async def test_status_says_how_many_are_waiting(door_client, session):
    _, key = await weft_with_key(door_client, session)
    assert "waiting for your user: 0" in text_of(await call(door_client, key, "status"))

    await call(door_client, key, "notify", title="one")
    await call(door_client, key, "notify", title="two")
    assert "waiting for your user: 2" in text_of(await call(door_client, key, "status"))


@pytest.mark.asyncio
async def test_newest_comes_first(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    for title in ("first", "second", "third"):
        await call(door_client, key, "notify", title=title)
    assert [n["title"] for n in await listed(door_client, weft["id"])] == [
        "third", "second", "first",
    ]


@pytest.mark.asyncio
async def test_a_notification_needs_a_title(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    result = await call(door_client, key, "notify", title="   ", body="words")
    assert result.get("isError")
    assert await counts(door_client) == {}


@pytest.mark.asyncio
async def test_a_long_report_is_cut_to_fit(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    await call(door_client, key, "notify", title="t" * 500, body="b" * 9000)
    [note] = await listed(door_client, weft["id"])
    assert len(note["title"]) == notifications.MAX_TITLE
    assert len(note["body"]) == notifications.MAX_BODY
    assert note["body"].endswith("…")


@pytest.mark.asyncio
async def test_a_looping_agent_is_held_to_its_hourly_rate(door_client, session, monkeypatch):
    monkeypatch.setattr(notifications, "PER_HOUR", 2)
    weft, key = await weft_with_key(door_client, session)

    await call(door_client, key, "notify", title="one")
    await call(door_client, key, "notify", title="two")
    refused = await call(door_client, key, "notify", title="three")

    assert refused.get("isError")
    assert "in the last hour" in text_of(refused)
    assert await counts(door_client) == {weft["id"]: 2}


@pytest.mark.asyncio
async def test_only_the_newest_wait(door_client, session, monkeypatch):
    monkeypatch.setattr(notifications, "MAX_WAITING", 3)
    weft, key = await weft_with_key(door_client, session)
    for n in range(5):
        await call(door_client, key, "notify", title=f"report {n}")

    assert [n["title"] for n in await listed(door_client, weft["id"])] == [
        "report 4", "report 3", "report 2",
    ]


@pytest.mark.asyncio
async def test_dismissing_all_clears_one_weft_only(door_client, session):
    jarvis, jarvis_key = await weft_with_key(door_client, session, "Jarvis")
    friday = await add_weft(door_client, "Friday")
    friday_key = (await door_client.post(f"/api/wefts/{friday['id']}/door")).json()["key"]

    await call(door_client, jarvis_key, "notify", title="from jarvis")
    await call(door_client, jarvis_key, "notify", title="from jarvis again")
    await call(door_client, friday_key, "notify", title="from friday")

    done = await door_client.post("/api/notifications/dismiss", json={"weft_id": jarvis["id"]})
    assert done.json() == {"dismissed": 2}
    assert await counts(door_client) == {friday["id"]: 1}


@pytest.mark.asyncio
async def test_another_user_sees_none_of_it(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    await call(door_client, key, "notify", title="for alice")
    [note] = await listed(door_client, weft["id"])

    await signed_in(door_client, session, username="bob")
    assert await counts(door_client) == {}
    assert (await door_client.get("/api/notifications", params={"weft": weft["id"]})).status_code == 404
    assert (await door_client.delete(f"/api/notifications/{note['id']}")).status_code == 404
    refused = await door_client.post("/api/notifications/dismiss", json={"weft_id": weft["id"]})
    assert refused.status_code == 404
    assert await session.scalar(select(func.count()).select_from(Notification)) == 1


@pytest.mark.asyncio
async def test_a_new_key_keeps_what_was_sent_and_the_weft_takes_it_away(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    await call(door_client, key, "notify", title="before the new key")

    assert (await door_client.post(f"/api/wefts/{weft['id']}/door")).status_code == 201
    assert await counts(door_client) == {weft["id"]: 1}

    assert (await door_client.delete(f"/api/wefts/{weft['id']}")).status_code == 204
    assert await counts(door_client) == {}


@pytest.mark.asyncio
async def test_the_writes_need_the_browsers_csrf(door_client, session):
    weft, key = await weft_with_key(door_client, session)
    await call(door_client, key, "notify", title="x")
    [note] = await listed(door_client, weft["id"])

    del door_client.headers["X-Weft-CSRF"]
    assert (await door_client.delete(f"/api/notifications/{note['id']}")).status_code == 403
