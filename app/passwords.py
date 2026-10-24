"""
Password hashing and policy.

argon2id via argon2-cffi, deliberately not through passlib: passlib's last release was
2020 and its bcrypt backend breaks against modern bcrypt, so depending on it means
inheriting a layer that is already broken rather than pinning around it.

**The dummy-hash dance is the security-relevant part of this module.** ``verify()``
against a real hash takes ~50ms of deliberate work. If the login handler skips that
work when the username does not exist, the response comes back in ~1ms instead, and the
difference is trivially measurable over a network. That turns the login endpoint into a
user-enumeration oracle: an attacker learns which accounts exist without ever guessing a
password. ``verify_or_dummy()`` exists so the handler cannot make that mistake — it
always does the work.
"""

import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app import config

logger = logging.getLogger(__name__)

#: RFC 9106's "second recommended" low-memory profile, which is argon2-cffi's default:
#: 64 MiB, 3 passes, 4 lanes. Chosen over the high-memory profile because this service
#: is expected to run on a homelab box where 64 MiB per concurrent login is already a
#: real consideration, and because the threat model here is an offline attack on a
#: stolen database rather than a nation-state.
#:
#: Raising these later is safe and automatic: needs_rehash() detects any stored hash
#: made with weaker parameters and the login path transparently upgrades it.
_hasher = PasswordHasher()

#: A real argon2id hash of a value nobody knows, computed once at import.
#:
#: Used to burn the same CPU time on a login for a non-existent user as for a real one.
#: It must be a genuine hash of the configured parameters, not a constant string —
#: verify() against a malformed hash fails fast and reintroduces the timing gap it is
#: here to close.
_DUMMY_HASH = _hasher.hash("weft-dummy-password-for-constant-time-login")


class PasswordPolicyError(ValueError):
    """Raised when a proposed password fails policy. The message is safe to show a
    user — it describes the rule, never anything about other accounts."""


def hash_password(password: str) -> str:
    """Hash a password for storage. Returns a full argon2id encoded string, which
    carries its own parameters and salt — there is no separate salt column by design."""
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a password against a stored hash. False on any failure, never raises.

    Swallowing InvalidHashError matters: a corrupt or truncated hash in the database
    must fail the login, not 500 the endpoint. A 500 here is itself an oracle — it
    tells an attacker that account exists and that its row is malformed.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        logger.error(
            "A stored password hash is malformed and could not be parsed. The affected "
            "account cannot log in until its password is reset."
        )
        return False
    except VerificationError:
        return False


def verify_or_dummy(password: str, stored_hash: str | None) -> bool:
    """Verify against ``stored_hash``, or burn equivalent time against a dummy and
    return False when there is no hash to check.

    Call this from the login handler instead of branching on "did we find a user".
    The whole point is that the two paths cost the same.
    """
    if stored_hash is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, stored_hash)


def needs_rehash(stored_hash: str) -> bool:
    """True when a stored hash was made with weaker parameters than we now use.

    The login handler is the only place that ever holds the plaintext, so it is the
    only place that can upgrade a hash — check this after a successful verify.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def validate_password(password: str, username: str | None = None) -> None:
    """Enforce password policy. Raises PasswordPolicyError with a message meant for the
    user.

    Length only, plus two obvious-footgun checks. Deliberately no character-class rules:
    NIST SP 800-63B dropped composition requirements because they push people toward
    "Password1!" and away from length, which is the property that actually matters.
    """
    minimum = int(config.get("security.password_min_length") or 12)

    if not password:
        raise PasswordPolicyError("Password must not be empty.")

    if len(password) < minimum:
        raise PasswordPolicyError(f"Password must be at least {minimum} characters.")

    # argon2 has no practical upper bound, but an unbounded password is an unbounded
    # amount of hashing work an unauthenticated caller can ask for. 1024 is far beyond
    # any real passphrase.
    if len(password) > 1024:
        raise PasswordPolicyError("Password must be at most 1024 characters.")

    if username and password.strip().lower() == username.strip().lower():
        raise PasswordPolicyError("Password must not be the same as the username.")
