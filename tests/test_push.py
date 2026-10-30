"""
Push: a device subscribes, and an agent's notification rings it while Weft is closed.

The assertions that would matter:

* ``test_encryption_matches_the_rfc_example`` — RFC 8291's own worked example, byte for
  byte. Get the key schedule wrong and every push is undecryptable noise to the browser,
  which drops it without a word.
* ``test_vapid_proves_the_sender`` — the push service's check, done here: the token
  verifies against the public key the browser subscribed with.
* ``test_a_subscription_must_point_at_a_push_service`` — the server POSTs to the
  endpoint, so an endpoint anywhere else is refused.
* ``test_a_signed_out_device_is_not_rung`` — a shared browser never rings for whoever
  used it last.
* ``test_an_agents_notification_rings_with_who_never_what`` — the round trip from the
  door, and the payload carrying the weft's name and count, never the report.
"""

import asyncio
import base64
import json
import os

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, update

from app import config, notifications, push, push_icons, webpush
from app.models import AuthSession, PushSubscription, Weft
from tests.test_door import call, door_client, weft_with_key  # noqa: F401

FCM = "https://fcm.googleapis.com/fcm/send/"


@pytest.fixture(autouse=True)
def fresh_state():
    notifications._sent.clear()
    push._last.clear()
    push._pending.clear()
    yield
    notifications._sent.clear()
    push._last.clear()
    push._pending.clear()


@pytest.fixture
def push_on(door_client):
    """Push turned on, with a fresh key, for this test's config."""
    raw = webpush.new_private_key()
    config._SERVER_CFG["push"] = {
        "enabled": True, "key": base64.urlsafe_b64encode(raw).decode(),
    }
    return raw


class Calls(list):
    """What the push service was sent, and what it will answer (``answer["status"]``)."""

    answer: dict


@pytest.fixture
def sent(monkeypatch):
    """Replace the POST to the push service, and record what it was given."""
    calls = Calls()
    answer = {"status": 201}

    async def fake(client, subscription, payload, **kwargs):
        calls.append((subscription, json.loads(payload), kwargs))
        return answer["status"]

    monkeypatch.setattr(webpush, "send", fake)
    calls.answer = answer
    return calls


def browser_keys() -> dict:
    """What a browser hands over on subscribing: its public key and an auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    return {"p256dh": webpush.b64url(public), "auth": webpush.b64url(os.urandom(16))}


async def subscribe(client, endpoint=FCM + "device-1", keys=None):
    return await client.put("/api/push/subscription",
                            json={"endpoint": endpoint, "keys": keys or browser_keys()})


async def settle():
    """Let the background sends finish."""
    while push._tasks:
        await asyncio.gather(*list(push._tasks))


# ---- the protocol -----------------------------------------------------------

def test_encryption_matches_the_rfc_example():
    # RFC 8291, Appendix A.
    sender = webpush.private_key(webpush.unb64url("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        sender=sender, salt=webpush.unb64url("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert webpush.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocIn"
        "mYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQ"
        "exSgSxsj_Qulcy4a-fN"
    )


def test_every_message_is_sealed_afresh():
    keys = browser_keys()
    assert webpush.encrypt(b"same", **keys) != webpush.encrypt(b"same", **keys)


def test_vapid_proves_the_sender():
    raw = webpush.new_private_key()
    header = webpush.vapid_header(FCM + "abc", raw, "https://weft.test")
    token = header.split("t=")[1].split(",")[0]
    public = header.split("k=")[1]
    assert public == webpush.public_key(raw)

    verifier = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), webpush.unb64url(public))
    claims = jwt.decode(token, verifier, algorithms=["ES256"],
                        audience="https://fcm.googleapis.com")
    assert claims["sub"] == "https://weft.test"

    stranger = ec.generate_private_key(ec.SECP256R1()).public_key()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, stranger, algorithms=["ES256"], audience="https://fcm.googleapis.com")


# ---- the switch -------------------------------------------------------------

@pytest.mark.asyncio
async def test_off_unless_the_instance_turns_it_on(door_client, session):
    await weft_with_key(door_client, session)
    response = await door_client.get("/api/push")
    assert response.json() == {"available": False, "public_key": None}
    assert (await subscribe(door_client)).status_code == 409


@pytest.mark.asyncio
async def test_on_it_hands_out_the_public_key(door_client, session, push_on):
    await weft_with_key(door_client, session)
    response = await door_client.get("/api/push")
    assert response.json() == {"available": True, "public_key": webpush.public_key(push_on)}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [
    "https://10.0.0.1/push",
    "https://evil.test/fcm.googleapis.com",
    "https://fcm.googleapis.com.evil.test/x",
    "http://fcm.googleapis.com/fcm/send/x",
])
async def test_a_subscription_must_point_at_a_push_service(door_client, session, push_on, endpoint):
    await weft_with_key(door_client, session)
    assert (await subscribe(door_client, endpoint)).status_code == 422
    assert await session.scalar(select(func.count()).select_from(PushSubscription)) == 0


@pytest.mark.asyncio
async def test_apple_and_mozilla_are_push_services_too(door_client, session, push_on):
    await weft_with_key(door_client, session)
    for endpoint in ("https://web.push.apple.com/abc",
                     "https://jmt17.google.com/fcm/send/abc",
                     "https://updates.push.services.mozilla.com/wpush/v2/abc"):
        assert (await subscribe(door_client, endpoint)).status_code == 204


@pytest.mark.asyncio
async def test_keys_must_be_keys(door_client, session, push_on):
    await weft_with_key(door_client, session)
    bad = browser_keys() | {"auth": webpush.b64url(b"short")}
    assert (await subscribe(door_client, keys=bad)).status_code == 422


@pytest.mark.asyncio
async def test_subscribing_again_moves_the_row(door_client, session, push_on):
    await weft_with_key(door_client, session)
    assert (await subscribe(door_client)).status_code == 204
    assert (await subscribe(door_client)).status_code == 204
    assert await session.scalar(select(func.count()).select_from(PushSubscription)) == 1

    gone = await door_client.request("DELETE", "/api/push/subscription",
                                     json={"endpoint": FCM + "device-1"})
    assert gone.status_code == 204
    assert await session.scalar(select(func.count()).select_from(PushSubscription)) == 0


# ---- the ring ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_agents_notification_rings_with_who_never_what(
    door_client, session, push_on, sent,
):
    weft, key = await weft_with_key(door_client, session, name="Builder")
    await subscribe(door_client)

    await call(door_client, key, "notify", title="Secret plans", body="The vault code is 1234")
    await settle()

    [(subscription, payload, kwargs)] = sent
    assert subscription.endpoint == FCM + "device-1"
    assert payload == {"weft": weft["id"], "name": "Builder", "count": 1,
                       "icon": payload["icon"]}
    assert push_icons.NAME.match(payload["icon"])
    assert kwargs["raw_private"] == push_on
    assert len(kwargs["topic"]) <= 32
    assert (await session.scalar(select(PushSubscription))).last_ok_at is not None


@pytest.mark.asyncio
async def test_a_signed_out_device_is_not_rung(door_client, session, push_on, sent):
    _, key = await weft_with_key(door_client, session)
    await subscribe(door_client)
    await session.execute(update(AuthSession).values(revoked_at=func.now()))
    await session.commit()

    await call(door_client, key, "notify", title="hello")
    await settle()
    assert sent == []


@pytest.mark.asyncio
async def test_a_gone_subscription_is_forgotten(door_client, session, push_on, sent):
    _, key = await weft_with_key(door_client, session)
    await subscribe(door_client)
    sent.answer["status"] = 410

    await call(door_client, key, "notify", title="hello")
    await settle()
    session.expire_all()
    assert await session.scalar(select(func.count()).select_from(PushSubscription)) == 0


@pytest.mark.asyncio
async def test_a_burst_rings_once_now_and_once_after(door_client, session, push_on, sent, monkeypatch):
    monkeypatch.setattr(push, "MIN_GAP_SECONDS", 0.2)
    _, key = await weft_with_key(door_client, session)
    await subscribe(door_client)

    for title in ("one", "two", "three"):
        await call(door_client, key, "notify", title=title)
    await settle()
    assert [payload["count"] for _, payload, _ in sent] == [1, 3]


@pytest.mark.asyncio
async def test_nothing_rings_with_push_off(door_client, session, sent):
    _, key = await weft_with_key(door_client, session)
    await call(door_client, key, "notify", title="hello")
    await settle()
    assert sent == []


# ---- the icon ---------------------------------------------------------------

async def icon_of(door_client, session, key, sent) -> str:
    await call(door_client, key, "notify", title="hello")
    await settle()
    return sent[-1][1]["icon"]


@pytest.mark.asyncio
async def test_the_icon_is_served_without_a_login(door_client, session, push_on, sent):
    from io import BytesIO

    from PIL import Image

    _, key = await weft_with_key(door_client, session)
    await subscribe(door_client)
    name = await icon_of(door_client, session, key, sent)

    response = await door_client.get(f"/api/push/icons/{name}.png",
                                     headers={"Authorization": "", "X-Weft-CSRF": ""})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "immutable" in response.headers["cache-control"]
    assert Image.open(BytesIO(response.content)).size == (push_icons.SIZE, push_icons.SIZE)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["0" * 64, "../../weft", "A" * 64])
async def test_only_a_kept_icon_is_served(door_client, name):
    assert (await door_client.get(f"/api/push/icons/{name}.png")).status_code == 404


@pytest.mark.asyncio
async def test_a_new_pair_is_a_new_icon_and_the_old_one_is_swept(
    door_client, session, push_on, sent, monkeypatch,
):
    _, key = await weft_with_key(door_client, session)
    await subscribe(door_client)
    before = await icon_of(door_client, session, key, sent)

    weft = await session.scalar(select(Weft))
    weft.colour_a, weft.colour_b = "#e09f3e", "#5d5d81"
    await session.commit()
    push._last.clear()
    after = await icon_of(door_client, session, key, sent)
    assert after != before

    monkeypatch.setattr(push_icons, "SWEEP_GRACE_SECONDS", -1)
    assert await push_icons.sweep(session) == 1
    assert not push_icons.path_for(before).exists()
    assert push_icons.path_for(after).exists()


def test_the_name_needs_the_secret(monkeypatch):
    weft = Weft(name="x", colour_a="#000000", colour_b="#ffffff", background=None)
    monkeypatch.setattr(config, "jwt_secret", lambda: "one-secret")
    first = push_icons.name_for(weft)
    monkeypatch.setattr(config, "jwt_secret", lambda: "another-secret")
    assert push_icons.name_for(weft) != first
