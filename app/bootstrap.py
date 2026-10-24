"""
First-boot owner creation.

Registration is closed by default, so a fresh instance needs exactly one account to
exist before anyone can do anything — and creating it must not require a shell.

**The rule that makes this safe: bootstrap runs only when the users table is empty.**
Not "when the env vars are set", not "when a flag is passed". Once an owner exists the
env vars are inert, so leaving `WEFT_BOOTSTRAP_PASSWORD` in a .env file cannot silently
reset or re-create the owner account later. The service warns when it finds them still
set, because a password sitting in an env file is a password that leaks into
`docker inspect`, process listings, and backups.

Either way the owner lands with ``must_change_password`` set, so the bootstrap
credential is a single-use door, not a standing password.
"""

import logging
import os
import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import Role, User
from app.passwords import hash_password

logger = logging.getLogger(__name__)

#: Where a generated bootstrap password is written. Lives in the data volume beside the
#: database — which already holds every secret this service has — at mode 0600.
BOOTSTRAP_FILE = os.getenv("WEFT_BOOTSTRAP_FILE", "data/BOOTSTRAP.txt")

_DEFAULT_OWNER = "admin"

#: Unambiguous alphabet: no O/0, l/1/I. A generated password gets read off a terminal
#: and typed by hand at least once, and "was that a one or an ell" is how people end up
#: choosing a weak password instead.
_ALPHABET = (
    "".join(c for c in string.ascii_letters if c not in "lIO")
    + "".join(c for c in string.digits if c not in "01")
    + "!@#%^&*-_=+?"
)


def generate_password(length: int = 24) -> str:
    """A 24-character password from a ~68-character alphabet is ~146 bits. There is no
    scenario where this is the weak link."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def users_exist(session: AsyncSession) -> bool:
    result = await session.execute(select(func.count()).select_from(User))
    return (result.scalar() or 0) > 0


async def ensure_owner(session: AsyncSession) -> str | None:
    """Create the first owner if and only if no users exist.

    Returns the created username, or None if bootstrap was skipped. Never raises: a
    failure here must not stop the service from starting, because a service that is up
    can be diagnosed and one that is not cannot.
    """
    try:
        if await users_exist(session):
            _warn_if_bootstrap_vars_still_set()
            return None

        username = (config.get("bootstrap.owner_username") or "").strip() or _DEFAULT_OWNER
        password = config.get("bootstrap.owner_password") or ""
        generated = False

        if not password:
            password = generate_password()
            generated = True

        owner = User(
            username=username,
            display_name=username,
            password_hash=hash_password(password),
            role=Role.owner,
            is_active=True,
            # Always. Whether the operator chose the password or we generated it, it
            # has been in an env file or a log line, so it is already compromised by
            # the standards this service holds everything else to.
            must_change_password=True,
        )
        session.add(owner)
        await session.commit()

        if generated:
            _deliver_generated_password(username, password)
        else:
            logger.warning(
                "Bootstrap owner %r created from WEFT_BOOTSTRAP_OWNER/PASSWORD. "
                "It must change its password at first login. Clear those env vars now.",
                username,
            )

        return username

    except Exception as exc:
        # Deliberately broad: bootstrap failing is bad, but taking the service down
        # with it means nobody can read the reason.
        logger.error("Bootstrap failed: %s — no owner account was created.", exc)
        await session.rollback()
        return None


def _deliver_generated_password(username: str, password: str) -> None:
    """Write the generated password to a 0600 file and log it once.

    Both, on purpose. The log line is easy to miss under `docker compose up -d`; the
    file is easy to miss if you are watching logs. Neither is a good place for a
    secret, which is why the account cannot do anything until the password is changed.
    """
    written = False
    try:
        os.makedirs(os.path.dirname(BOOTSTRAP_FILE) or ".", exist_ok=True)
        # Open with 0600 from the start rather than chmod-ing afterwards — otherwise
        # the file briefly exists as world-readable, which is exactly long enough.
        fd = os.open(BOOTSTRAP_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                f"weft-service bootstrap credential\n"
                f"================================\n\n"
                f"username: {username}\n"
                f"password: {password}\n\n"
                f"This account must change its password at first login and can do\n"
                f"nothing else until it does.\n\n"
                f"DELETE THIS FILE once you have logged in.\n"
            )
        written = True
    except Exception as exc:
        logger.error("Could not write %s: %s", BOOTSTRAP_FILE, exc)

    logger.warning(
        "=" * 72 + "\n"
        "  FIRST BOOT: created owner account %r\n"
        "  password: %s\n"
        "  %s\n"
        "  This account must change its password at first login.\n"
        + "=" * 72,
        username,
        password,
        f"also written to {BOOTSTRAP_FILE} (delete it after logging in)"
        if written else "could not be written to disk — copy it from here NOW",
    )


def _warn_if_bootstrap_vars_still_set() -> None:
    """Bootstrap vars left in place after the owner exists do nothing, which is the
    problem: they look load-bearing, so nobody removes them, and a password sits in an
    env file forever."""
    if config.get("bootstrap.owner_password"):
        logger.warning(
            "WEFT_BOOTSTRAP_PASSWORD is still set but an owner already exists, so it "
            "has no effect. Clear it — it is a password sitting in an env file, "
            "visible to `docker inspect` and anything that reads your backups."
        )
