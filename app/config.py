"""
Config loader for weft-service.

Parses ``config.yml`` into a single ``server:`` block, expands ``${ENV_VAR}`` once at
load, and exposes typed accessors with in-code defaults.

Two rules, both inherited from ind-bridge and both load-bearing:

**Fail loud, never fail to start.** A missing file, unparseable YAML, a missing block,
or a malformed secret degrades the *affected subsystem* and logs at ERROR. It never
raises, and ``/health`` always answers. A service that refuses to boot because of a
typo in an optional block is a service you cannot debug remotely at 2am.

**Defaults live in code, not in config.yml.** ``config.yml`` is an override sheet, not
a required manifest. The service runs with no config file at all — it just refuses the
routes whose secrets are missing.

The degradation matrix (each row is independent; none of them stops the container):

===========================  =======================================  ====================
missing                      effect                                   /health reports
===========================  =======================================  ====================
config.yml entirely          defaults; no allowed_origins beyond      config_loaded: false
                             public_origin
jwt.secret                   every auth route 503                     auth: unavailable
csrf.secret                  every state-changing route 503           csrf: unavailable
vault.key (or malformed)     /api/providers/* 503 only                vault: unavailable
migrations failed            every non-/health route 503              db: migration_failed
===========================  =======================================  ====================

Secrets are read from the environment via ``${VAR}`` expansion. Nothing in this module
ever logs a secret value — only whether one is present and whether it is the right
length. ``tests/test_no_secret_logging.py`` enforces that.
"""

import base64
import binascii
import logging
import os
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.yml")

#: Required length, in bytes, of every secret this service generates instructions for.
#: 32 bytes = 256 bits. Anything shorter is rejected rather than silently accepted,
#: because a short key is the failure mode that looks exactly like success.
SECRET_BYTES = 32

_SERVER_CFG: dict = {}
_config_loaded: bool = False

#: Defaults, applied when config.yml is absent or a key is missing. Structure mirrors
#: config.example.yml exactly — if you add a key there, add it here.
DEFAULTS: dict[str, Any] = {
    "name": "weft-service",
    "port": 5010,
    "public_origin": "http://localhost:5010",
    "database": {
        "url": "sqlite+aiosqlite:///./data/weft.db",
    },
    "jwt": {
        "secret": "",
        "algorithm": "HS256",
        "issuer": "weft-service",
        "audience": "weft-web",
        "leeway_seconds": 10,
    },
    "csrf": {
        "secret": "",
        "ttl_seconds": 86400,
    },
    "vault": {
        "key": "",
        "key_id": "k1",
        "old_keys": "",
    },
    "security": {
        "access_token_ttl_seconds": 900,
        "refresh_token_ttl_days": 30,
        "session_absolute_ttl_days": 30,
        # How long a signed-out or expired device's rows are kept before the housekeeping
        # sweep deletes them. Only the reuse warning in the log needs them by then.
        "session_purge_grace_days": 7,
        "cookie_secure": True,
        "cookie_prefix_host": True,
        "cookie_samesite": "strict",
        "allowed_origins": [],
        "login_max_attempts": 5,
        "login_lockout_seconds": 900,
        "login_ip_window_seconds": 300,
        "login_ip_max": 20,
        "invite_ttl_hours": 168,
        "password_min_length": 12,
        "hsts": True,
        "hsts_include_subdomains": False,
        "expose_docs": False,
    },
    "websocket": {
        "auth_deadline_seconds": 5,
        "heartbeat_seconds": 30,
        "max_frame_bytes": 65536,
        "max_messages_per_second": 20,
        "max_connections_per_user": 8,
        "sweep_interval_seconds": 60,
    },
    "speech": {
        # Spoken clips are kept this long since last heard, so a reply can be replayed
        #. 0 turns the cache off. See app/speech_cache.py.
        "cache_ttl_hours": 168,
    },
    "bootstrap": {
        "owner_username": "",
        "owner_password": "",
    },
}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_config(path: str | None = None, force: bool = False) -> None:
    """Parse ``config.yml`` and populate module state. Called once at startup.

    Never raises. Every failure mode degrades to "defaults, with a loud log line".

    Idempotent by default: three separate call sites legitimately want the config
    loaded (``__main__`` needs the port, ``create_app`` needs expose_docs before
    lifespan runs, and lifespan itself), and re-parsing for each of them would repeat
    every warning three times — which trains the operator to skim past exactly the
    lines that matter. Tests pass ``force=True`` to load a different file.
    """
    global _SERVER_CFG, _config_loaded

    if _config_loaded and not force:
        return

    cfg_path = path or CONFIG_PATH
    _SERVER_CFG = {}
    _config_loaded = False

    if not os.path.exists(cfg_path):
        logger.warning(
            "No config file at %s — running on built-in defaults. "
            "Secrets come from the environment, so auth may still work; "
            "copy config.example.yml to config.yml to set origins and policy.",
            cfg_path,
        )
        _warn_on_insecure_flags()
        return

    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except Exception as exc:
        logger.error("Failed to parse config file %s: %s — using defaults.", cfg_path, exc)
        _hint_tab_indentation(cfg_path)
        return

    if not raw:
        logger.warning("Config file %s is empty — using defaults.", cfg_path)
        return

    try:
        raw = _expand_env_vars(raw)
        server = raw.get("server")
        if not isinstance(server, dict) or not server:
            logger.warning(
                "Config file %s has no 'server:' block — using defaults. "
                "Every key in this file lives under a single top-level 'server:'.",
                cfg_path,
            )
            return

        _SERVER_CFG = server
        _config_loaded = True
        logger.info("Config loaded from %s.", cfg_path)
    except Exception as exc:
        logger.error("Config structure error in %s: %s — using defaults.", cfg_path, exc)
        return

    _warn_on_insecure_flags()
    _warn_on_unknown_keys(server)


def is_config_loaded() -> bool:
    """True iff a valid ``config.yml`` was parsed. False means "running on defaults"."""
    return _config_loaded


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get(dotted: str, default: Any = None) -> Any:
    """Look up ``"security.cookie_secure"`` against config, then DEFAULTS, then
    ``default``.

    Falling through to DEFAULTS per-key (rather than deep-merging once at load) means a
    partial config.yml — one that sets only ``security.allowed_origins``, say — keeps
    every other default instead of blanking its siblings.
    """
    parts = dotted.split(".")

    found, value = _walk(_SERVER_CFG, parts)
    if found and value is not None:
        return value

    found, value = _walk(DEFAULTS, parts)
    if found and value is not None:
        return value

    return default


def _walk(tree: Any, parts: list[str]) -> tuple[bool, Any]:
    """Return ``(found, value)`` for a dotted path. ``found`` distinguishes a key that
    is absent from one explicitly set to a falsy value (``expose_docs: false`` must not
    fall through to the default)."""
    node = tree
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def database_url() -> str:
    return str(get("database.url"))


def public_origin() -> str:
    return str(get("public_origin")).rstrip("/")


def allowed_origins() -> list[str]:
    """Origins accepted on state-changing requests and the WebSocket handshake.

    ``public_origin`` is always included — forgetting to list your own origin should
    not lock you out of your own service.
    """
    configured = get("security.allowed_origins") or []
    if not isinstance(configured, list):
        logger.error(
            "security.allowed_origins must be a list of origin strings, got %s — "
            "ignoring it and allowing only public_origin.",
            type(configured).__name__,
        )
        configured = []

    origins = {str(o).rstrip("/") for o in configured if str(o).strip()}
    origins.add(public_origin())
    return sorted(origins)


# ---------------------------------------------------------------------------
# Secrets — presence and shape only. Values never leave this module.
# ---------------------------------------------------------------------------

def jwt_secret() -> str:
    return str(get("jwt.secret") or "")


def csrf_secret() -> str:
    return str(get("csrf.secret") or "")


def vault_key() -> bytes | None:
    """Decode the vault key, or None if absent/malformed.

    Returning None rather than raising is what keeps a bad vault key from taking down
    login: only ``/api/providers/*`` degrades.
    """
    raw = str(get("vault.key") or "")
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(_pad_b64(raw))
    except (binascii.Error, ValueError) as exc:
        logger.error(
            "vault.key is not valid base64 (%s) — the provider vault is UNAVAILABLE. "
            "Generate one with: "
            'python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"',
            exc,
        )
        return None

    if len(key) != SECRET_BYTES:
        logger.error(
            "vault.key decodes to %d bytes, expected %d — the provider vault is "
            "UNAVAILABLE. A short key is not a weak key here, it is a wrong key.",
            len(key), SECRET_BYTES,
        )
        return None
    return key


def vault_old_keys() -> dict[str, bytes]:
    """Decrypt-only keys retained across a rotation, parsed from
    ``"key_id:base64,key_id:base64"``. Malformed entries are skipped with a warning
    rather than failing the lot — one bad entry should not strand every other row."""
    raw = str(get("vault.old_keys") or "").strip()
    if not raw:
        return {}

    out: dict[str, bytes] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key_id, _, b64 = entry.partition(":")
        if not key_id or not b64:
            logger.warning("vault.old_keys entry %r is not 'key_id:base64' — skipped.", key_id)
            continue
        try:
            decoded = base64.urlsafe_b64decode(_pad_b64(b64))
        except (binascii.Error, ValueError):
            logger.warning("vault.old_keys entry %r is not valid base64 — skipped.", key_id)
            continue
        if len(decoded) != SECRET_BYTES:
            logger.warning(
                "vault.old_keys entry %r decodes to %d bytes, expected %d — skipped.",
                key_id, len(decoded), SECRET_BYTES,
            )
            continue
        out[key_id] = decoded
    return out


def auth_available() -> bool:
    """False disables every auth route with a 503. Static hosting still works."""
    return bool(jwt_secret())


def csrf_available() -> bool:
    return bool(csrf_secret())


def vault_available() -> bool:
    return vault_key() is not None


def degradation_report() -> dict[str, Any]:
    """The subsystem status block served by ``/health``. Deliberately coarse: it says
    what is unavailable, never why in a way that helps an unauthenticated caller."""
    return {
        "config_loaded": is_config_loaded(),
        "auth": "ok" if auth_available() else "unavailable",
        "csrf": "ok" if csrf_available() else "unavailable",
        "vault": "ok" if vault_available() else "unavailable",
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` in string values. A missing var becomes empty —
    which is exactly what makes the degradation matrix above work: an unset
    WEFT_VAULT_KEY yields "" and turns off one subsystem, rather than leaving the
    literal string "${WEFT_VAULT_KEY}" to be used as an encryption key."""
    if isinstance(obj, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(i) for i in obj]
    return obj


def _pad_b64(value: str) -> str:
    """Restore stripped base64 padding. Generators vary on whether they emit '='."""
    value = value.strip()
    return value + "=" * (-len(value) % 4)


def _warn_on_insecure_flags() -> None:
    """Say it out loud, every boot, when the cookie flags are off.

    These are the two flags most likely to be flipped "just for local dev" and then
    ride into production in a copied config.yml.
    """
    if not get("security.cookie_secure"):
        logger.warning(
            "security.cookie_secure is FALSE — refresh cookies will be sent over "
            "plain HTTP. Acceptable for local dev over http://localhost, never "
            "anywhere reachable by anyone else."
        )
    if not get("security.cookie_prefix_host"):
        logger.warning(
            "security.cookie_prefix_host is FALSE — the __Host- cookie prefix is "
            "disabled, so a sibling subdomain could overwrite the refresh cookie."
        )
    # RFC 7518 §3.2: an HMAC key should be at least as long as the hash output, so 32
    # bytes for SHA-256. A shorter key does not make signing fail — it just makes the
    # signature cheaper to brute-force, silently. .env.example generates 32 bytes; this
    # catches the operator who pasted a memorable string instead.
    for label, value in (("jwt.secret", jwt_secret()), ("csrf.secret", csrf_secret())):
        if value and len(value.encode("utf-8")) < SECRET_BYTES:
            logger.warning(
                "%s is only %d bytes; %d or more is recommended (RFC 7518 §3.2). "
                "Signing still works, the signature is just weaker than it looks. "
                'Generate one with: python3 -c "import os,base64;'
                'print(base64.urlsafe_b64encode(os.urandom(32)).decode())"',
                label, len(value.encode("utf-8")), SECRET_BYTES,
            )

    if str(get("security.cookie_samesite")).lower() not in ("strict", "lax"):
        logger.warning(
            "security.cookie_samesite is %r — expected 'strict' or 'lax'. "
            "'none' would disable the browser's own CSRF protection entirely.",
            get("security.cookie_samesite"),
        )


def _warn_on_unknown_keys(server: dict) -> None:
    """Name top-level keys that no accessor reads.

    A typo'd key in a config file is silent by nature: the service uses its default and
    behaves subtly differently from what the file says. Saying so at boot turns a
    mystery into a one-line fix.
    """
    unknown = sorted(set(server) - set(DEFAULTS))
    if unknown:
        logger.warning(
            "Unknown key(s) under 'server:' — ignored, and likely a typo: %s. "
            "Known keys: %s",
            ", ".join(unknown), ", ".join(sorted(DEFAULTS)),
        )


def _hint_tab_indentation(cfg_path: str) -> None:
    """Name the most likely cause of a YAML parse failure: a literal tab.

    YAML forbids tabs as indentation, and the raw parser message ("found character
    '\\t' that cannot start any token") is accurate and useless if you don't already
    know that. Sniff the file rather than trusting the exception string.
    """
    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except Exception:
        return

    offenders = [n for n, line in enumerate(text.splitlines(), start=1) if line.startswith("\t")]
    if offenders:
        logger.error(
            "HINT: config.yml is indented with TABS on line(s) %s. YAML requires "
            "spaces — a single tab fails the whole file.",
            ", ".join(str(n) for n in offenders[:5]),
        )
