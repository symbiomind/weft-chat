"""
Tests for app/vault.py — the only reversible secret in the schema.

The assertions that would matter in an incident:

* ``test_a_ciphertext_moved_to_another_user_does_not_open`` — the associated-data
  binding. Without it, someone with database write access can steal a key they cannot
  read, by making this service decrypt it and post it to a host they control.
* ``test_two_seals_of_the_same_secret_use_different_nonces`` — nonce reuse under one
  key in GCM is key-compromising, not merely weakening.
* ``test_a_retired_key_still_opens_its_own_rows`` — the property that makes rotation a
  gradual operation rather than an outage.
"""

import base64
import os

import pytest

from app import config, vault

SECRET = "sk-or-v1-abcdef0123456789"
USER = "11111111-1111-1111-1111-111111111111"
PROVIDER = "22222222-2222-2222-2222-222222222222"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


@pytest.fixture
def vault_config(tmp_path):
    """A config with one current vault key and nothing retired."""
    key = _b64(os.urandom(32))
    path = tmp_path / "config.yml"
    path.write_text(
        f'server:\n  vault:\n    key: "{key}"\n    key_id: "k1"\n', encoding="utf-8"
    )
    config.load_config(str(path), force=True)
    return key


def test_round_trip(vault_config):
    ciphertext, nonce, key_id = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)
    assert key_id == "k1"
    assert SECRET.encode() not in ciphertext

    opened = vault.open_secret(
        ciphertext, nonce, key_id, user_id=USER, provider_id=PROVIDER
    )
    assert opened == SECRET


def test_two_seals_of_the_same_secret_use_different_nonces(vault_config):
    """A fresh nonce per write, always.

    GCM with a repeated nonce under one key leaks the XOR of the plaintexts and lets an
    attacker forge tags for that key. This is the test that stops someone "optimising"
    by hoisting a nonce or a cipher object to module scope.
    """
    first = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)
    second = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)

    assert first[1] != second[1]
    assert first[0] != second[0]


def test_an_empty_secret_round_trips(vault_config):
    """A gateway on the private network often needs no bearer. That has to be storable
    as a value, not as a special case every caller remembers to check."""
    ciphertext, nonce, key_id = vault.seal("", user_id=USER, provider_id=PROVIDER)
    assert vault.open_secret(
        ciphertext, nonce, key_id, user_id=USER, provider_id=PROVIDER
    ) == ""


def test_a_ciphertext_moved_to_another_user_does_not_open(vault_config):
    """The attack the AAD exists for.

    No endpoint ever returns a decrypted secret, so reading the vault through the API
    is impossible by construction. But *using* one is not: paste a victim's ciphertext
    into a provider row of your own, point base_url at a host you control, and open the
    models screen. The service decrypts and sends. Binding the ciphertext to its owner
    is what makes that paste fail.
    """
    ciphertext, nonce, key_id = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)

    with pytest.raises(vault.VaultError):
        vault.open_secret(
            ciphertext, nonce, key_id,
            user_id="33333333-3333-3333-3333-333333333333",
            provider_id=PROVIDER,
        )


def test_a_ciphertext_moved_to_another_provider_row_does_not_open(vault_config):
    ciphertext, nonce, key_id = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)

    with pytest.raises(vault.VaultError):
        vault.open_secret(
            ciphertext, nonce, key_id,
            user_id=USER,
            provider_id="44444444-4444-4444-4444-444444444444",
        )


def test_tampered_ciphertext_is_rejected(vault_config):
    """Authenticated encryption, not merely encryption: a flipped bit must fail loudly
    rather than decrypt to garbage the service then sends upstream."""
    ciphertext, nonce, key_id = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)
    broken = bytearray(ciphertext)
    broken[0] ^= 0x01

    with pytest.raises(vault.VaultError):
        vault.open_secret(
            bytes(broken), nonce, key_id, user_id=USER, provider_id=PROVIDER
        )


def test_a_retired_key_still_opens_its_own_rows(tmp_path):
    """Rotation is: current key moves to old_keys, a new one takes its place.

    Rows sealed before the rotation must keep opening, or rotating the vault key is an
    outage for every provider anyone configured.
    """
    old_key = os.urandom(32)
    path = tmp_path / "config.yml"
    path.write_text(
        f'server:\n  vault:\n    key: "{_b64(old_key)}"\n    key_id: "k1"\n',
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)
    ciphertext, nonce, key_id = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)
    assert key_id == "k1"

    # Rotate: k2 is now current, k1 is decrypt-only.
    path.write_text(
        f'server:\n  vault:\n'
        f'    key: "{_b64(os.urandom(32))}"\n'
        f'    key_id: "k2"\n'
        f'    old_keys: "k1:{_b64(old_key)}"\n',
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)

    assert vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)[2] == "k2"
    assert vault.open_secret(
        ciphertext, nonce, "k1", user_id=USER, provider_id=PROVIDER
    ) == SECRET


def test_an_unknown_key_id_raises_rather_than_guessing(vault_config):
    """Trying every loaded key until one works would turn a rotation mistake into a
    silent success and hide that a row is sealed with something nobody tracked."""
    ciphertext, nonce, _ = vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)

    with pytest.raises(vault.VaultError):
        vault.open_secret(
            ciphertext, nonce, "k99", user_id=USER, provider_id=PROVIDER
        )


def test_sealing_without_a_key_raises(tmp_path):
    """The degradation contract: no vault key means /api/providers/* answers 503. That
    only works if seal() refuses rather than inventing a key."""
    path = tmp_path / "config.yml"
    path.write_text("server:\n  name: weft\n", encoding="utf-8")
    config.load_config(str(path), force=True)

    assert not vault.available()
    with pytest.raises(vault.VaultError):
        vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)


def test_sealing_without_a_key_id_raises(tmp_path):
    """A row sealed under an unnamed key cannot be found again after a rotation, so
    writing one is worse than refusing."""
    path = tmp_path / "config.yml"
    path.write_text(
        f'server:\n  vault:\n    key: "{_b64(os.urandom(32))}"\n    key_id: ""\n',
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)

    with pytest.raises(vault.VaultError):
        vault.seal(SECRET, user_id=USER, provider_id=PROVIDER)


def test_an_overlong_secret_is_refused(vault_config):
    with pytest.raises(vault.VaultError):
        vault.seal("x" * (vault.MAX_SECRET_CHARS + 1), user_id=USER, provider_id=PROVIDER)


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------

def test_hint_is_the_last_four_characters():
    assert vault.hint_for("sk-or-v1-abcd7f3a") == "7f3a"


def test_a_short_secret_gets_no_hint():
    """Four characters of a forty-character key is a recognition aid. Four characters
    of a four-character key is the key."""
    assert vault.hint_for("abcd") is None
    assert vault.hint_for("") is None
