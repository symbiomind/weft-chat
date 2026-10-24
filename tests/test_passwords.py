"""
Tests for app/passwords.py.

**Why this file matters.** Two properties here are worth more than the rest combined.

1. **verify_or_dummy must burn the same time whether or not the account exists.** If it
   returns early for an unknown username, the login endpoint becomes a user-enumeration
   oracle: an attacker times responses and learns which accounts are real without ever
   guessing a password. `test_dummy_verify_is_not_fast` is the regression guard, and it
   is deliberately written to catch the *shape* of the bug (returning in microseconds)
   rather than to police exact timings.

2. **A malformed stored hash must fail closed, not raise.** A 500 from the login route
   is itself an oracle — it distinguishes "this account exists but its row is corrupt"
   from "no such account".
"""

import time

import pytest

from app import config
from app.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_or_dummy,
    verify_password,
)


# ---------------------------------------------------------------------------
# Hashing round trip
# ---------------------------------------------------------------------------

def test_hash_and_verify_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True


def test_wrong_password_is_rejected():
    stored = hash_password("correct horse battery staple")
    assert verify_password("Correct horse battery staple", stored) is False


def test_hash_is_argon2id():
    """Not argon2i, not argon2d, not bcrypt. argon2id is the variant with both
    side-channel and GPU resistance, and it is what the threat model assumes."""
    assert hash_password("x").startswith("$argon2id$")


def test_same_password_hashes_differently_each_time():
    """Per-hash salt. Identical hashes for identical passwords would let anyone with
    the database see which users share a password."""
    a = hash_password("same-password-both-times")
    b = hash_password("same-password-both-times")
    assert a != b
    assert verify_password("same-password-both-times", a)
    assert verify_password("same-password-both-times", b)


def test_hash_carries_its_own_parameters():
    """The encoded string holds algorithm, version, memory, time and parallelism —
    which is why there is no separate salt or params column, and why raising the cost
    later does not invalidate existing hashes."""
    stored = hash_password("x")
    assert stored.count("$") >= 4
    assert "m=" in stored and "t=" in stored and "p=" in stored


def test_unicode_password_round_trips():
    """People use emoji and non-Latin scripts in passphrases. A naive encoding step
    would break login for them and nobody else, which is the hardest kind of bug to
    hear about."""
    secret = "🦞 хорошая лошадь 電池 ホッチキス"
    assert verify_password(secret, hash_password(secret)) is True


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------

def test_malformed_hash_returns_false_and_does_not_raise():
    assert verify_password("anything", "not-a-hash") is False


def test_empty_stored_hash_returns_false():
    assert verify_password("anything", "") is False


def test_truncated_hash_returns_false():
    stored = hash_password("x")
    assert verify_password("x", stored[: len(stored) // 2]) is False


# ---------------------------------------------------------------------------
# The enumeration-oracle guard
# ---------------------------------------------------------------------------

def test_verify_or_dummy_returns_false_for_missing_hash():
    assert verify_or_dummy("anything", None) is False


def test_verify_or_dummy_still_verifies_a_real_hash():
    stored = hash_password("hunter2-but-longer")
    assert verify_or_dummy("hunter2-but-longer", stored) is True
    assert verify_or_dummy("wrong", stored) is False


def test_dummy_verify_is_not_fast():
    """The regression this guards: someone 'optimises' verify_or_dummy to return early
    when stored_hash is None, and login starts answering unknown usernames in
    microseconds while real ones take ~50ms.

    Asserted as a floor in absolute terms rather than a ratio against a real verify,
    because ratios are flaky under CI scheduling noise while "did it do any argon2 work
    at all" is unambiguous — an early return is ~1000x under this bound.
    """
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        verify_or_dummy("anything", None)
        samples.append(time.perf_counter() - start)

    assert min(samples) > 0.005, (
        f"verify_or_dummy(None) returned in {min(samples)*1000:.3f}ms — too fast to "
        "have hashed anything. The unknown-user path must do the same work as the "
        "known-user path or login leaks which accounts exist."
    )


def test_dummy_and_real_verify_are_the_same_order_of_magnitude():
    """Complements the floor above: the two paths should cost comparably, not merely
    both be slow. Bounds are wide because this runs on shared CI."""
    stored = hash_password("some-real-password")

    def timed(fn) -> float:
        return min(_time_once(fn) for _ in range(3))

    dummy = timed(lambda: verify_or_dummy("anything", None))
    real = timed(lambda: verify_or_dummy("wrong-password", stored))

    ratio = max(dummy, real) / min(dummy, real)
    assert ratio < 5, f"timing paths differ by {ratio:.1f}x — that is a measurable oracle"


def _time_once(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


# ---------------------------------------------------------------------------
# Rehashing
# ---------------------------------------------------------------------------

def test_fresh_hash_does_not_need_rehash():
    assert needs_rehash(hash_password("x")) is False


def test_weaker_legacy_hash_needs_rehash():
    """A hash made with lower cost parameters must be detected so the login path can
    transparently upgrade it. Without this, raising the cost only protects accounts
    created after the change."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("x")
    assert needs_rehash(weak) is True


def test_needs_rehash_on_garbage_is_false_not_an_exception():
    """Called right after a successful verify; it must never be the thing that raises."""
    assert needs_rehash("not-a-hash") is False


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def test_valid_password_passes():
    validate_password("a-perfectly-fine-passphrase")


def test_empty_password_is_rejected():
    with pytest.raises(PasswordPolicyError):
        validate_password("")


def test_short_password_is_rejected():
    with pytest.raises(PasswordPolicyError, match="at least 12"):
        validate_password("short")


def test_minimum_length_comes_from_config(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("server:\n  security:\n    password_min_length: 20\n", encoding="utf-8")
    config.load_config(str(path), force=True)

    with pytest.raises(PasswordPolicyError, match="at least 20"):
        validate_password("nineteen-chars-x")
    validate_password("this-one-is-comfortably-long-enough")


def test_absurdly_long_password_is_rejected():
    """An unbounded password is an unbounded amount of argon2 work an unauthenticated
    caller can request — a free denial-of-service lever."""
    with pytest.raises(PasswordPolicyError, match="at most"):
        validate_password("x" * 5000)


def test_password_equal_to_username_is_rejected():
    with pytest.raises(PasswordPolicyError):
        validate_password("averylongusername", username="averylongusername")


def test_password_equal_to_username_is_case_insensitive():
    with pytest.raises(PasswordPolicyError):
        validate_password("AVeryLongUsername", username="averylongusername")


def test_no_character_class_requirements():
    """Deliberate: NIST SP 800-63B dropped composition rules because they push people
    toward 'Password1!' and away from length. A long all-lowercase passphrase is fine
    and this test exists so nobody 'fixes' that later."""
    validate_password("all lowercase words and spaces only")
