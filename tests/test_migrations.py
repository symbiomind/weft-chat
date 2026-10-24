"""
Migrations against a database that has something in it.

Every other test builds its schema with ``create_all``, so until 2026-10-05 no test
ever ran a migration over real rows. That is how the push-to-talk migration shipped:
it rebuilt the ``users`` table in batch mode while foreign keys were enforced, the
``DROP TABLE users`` inside the rebuild fired every ``ON DELETE CASCADE`` pointing at
it, and every provider and pick was deleted. The schema was perfect afterwards. The
data was gone.

These run the real ``alembic upgrade`` over a file database with a user, a provider
and a pick in it, and count what survives.
"""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine


def alembic_config(path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    cfg.attributes["configure_logger"] = False
    return cfg


def seed(path):
    """One user with one provider and one pick, as a real install has."""
    db = sqlite3.connect(path)
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, is_active, token_version,"
        " must_change_password, created_at, updated_at)"
        " VALUES ('u1', 'alice', 'x', 'owner', 1, 0, 0, '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO provider_secrets (id, user_id, name, kind, base_url,"
        " secret_ciphertext, secret_nonce, key_id, created_at, updated_at)"
        " VALUES ('p1', 'u1', 'bridge', 'llm', 'http://bridge.test/v1',"
        " x'00', x'00', 'k1', '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO selected_models (id, provider_id, model_id, created_at)"
        " VALUES ('m1', 'p1', 'bridge/iris', '2026-01-01')"
    )
    db.commit()
    db.close()


def counts(path) -> tuple[int, int, int]:
    """Users, providers, and picks — which are wefts from 2e93d99f1f9a on."""
    db = sqlite3.connect(path)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        picks = "wefts" if "wefts" in tables else "selected_models"
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("users", "provider_secrets", picks)
        )
    finally:
        db.close()


def test_rebuilding_users_keeps_every_provider_and_pick(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)

    # The revision before the push-to-talk columns, which rebuild `users`.
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    assert counts(path) == (1, 1, 1)

    command.upgrade(cfg, "head")

    assert counts(path) == (1, 1, 1)
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT stt_provider_id, stt_send_on_release, stt_tell_weft FROM users"
        ).fetchone() == (None, 0, 1)
    finally:
        db.close()


def test_downgrading_past_it_keeps_them_too(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "a8d3f1c6e2b9")

    assert counts(path) == (1, 1, 1)


@pytest.mark.parametrize("revision", ["head"])
def test_the_foreign_keys_still_hold_afterwards(tmp_path, revision):
    """Enforcement is off while migrating; the check afterwards is what makes that safe.
    A clean upgrade must leave nothing for it to find."""
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    command.upgrade(cfg, revision)

    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Picks become wefts (2e93d99f1f9a)
# ---------------------------------------------------------------------------

BEFORE_WEFTS = "c1f5b8d2e4a7"


def seed_picks(path):
    """Alice with three picks: two with the same model name on two providers, one of
    them kept in a session store with colours and a voice. Bob with one."""
    db = sqlite3.connect(path)
    db.executescript("""
        INSERT INTO users (id, username, password_hash, role, is_active, token_version,
            must_change_password, created_at, updated_at, stt_send_on_release, stt_tell_buddy)
        VALUES ('u1', 'alice', 'x', 'owner', 1, 0, 0, '2026-01-01', '2026-01-01', 0, 1),
               ('u2', 'bob', 'x', 'user', 1, 0, 0, '2026-01-01', '2026-01-01', 0, 1);
        INSERT INTO provider_secrets (id, user_id, name, kind, base_url, secret_ciphertext,
            secret_nonce, key_id, created_at, updated_at)
        VALUES ('p1aaaaaaaa', 'u1', 'bridge', 'llm', 'http://a.test/v1', x'00', x'00', 'k1',
                '2026-01-01', '2026-01-01'),
               ('p2bbbbbbbb', 'u1', 'cpu', 'llm', 'http://b.test/v1', x'00', x'00', 'k1',
                '2026-01-01', '2026-01-01'),
               ('s1', 'u1', 'store', 'session', 'http://s.test', x'00', x'00', 'k1',
                '2026-01-01', '2026-01-01'),
               ('p3cccccccc', 'u2', 'bridge', 'llm', 'http://c.test/v1', x'00', x'00', 'k1',
                '2026-01-01', '2026-01-01');
        INSERT INTO selected_models (id, provider_id, model_id, created_at, history,
            send_turns, window_turns, colour_a, colour_b, tts_voice, session_provider_id)
        VALUES ('m1', 'p1aaaaaaaa', 'bridge/iris', '2026-01-01', 'session', 1, 50,
                '#112233', '#445566', 'af_bella', 's1'),
               ('m2', 'p2bbbbbbbb', 'lfm2.5:8b', '2026-01-02', 'memory', 20, 200,
                NULL, NULL, NULL, NULL),
               ('m3', 'p1aaaaaaaa', 'other/lfm2.5:8b', '2026-01-03', 'memory', 20, 200,
                NULL, NULL, NULL, NULL),
               ('m4', 'p3cccccccc', 'bridge/iris', '2026-01-01', 'memory', 20, 200,
                NULL, NULL, NULL, NULL);
    """)
    db.commit()
    db.close()


def wefts(path) -> dict:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in db.execute("SELECT * FROM wefts")}
    finally:
        db.close()


def test_every_pick_becomes_a_weft_with_everything_it_had(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, BEFORE_WEFTS)
    seed_picks(path)

    command.upgrade(cfg, "head")

    rows = wefts(path)
    assert set(rows) == {"m1", "m2", "m3", "m4"}
    iris = rows["m1"]
    assert (iris["user_id"], iris["name"], iris["provider_id"], iris["model_id"]) == (
        "u1", "Iris", "p1aaaaaaaa", "bridge/iris",
    )
    assert (iris["history"], iris["send_turns"], iris["window_turns"]) == ("session", 1, 50)
    assert (iris["colour_a"], iris["colour_b"], iris["tts_voice"]) == (
        "#112233", "#445566", "af_bella",
    )
    assert iris["session_provider_id"] == "s1"
    assert rows["m4"]["user_id"] == "u2" and rows["m4"]["name"] == "Iris"


def test_a_name_one_user_already_has_gets_a_number(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, BEFORE_WEFTS)
    seed_picks(path)

    command.upgrade(cfg, "head")

    rows = wefts(path)
    assert (rows["m2"]["name"], rows["m3"]["name"]) == ("Lfm2.5:8b", "Lfm2.5:8b 2")
    assert rows["m3"]["name_key"] == "lfm2.5:8b 2"


def test_every_weft_keeps_the_session_id_it_already_had(tmp_path):
    """The thread a session store holds for a pick must still be its weft's thread."""
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, BEFORE_WEFTS)
    seed_picks(path)

    command.upgrade(cfg, "head")

    rows = wefts(path)
    assert rows["m1"]["auto_session_id"] == "weft.alice.p1aaaaaa.bridge.iris"
    assert rows["m2"]["auto_session_id"] == "weft.alice.p2bbbbbb.lfm2.5:8b"
    assert rows["m4"]["auto_session_id"] == "weft.bob.p3cccccc.bridge.iris"


def test_deleting_a_provider_after_the_migration_unbinds_rather_than_deletes(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, BEFORE_WEFTS)
    seed_picks(path)
    command.upgrade(cfg, "head")

    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM provider_secrets WHERE id = 'p1aaaaaaaa'")
        db.commit()
    finally:
        db.close()

    rows = wefts(path)
    assert set(rows) == {"m1", "m2", "m3", "m4"}
    assert rows["m1"]["provider_id"] is None and rows["m1"]["colour_a"] == "#112233"


def test_downgrading_keeps_the_bound_wefts_as_picks(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, BEFORE_WEFTS)
    seed_picks(path)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, BEFORE_WEFTS)

    assert counts(path) == (2, 4, 4)


# ---------------------------------------------------------------------------
# The MCP door's keys (d2a7c4e9f1b3)
# ---------------------------------------------------------------------------

def tables(path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def test_weft_keys_arrive_empty_and_touch_nothing_else(tmp_path):
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)

    command.upgrade(cfg, "d2a7c4e9f1b3")
    assert "weft_keys" in tables(path)
    assert counts(path) == (1, 1, 1)

    command.downgrade(cfg, "2e93d99f1f9a")
    assert "weft_keys" not in tables(path)
    assert counts(path) == (1, 1, 1)


def test_auth_sessions_are_backfilled_one_per_family_from_its_tokens(tmp_path):
    """Every family signed in before the table existed must get its row, or app/deps.py
    would sign every device out on the restart that adds it."""
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    command.upgrade(cfg, "d2a7c4e9f1b3")

    db = sqlite3.connect(path)
    rows = [
        # id, family, issued, ip, user agent, revoked at
        ("t1", "phone", "2026-10-01 08:00:00.000000", "10.0.0.5", "old phone", None),
        ("t2", "phone", "2026-10-02 09:00:00.000000", "10.0.0.6", "new phone", None),
        ("t3", "laptop", "2026-10-03 10:00:00.000000", "10.0.0.7", "laptop",
         "2026-10-04 11:00:00.000000"),
    ]
    for token_id, family, issued, ip, agent, revoked in rows:
        db.execute(
            "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, issued_at,"
            " expires_at, family_expires_at, ip, user_agent, revoked_at, revoked_reason)"
            " VALUES (?, 'u1', ?, ?, ?, '2026-11-01 00:00:00.000000',"
            " '2026-10-31 00:00:00.000000', ?, ?, ?, ?)",
            (token_id, family, token_id * 32, issued, ip, agent, revoked,
             "logout" if revoked else None),
        )
    db.commit()
    db.close()

    command.upgrade(cfg, "f3b8e1a6c2d4")

    db = sqlite3.connect(path)
    try:
        got = {
            r[0]: r[1:]
            for r in db.execute(
                "SELECT id, user_id, created_at, last_seen_at, ip, user_agent,"
                " revoked_at, revoked_reason FROM auth_sessions"
            )
        }
        assert db.execute("SELECT count(*) FROM refresh_tokens").fetchone()[0] == 3
    finally:
        db.close()

    assert got == {
        "phone": ("u1", "2026-10-01 08:00:00.000000", "2026-10-02 09:00:00.000000",
                  "10.0.0.6", "new phone", None, None),
        "laptop": ("u1", "2026-10-03 10:00:00.000000", "2026-10-03 10:00:00.000000",
                   "10.0.0.7", "laptop", "2026-10-04 11:00:00.000000", "logout"),
    }
    assert counts(path) == (1, 1, 1)

    command.downgrade(cfg, "d2a7c4e9f1b3")
    assert "auth_sessions" not in tables(path)


# ---------------------------------------------------------------------------
# Tip progress moves to the account (a4c9e2f7b1d5)
# ---------------------------------------------------------------------------

def test_tips_on_arrives_without_rebuilding_users(tmp_path):
    """A plain ADD COLUMN: a rebuild of `users` is what cost every provider once."""
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    command.upgrade(cfg, "f3b8e1a6c2d4")

    statements = []

    def record(conn, cursor, sql, *args):
        statements.append(sql)

    # On the Engine class: alembic builds its own engine from the config.
    event.listen(Engine, "before_cursor_execute", record)
    try:
        command.upgrade(cfg, "a4c9e2f7b1d5")
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert any("ALTER TABLE users ADD COLUMN tips_on" in s for s in statements)
    assert not any("DROP TABLE users" in s or "_alembic_tmp_users" in s for s in statements)
    assert counts(path) == (1, 1, 1)
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT tips_on FROM users").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM tip_marks").fetchone() == (0,)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# stt_tell_buddy becomes stt_tell_weft (ada7b0125884)
# ---------------------------------------------------------------------------

def test_tell_weft_is_renamed_in_place_and_keeps_every_value(tmp_path):
    """A plain RENAME COLUMN: a rebuild of `users` is what cost every provider once.
    A user who had switched it off must still have it off."""
    path = tmp_path / "weft.db"
    cfg = alembic_config(path)
    command.upgrade(cfg, "a8d3f1c6e2b9")
    seed(path)
    command.upgrade(cfg, "b6e1d3f8a2c7")
    db = sqlite3.connect(path)
    try:
        db.execute("UPDATE users SET stt_tell_buddy = 0")
        db.commit()
    finally:
        db.close()

    statements = []

    def record(conn, cursor, sql, *args):
        statements.append(sql)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        command.upgrade(cfg, "ada7b0125884")
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert any("RENAME COLUMN stt_tell_buddy TO stt_tell_weft" in s for s in statements)
    assert not any("DROP TABLE users" in s or "_alembic_tmp_users" in s for s in statements)
    assert counts(path) == (1, 1, 1)
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT stt_tell_weft FROM users").fetchone() == (0,)
    finally:
        db.close()

    command.downgrade(cfg, "b6e1d3f8a2c7")
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT stt_tell_buddy FROM users").fetchone() == (0,)
    finally:
        db.close()
    assert counts(path) == (1, 1, 1)
