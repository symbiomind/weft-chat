"""
Tests for app/bootstrap.py.

**Why this file matters.** Bootstrap is the one code path that creates an account
without anyone being authenticated. Getting its trigger condition wrong is a complete
authentication bypass: if it fired whenever the env vars were set rather than only when
the table is empty, then leaving `WEFT_BOOTSTRAP_PASSWORD` in a .env file — which is
exactly what people do — would let a restart silently re-create or reset the owner
account. `test_does_not_run_when_any_user_exists` is the guard, and it is the most
important assertion in this file.

The rest defend the properties that make the bootstrap credential a single-use door:
the account always lands with must_change_password, and the generated password never
reaches disk as anything other than a 0600 file.
"""

import os
import stat

import pytest
from sqlalchemy import select

from app import bootstrap, config
from app.bootstrap import ensure_owner, generate_password, users_exist
from app.models import Role, User
from app.passwords import verify_password


@pytest.fixture
def bootstrap_file(tmp_path, monkeypatch):
    """Redirect the credential file into the test's own directory."""
    path = tmp_path / "BOOTSTRAP.txt"
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", str(path))
    return path


def load_bootstrap_config(tmp_path, username: str = "", password: str = "") -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "server:\n"
        "  bootstrap:\n"
        f'    owner_username: "{username}"\n'
        f'    owner_password: "{password}"\n',
        encoding="utf-8",
    )
    config.load_config(str(cfg), force=True)


# ---------------------------------------------------------------------------
# The trigger condition — the bypass guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_creates_owner_on_an_empty_database(session, tmp_path, bootstrap_file):
    load_bootstrap_config(tmp_path)
    created = await ensure_owner(session)

    assert created == "admin"
    owner = (await session.execute(select(User))).scalars().one()
    assert owner.role is Role.owner
    assert owner.is_active is True


@pytest.mark.asyncio
async def test_does_not_run_when_any_user_exists(session, tmp_path, bootstrap_file):
    """THE test. Bootstrap keys off an empty table, never off the env vars — otherwise
    a stale WEFT_BOOTSTRAP_PASSWORD in .env is a standing account-reset on every
    restart."""
    session.add(User(username="someone", password_hash="$argon2id$x", role=Role.user))
    await session.commit()

    load_bootstrap_config(tmp_path, username="attacker", password="a-very-long-password")
    created = await ensure_owner(session)

    assert created is None
    usernames = {u.username for u in (await session.execute(select(User))).scalars()}
    assert usernames == {"someone"}, "bootstrap must not add an account to a populated instance"


@pytest.mark.asyncio
async def test_does_not_run_even_when_the_only_user_is_a_plain_user(session, tmp_path, bootstrap_file):
    """An instance with users but no owner is a broken instance, not an empty one.
    Minting a fresh owner into it would be a privilege-escalation path: delete the
    owner row, restart, get a new owner with a password you chose."""
    session.add(User(username="bob", password_hash="$argon2id$x", role=Role.user))
    await session.commit()

    load_bootstrap_config(tmp_path, username="attacker", password="a-very-long-password")
    assert await ensure_owner(session) is None


@pytest.mark.asyncio
async def test_running_twice_creates_exactly_one_owner(session, tmp_path, bootstrap_file):
    load_bootstrap_config(tmp_path)
    await ensure_owner(session)
    await ensure_owner(session)

    assert len((await session.execute(select(User))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Credential handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_configured_credentials_are_used(session, tmp_path, bootstrap_file):
    load_bootstrap_config(tmp_path, username="dana", password="a-configured-password")
    await ensure_owner(session)

    owner = (await session.execute(select(User))).scalars().one()
    assert owner.username == "dana"
    assert verify_password("a-configured-password", owner.password_hash)


@pytest.mark.asyncio
async def test_password_is_generated_when_not_configured(session, tmp_path, bootstrap_file):
    load_bootstrap_config(tmp_path, username="dana")
    await ensure_owner(session)

    assert bootstrap_file.exists(), "a generated password must be delivered somewhere"
    content = bootstrap_file.read_text()
    assert "username: dana" in content

    password = next(
        line.split(": ", 1)[1] for line in content.splitlines() if line.startswith("password: ")
    )
    owner = (await session.execute(select(User))).scalars().one()
    assert verify_password(password, owner.password_hash), \
        "the password in the file must be the one that actually works"


@pytest.mark.asyncio
async def test_generated_credential_file_is_0600(session, tmp_path, bootstrap_file):
    """World-readable would be bad; group-readable would be bad. It is opened with the
    mode rather than chmod-ed afterwards, so there is no window where it is neither."""
    load_bootstrap_config(tmp_path)
    await ensure_owner(session)

    mode = stat.S_IMODE(os.stat(bootstrap_file).st_mode)
    assert mode == 0o600, f"credential file is mode {mode:o}, expected 600"


@pytest.mark.asyncio
async def test_configured_password_is_not_written_to_disk(session, tmp_path, bootstrap_file):
    """If the operator supplied the password they already have it. Writing it out again
    just creates a second copy to forget about."""
    load_bootstrap_config(tmp_path, username="dana", password="a-configured-password")
    await ensure_owner(session)

    assert not bootstrap_file.exists()


@pytest.mark.asyncio
async def test_owner_must_change_password_however_it_was_set(session, tmp_path, bootstrap_file):
    """Both paths. A configured password has sat in an env file and a generated one has
    sat in a log line — by this service's own standards both are already compromised."""
    load_bootstrap_config(tmp_path, username="dana", password="a-configured-password")
    await ensure_owner(session)

    owner = (await session.execute(select(User))).scalars().one()
    assert owner.must_change_password is True


@pytest.mark.asyncio
async def test_password_hash_is_stored_not_the_plaintext(session, tmp_path, bootstrap_file):
    load_bootstrap_config(tmp_path, username="dana", password="a-configured-password")
    await ensure_owner(session)

    owner = (await session.execute(select(User))).scalars().one()
    assert owner.password_hash.startswith("$argon2id$")
    assert "a-configured-password" not in owner.password_hash


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_returns_none_rather_than_raising(session, tmp_path, bootstrap_file, monkeypatch):
    """A failed bootstrap must not stop the service from starting — a process that is
    up can be diagnosed from /health, one that crash-loops cannot."""
    load_bootstrap_config(tmp_path)
    monkeypatch.setattr(
        bootstrap, "hash_password",
        lambda _p: (_ for _ in ()).throw(RuntimeError("hashing exploded")),
    )

    assert await ensure_owner(session) is None
    assert (await session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_unwritable_credential_file_still_creates_the_account(session, tmp_path, monkeypatch):
    """The password is logged as well as written. Losing the file is recoverable;
    failing to create the owner is not."""
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", "/proc/definitely/not/writable/x.txt")
    load_bootstrap_config(tmp_path)

    assert await ensure_owner(session) == "admin"
    assert len((await session.execute(select(User))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_users_exist_reflects_the_table(session):
    assert await users_exist(session) is False
    session.add(User(username="x", password_hash="h"))
    await session.commit()
    assert await users_exist(session) is True


def test_generated_passwords_are_long_and_unique():
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50, "generated passwords must not repeat"
    assert all(len(p) == 24 for p in passwords)


def test_generated_password_avoids_ambiguous_characters():
    """It gets read off a terminal and typed by hand at least once. 'Was that a one or
    an ell' is how people give up and choose something weak instead."""
    joined = "".join(generate_password() for _ in range(50))
    for ambiguous in "lIO01":
        assert ambiguous not in joined
