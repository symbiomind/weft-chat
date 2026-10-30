"""
Web Push, the protocol: one encrypted message to one browser's push service.

A browser that subscribes hands over an endpoint on its vendor's push service and two
keys. Sending is an HTTP POST to that endpoint, and three RFCs say what goes in it:

* RFC 8291, **message encryption**. The body is sealed to the browser's ``p256dh`` key
  and ``auth`` secret, so the push service carries it without being able to read it.
  An ephemeral key pair per message, ECDH with the browser's key, HKDF, AES-128-GCM.
* RFC 8188, the ``aes128gcm`` **content coding** that carries the result: a header with
  the salt and the sender's public key, then a single record.
* RFC 8292, **VAPID**. A short-lived ES256 JWT signed with this server's private key.
  The browser bound its subscription to the matching public key when it subscribed, so
  the push service refuses anyone else's signature. A stolen list of endpoints is no
  use without the key.

Hand-rolled rather than ``pywebpush``: the primitives are already here (cryptography for
the vault, PyJWT for sessions, httpx for providers), the whole protocol is the two
functions below, and ``test_push.py`` holds them to RFC 8291's own worked example.

Nothing here knows about users, wefts or the database. See ``app/push.py``.
"""

import base64
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: The one record's size, as the header declares it. A payload here is a few dozen
#: bytes, far under it, so there is only ever one record.
RECORD_SIZE = 4096

#: How long a VAPID token is good for. RFC 8292 caps it at 24 hours; push services
#: are stricter in practice, and a token is made per send anyway.
TOKEN_SECONDS = 12 * 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """The uncompressed point, 65 bytes starting 0x04: the form Web Push speaks."""
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def private_key(raw: bytes) -> ec.EllipticCurvePrivateKey:
    """A P-256 private key from its 32-byte scalar, which is how VAPID keys are written."""
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def new_private_key() -> bytes:
    """A fresh VAPID private key, as its 32-byte scalar."""
    return ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")


def public_key(raw_private: bytes) -> str:
    """The public half, as the browser wants it for ``applicationServerKey``."""
    return b64url(_public_bytes(private_key(raw_private).public_key()))


def encrypt(
    payload: bytes, p256dh: str, auth: str, *,
    sender: ec.EllipticCurvePrivateKey | None = None, salt: bytes | None = None,
) -> bytes:
    """Seal ``payload`` for one subscription: the whole request body (RFC 8291 §3-4).

    ``sender`` and ``salt`` are fresh for every message and only ever passed by the
    test that checks this against the RFC's example. Reusing either across messages
    would reuse a GCM key and nonce.
    """
    receiver_bytes = unb64url(p256dh)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_bytes)
    auth_secret = unb64url(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    sender_bytes = _public_bytes(sender.public_key())

    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + receiver_bytes + sender_bytes, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # 0x02 marks the last (and only) record, with no padding after it.
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(sender_bytes)) + sender_bytes
    return header + sealed


def vapid_header(endpoint: str, raw_private: bytes, subject: str) -> str:
    """The Authorization header that proves this server is the one subscribed to."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + TOKEN_SECONDS,
        "sub": subject,
    }
    token = jwt.encode(claims, private_key(raw_private), algorithm="ES256")
    return f"vapid t={token}, k={public_key(raw_private)}"


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str


async def send(
    client: httpx.AsyncClient, subscription: Subscription, payload: bytes, *,
    raw_private: bytes, subject: str, ttl: int, topic: str | None = None,
) -> int:
    """POST one message. Returns the push service's status: 201 delivered, 404 or 410
    the subscription is gone, anything else a failure for the caller to log.

    ``topic`` lets the push service replace an undelivered message with a newer one of
    the same topic, so a phone that was off wakes to one notification per weft, not a
    stack of them (RFC 8030 §5.4).
    """
    headers = {
        "Authorization": vapid_header(subscription.endpoint, raw_private, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        # High, or Android holds it in doze until the phone wakes: minutes late, which
        # for a report someone is waiting on is the whole point lost. Sends are already
        # held to one per weft per minute.
        "Urgency": "high",
    }
    if topic:
        headers["Topic"] = topic
    body = encrypt(payload, subscription.p256dh, subscription.auth)
    response = await client.post(subscription.endpoint, content=body, headers=headers)
    return response.status_code
