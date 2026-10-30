"""Print a new VAPID key pair for Web Push.

The private key goes in the environment as WEFT_PUSH_KEY (config.yml reads it as
push.key); the public key is printed only so you can recognise it. The server works
it out from the private key and hands it to browsers itself.

    python3 scripts/make_push_key.py

A new key cuts off every device subscribed with the old one, until each visits again.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    print(f"WEFT_PUSH_KEY={b64url(private)}")
    print(f"# public key: {b64url(public)}")


if __name__ == "__main__":
    main()
