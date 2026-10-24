"""
Tests for app/config.py.

**Why this file matters.** config.py is the module that decides whether the rest of the
service is allowed to run. Two properties are load-bearing and both are easy to break
without noticing:

1. **It never raises.** If a bad config.yml could crash the boot, a typo made over SSH
   at 2am takes the service down with no /health to diagnose it from. Every failure
   path below asserts the loader survives.

2. **A missing secret degrades exactly one subsystem.** An unset WEFT_VAULT_KEY must
   turn off the provider vault and nothing else — it must not take down login, and it
   must absolutely not fall through to using the literal string "${WEFT_VAULT_KEY}" as
   an encryption key. The env-expansion tests exist for that second failure, which
   would be silent, catastrophic, and pass every "does it start?" check.
"""

import base64
import os

import pytest

from app import config


@pytest.fixture(autouse=True)
def reset_config_state():
    """Every test gets a clean module. config.py holds global state on purpose (it is
    read from everywhere), so tests must not leak into each other."""
    config._SERVER_CFG = {}
    config._config_loaded = False
    yield
    config._SERVER_CFG = {}
    config._config_loaded = False


def write_cfg(tmp_path, text: str) -> str:
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------

def test_missing_file_does_not_raise_and_uses_defaults(tmp_path):
    config.load_config(str(tmp_path / "nope.yml"), force=True)
    assert config.is_config_loaded() is False
    assert config.get("port") == 5010
    assert config.get("jwt.algorithm") == "HS256"


def test_unparseable_yaml_does_not_raise(tmp_path):
    path = write_cfg(tmp_path, "server:\n\tname: tabs-are-illegal\n")
    config.load_config(path, force=True)
    assert config.is_config_loaded() is False
    assert config.get("name") == "weft-service"


def test_empty_file_does_not_raise(tmp_path):
    config.load_config(write_cfg(tmp_path, ""), force=True)
    assert config.is_config_loaded() is False
    assert config.get("port") == 5010


def test_missing_server_block_does_not_raise(tmp_path):
    config.load_config(write_cfg(tmp_path, "notserver:\n  port: 1\n"), force=True)
    assert config.is_config_loaded() is False
    assert config.get("port") == 5010


def test_scalar_where_mapping_expected_does_not_raise(tmp_path):
    """`server: "oops"` is valid YAML and structurally wrong. It must degrade."""
    config.load_config(write_cfg(tmp_path, 'server: "oops"\n'), force=True)
    assert config.is_config_loaded() is False
    assert config.get("port") == 5010


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_second_load_is_a_noop_without_force(tmp_path):
    config.load_config(write_cfg(tmp_path, "server:\n  port: 6000\n"), force=True)
    assert config.get("port") == 6000

    config.load_config(write_cfg(tmp_path, "server:\n  port: 7000\n"))
    assert config.get("port") == 6000, "load_config without force must not re-parse"

    config.load_config(write_cfg(tmp_path, "server:\n  port: 7000\n"), force=True)
    assert config.get("port") == 7000


# ---------------------------------------------------------------------------
# Env expansion — the silent-catastrophe tests
# ---------------------------------------------------------------------------

def test_env_var_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("WEFT_TEST_SECRET", "s3cret-from-env")
    path = write_cfg(tmp_path, 'server:\n  jwt:\n    secret: "${WEFT_TEST_SECRET}"\n')
    config.load_config(path, force=True)
    assert config.jwt_secret() == "s3cret-from-env"


def test_missing_env_var_becomes_empty_not_the_literal(tmp_path, monkeypatch):
    """The failure this prevents: an unset var leaving "${WEFT_VAULT_KEY}" in place,
    which is a non-empty string, so every presence check passes and the service
    cheerfully encrypts everything under a publicly-known 'key'."""
    monkeypatch.delenv("WEFT_DEFINITELY_UNSET", raising=False)
    path = write_cfg(tmp_path, 'server:\n  jwt:\n    secret: "${WEFT_DEFINITELY_UNSET}"\n')
    config.load_config(path, force=True)

    assert config.jwt_secret() == ""
    assert "${" not in config.jwt_secret()
    assert config.auth_available() is False


def test_env_expansion_reaches_nested_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("WEFT_TEST_ORIGIN", "https://from-env.example")
    path = write_cfg(
        tmp_path,
        'server:\n  security:\n    allowed_origins:\n      - "${WEFT_TEST_ORIGIN}"\n',
    )
    config.load_config(path, force=True)
    assert "https://from-env.example" in config.allowed_origins()


# ---------------------------------------------------------------------------
# Lookup semantics
# ---------------------------------------------------------------------------

def test_partial_config_keeps_sibling_defaults(tmp_path):
    """Setting one security key must not blank the other twelve."""
    path = write_cfg(tmp_path, "server:\n  security:\n    login_max_attempts: 99\n")
    config.load_config(path, force=True)
    assert config.get("security.login_max_attempts") == 99
    assert config.get("security.access_token_ttl_seconds") == 900
    assert config.get("security.cookie_secure") is True


def test_explicit_false_does_not_fall_through_to_default(tmp_path):
    """`found` must distinguish absent from falsy, or `expose_docs: false` silently
    becomes the True default and publishes the auth surface."""
    path = write_cfg(tmp_path, "server:\n  security:\n    cookie_secure: false\n")
    config.load_config(path, force=True)
    assert config.get("security.cookie_secure") is False


def test_unknown_dotted_path_returns_supplied_default(tmp_path):
    config.load_config(write_cfg(tmp_path, "server:\n  port: 1\n"), force=True)
    assert config.get("no.such.key", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------

def test_public_origin_is_always_allowed(tmp_path):
    """Forgetting to list your own origin must not lock you out of your own service."""
    path = write_cfg(
        tmp_path,
        'server:\n  public_origin: "https://weft.example.com"\n'
        '  security:\n    allowed_origins: []\n',
    )
    config.load_config(path, force=True)
    assert config.allowed_origins() == ["https://weft.example.com"]


def test_trailing_slashes_are_normalised(tmp_path):
    """An Origin header never has a trailing slash, so a configured one would never
    match and the operator would be left staring at a correct-looking config."""
    path = write_cfg(
        tmp_path,
        'server:\n  public_origin: "https://a.example/"\n'
        '  security:\n    allowed_origins: ["https://b.example/"]\n',
    )
    config.load_config(path, force=True)
    assert config.allowed_origins() == ["https://a.example", "https://b.example"]


def test_non_list_allowed_origins_degrades_closed(tmp_path):
    """A string where a list belongs must not be iterated character by character into
    a set of single-letter 'origins'."""
    path = write_cfg(
        tmp_path,
        'server:\n  public_origin: "https://a.example"\n'
        '  security:\n    allowed_origins: "https://b.example"\n',
    )
    config.load_config(path, force=True)
    assert config.allowed_origins() == ["https://a.example"]


# ---------------------------------------------------------------------------
# Vault key shape
# ---------------------------------------------------------------------------

def _key_b64(nbytes: int = 32) -> str:
    return base64.urlsafe_b64encode(os.urandom(nbytes)).decode()


def test_valid_vault_key_decodes_to_32_bytes(tmp_path):
    raw = _key_b64()
    config.load_config(write_cfg(tmp_path, f'server:\n  vault:\n    key: "{raw}"\n'), force=True)
    key = config.vault_key()
    assert key is not None and len(key) == config.SECRET_BYTES
    assert config.vault_available() is True


def test_absent_vault_key_disables_only_the_vault(tmp_path):
    """The degradation-isolation test: no vault key must not touch auth."""
    path = write_cfg(
        tmp_path,
        'server:\n  jwt:\n    secret: "present"\n  vault:\n    key: ""\n',
    )
    config.load_config(path, force=True)
    assert config.vault_available() is False
    assert config.auth_available() is True
    assert config.degradation_report()["vault"] == "unavailable"
    assert config.degradation_report()["auth"] == "ok"


def test_malformed_base64_vault_key_is_rejected(tmp_path):
    path = write_cfg(tmp_path, 'server:\n  vault:\n    key: "not!valid!base64!"\n')
    config.load_config(path, force=True)
    assert config.vault_key() is None


def test_short_vault_key_is_rejected(tmp_path):
    """16 bytes is valid base64 and the wrong key size. Accepting it would silently
    halve the security of every stored provider token."""
    config.load_config(write_cfg(tmp_path, f'server:\n  vault:\n    key: "{_key_b64(16)}"\n'), force=True)
    assert config.vault_key() is None


def test_unpadded_base64_key_is_accepted(tmp_path):
    """Generators differ on emitting '='. Rejecting an unpadded key would be a
    confusing false negative on a perfectly good secret."""
    raw = _key_b64().rstrip("=")
    config.load_config(write_cfg(tmp_path, f'server:\n  vault:\n    key: "{raw}"\n'), force=True)
    assert config.vault_key() is not None


# ---------------------------------------------------------------------------
# Rotation keys
# ---------------------------------------------------------------------------

def test_old_keys_parse_into_a_mapping(tmp_path):
    k0, k1 = _key_b64(), _key_b64()
    path = write_cfg(tmp_path, f'server:\n  vault:\n    old_keys: "k0:{k0},k1:{k1}"\n')
    config.load_config(path, force=True)
    old = config.vault_old_keys()
    assert set(old) == {"k0", "k1"}
    assert all(len(v) == 32 for v in old.values())


def test_one_malformed_old_key_does_not_strand_the_others(tmp_path):
    """A bad entry must be skipped, not abort the parse — otherwise a single typo
    makes every historically-encrypted row undecryptable."""
    good = _key_b64()
    path = write_cfg(tmp_path, f'server:\n  vault:\n    old_keys: "broken,k1:{good},k2:!!!"\n')
    config.load_config(path, force=True)
    assert set(config.vault_old_keys()) == {"k1"}


def test_empty_old_keys_is_an_empty_mapping(tmp_path):
    config.load_config(write_cfg(tmp_path, 'server:\n  vault:\n    old_keys: ""\n'), force=True)
    assert config.vault_old_keys() == {}


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------

def test_degradation_report_has_every_subsystem(tmp_path):
    config.load_config(write_cfg(tmp_path, "server:\n  port: 1\n"), force=True)
    report = config.degradation_report()
    assert set(report) == {"config_loaded", "auth", "csrf", "vault"}


def test_fully_configured_reports_all_ok(tmp_path):
    path = write_cfg(
        tmp_path,
        'server:\n'
        '  jwt:\n    secret: "a"\n'
        '  csrf:\n    secret: "b"\n'
        f'  vault:\n    key: "{_key_b64()}"\n',
    )
    config.load_config(path, force=True)
    assert config.degradation_report() == {
        "config_loaded": True, "auth": "ok", "csrf": "ok", "vault": "ok",
    }


# ---------------------------------------------------------------------------
# Secret strength
# ---------------------------------------------------------------------------

def test_short_signing_secret_is_warned_about(tmp_path, caplog):
    """RFC 7518 §3.2: an HMAC key shorter than the hash output is weaker than it
    looks, and nothing fails to make that visible — signing still works fine. The
    operator who pasted a memorable string instead of running the generator gets no
    other signal."""
    import logging

    path = write_cfg(tmp_path, 'server:\n  jwt:\n    secret: "tooshort"\n')
    with caplog.at_level(logging.WARNING, logger="app.config"):
        config.load_config(path, force=True)

    messages = [r.getMessage() for r in caplog.records]
    assert any("jwt.secret is only 8 bytes" in m for m in messages), messages


def test_adequate_secret_is_not_warned_about(tmp_path, caplog):
    import logging

    path = write_cfg(tmp_path, 'server:\n  jwt:\n    secret: "%s"\n' % ("x" * 32))
    with caplog.at_level(logging.WARNING, logger="app.config"):
        config.load_config(path, force=True)

    assert not any("jwt.secret is only" in r.getMessage() for r in caplog.records)
