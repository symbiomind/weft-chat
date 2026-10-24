"""
The provider vault: AES-GCM at rest for the one reversible secret in this schema.

Everything else this service stores is a one-way digest — passwords are argon2id,
refresh and invite tokens are SHA-256. A provider bearer token cannot be, because we
have to *present* it upstream. So it is the only thing here that can be turned back
into plaintext, and this module is the only place that can do it.

**Why AES-GCM and not Fernet.** Fernet would be fewer lines, but it has no associated
data, and associated data is what makes a ciphertext un-movable (see below). GCM also
keeps the stored blob small and puts authentication in the same primitive as
encryption, so there is no "decrypt then forget to verify" shape to get wrong.

**The nonce is fresh on every write, and that is not a style preference.** Reusing a
nonce under one key in GCM does not merely weaken the ciphertext: it leaks the XOR of
the two plaintexts *and* allows forgery of the authentication tag for that key. It is a
key-compromising failure, not a degradation. ``os.urandom(12)`` per write, every write,
no exceptions — which is why sealing is a function rather than a reusable cipher object
somebody could be tempted to hold onto.

**Associated data binds a row to its owner.** The AAD is
``weft:provider:<user_id>:<provider_id>``. It is not secret — it is right there in the
row — but it is authenticated, so a ciphertext copied from one row into another fails
to open. That closes an attack the "no endpoint returns the plaintext" rule does not:
someone with write access to the database could otherwise paste another user's
``secret_ciphertext`` into a provider row of their own, point its ``base_url`` at a
host they control, and have this service decrypt and hand over the victim's key.
Nothing about that requires reading the vault key.

**Key rotation.** ``vault.key`` seals; ``vault.old_keys`` only opens. A row records the
``key_id`` that sealed it, so rotation is: move the current key into ``old_keys``, set a
new ``vault.key`` and ``key_id``, and re-seal rows lazily or in a script. Nothing has to
happen atomically, and no row becomes unreadable in the meantime.
"""

import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import config

logger = logging.getLogger(__name__)

#: GCM's standard nonce length. 96 bits is the size the mode is specified and optimised
#: for; other lengths are legal but go through an extra derivation step and buy nothing.
NONCE_BYTES = 12

#: How many trailing characters of a secret we keep in the clear for read-back.
#: Four characters of a provider key is not enough to use and is enough to recognise —
#: which is the entire job of the hint.
HINT_CHARS = 4

#: A generous ceiling. Real bearer tokens are well under this; the limit exists so a
#: multi-megabyte "secret" cannot be used to bloat the database through a normal route.
MAX_SECRET_CHARS = 4096


class VaultError(Exception):
    """Sealing or opening failed. Callers turn this into a 503 or a 500 — never into a
    message that distinguishes *why*, since the difference between "wrong key" and
    "corrupt row" is information about the vault."""


def available() -> bool:
    return config.vault_available()


def associated_data(user_id: str, provider_id: str) -> bytes:
    """The AAD for one provider row.

    Both halves matter. ``user_id`` stops a ciphertext moving between accounts;
    ``provider_id`` stops it moving between one user's own rows, which is a pointless
    attack but free to prevent. Changing this format invalidates every stored row, so
    it is a migration, not an edit.
    """
    return f"weft:provider:{user_id}:{provider_id}".encode("utf-8")


def _current_key() -> tuple[str, bytes]:
    key = config.vault_key()
    if key is None:
        raise VaultError("The provider vault is not configured.")
    key_id = str(config.get("vault.key_id") or "").strip()
    if not key_id:
        # A row sealed with an unnamed key cannot be found again after a rotation.
        # Refusing is better than writing an unopenable row.
        raise VaultError("vault.key_id is not set — sealed rows would be unidentifiable.")
    return key_id, key


def _key_for(key_id: str) -> bytes:
    """Resolve a row's key_id to key material, current key first, then retired ones."""
    current_id = str(config.get("vault.key_id") or "").strip()
    current = config.vault_key()
    if current is not None and key_id == current_id:
        return current

    old = config.vault_old_keys().get(key_id)
    if old is not None:
        return old

    raise VaultError(f"No vault key with id {key_id!r} is loaded.")


def hint_for(secret: str) -> str | None:
    """The masked read-back tail, or None when there is nothing worth showing.

    A secret shorter than the hint length would be almost entirely revealed by its own
    hint, so those get no hint at all.
    """
    if len(secret) <= HINT_CHARS:
        return None
    return secret[-HINT_CHARS:]


def seal(secret: str, *, user_id: str, provider_id: str) -> tuple[bytes, bytes, str]:
    """Encrypt a provider secret. Returns ``(ciphertext, nonce, key_id)``.

    An empty secret is sealed like any other value rather than stored as NULL: local
    gateways often need no bearer at all, and a row whose ciphertext is absent would
    need a second code path everywhere that opens one.
    """
    if len(secret) > MAX_SECRET_CHARS:
        raise VaultError("Secret is too long.")

    key_id, key = _current_key()
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(
        nonce, secret.encode("utf-8"), associated_data(user_id, provider_id)
    )
    return ciphertext, nonce, key_id


def open_secret(
    ciphertext: bytes, nonce: bytes, key_id: str, *, user_id: str, provider_id: str
) -> str:
    """Decrypt a provider secret, or raise.

    Every failure — wrong key, tampered ciphertext, a row moved between users — arrives
    here as the same ``InvalidTag``, and leaves as the same ``VaultError``. That is the
    correct amount of detail for a caller to have.
    """
    key = _key_for(key_id)
    try:
        plaintext = AESGCM(key).decrypt(
            nonce, ciphertext, associated_data(user_id, provider_id)
        )
    except InvalidTag as exc:
        # Worth a log line: this is either a rotation done wrong or someone editing the
        # database by hand, and both are things an operator wants to know happened.
        logger.error(
            "Vault open failed for provider %s (user %s, key_id %s). The row does not "
            "authenticate under that key — rotation gone wrong, or the row was moved.",
            provider_id, user_id, key_id,
        )
        raise VaultError("Could not open the sealed secret.") from exc

    return plaintext.decode("utf-8")
