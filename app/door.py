"""
Weft's MCP door: an agent shows its user a picture.

A weft's agent can make a picture (a screenshot, a chart, a photo it found) on a
machine Weft cannot see. This door is how the picture gets into Weft. The agent adds
Weft's MCP server to its config with a key minted for one weft, and gains four tools:

* ``upload`` hands back a one-time address. The agent sends the file there with its
  shell (``curl -T``), and the answer is the line to write in its reply, which the chat
  window shows as the picture (web/js/references.js).
* ``view`` gives a stored picture back to the agent as an image, so an agent that can
  only see pictures in tool results can still look at what it showed.
* ``notify`` leaves the user a report to read when they next open Weft:
  the way an agent working in the background keeps its user posted.
  See app/notifications.py.
* ``status`` says which weft the key opens, so an agent can check its own door, and
  how many of its notifications are still waiting.

**Why upload is two steps.** A tool's arguments are written by the model, token by
token. A picture passed as base64 in an argument would cost the agent its size in
output tokens: a 200KB screenshot is roughly 70,000 of them, slow, expensive and easy
to garble. So the tool call carries only a name, and the bytes go over plain HTTP from
the agent's shell, never through the model.

**Ownership.** A key belongs to one weft, and a weft to one user. The picture lands in
that user's attachment folder through the same re-encode as a 📎 upload
(app/attachments.py), so the folder is still the whole ownership check: the key can
only ever add to, and read back from, its own user's pictures.

**Tickets and limits live in this process.** The service runs one worker (see
app/server.py), so an in-memory table is the whole truth. A restart forgets every open
ticket, which costs an agent one more ``upload`` call. Limits are per weft, so a looping
agent can fill only so much disk before somebody notices.
"""

import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app import attachments, config, db, notifications, push
from app.models import User, Weft, WeftKey, utcnow
from app.refresh import hash_token

logger = logging.getLogger(__name__)

#: Where the door is served. One exact path: see ``mount``.
PATH = "/api/mcp"

#: In front of every key, so a key pasted somewhere it should not be is recognisable.
KEY_PREFIX = "weft_"

#: How long an upload address stays good. Long enough for a slow agent to run a curl,
#: short enough that one left in a log is dead by the time anyone reads it.
TICKET_SECONDS = 10 * 60

#: Upload addresses one weft may have open at once.
MAX_OPEN_TICKETS = 6

#: Upload addresses one weft may ask for in an hour.
TICKETS_PER_HOUR = 60

_HASH_ANYWHERE = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def mint_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def mcp_url() -> str:
    return config.public_origin() + PATH


@dataclass(frozen=True)
class Caller:
    """Who came through the door: the weft the key belongs to, and its user."""

    user_id: str
    weft_id: str
    weft_name: str
    hint: str
    created_at: datetime


async def resolve_key(session, raw: str | None) -> Caller | None:
    """The caller a key opens the door for, or None. Notes the key as used."""
    if not raw or not raw.startswith(KEY_PREFIX):
        return None
    row = (
        await session.execute(
            select(WeftKey, Weft.name, Weft.user_id)
            .join(Weft, Weft.id == WeftKey.weft_id)
            .join(User, User.id == Weft.user_id)
            .where(WeftKey.token_hash == hash_token(raw), User.is_active.is_(True))
        )
    ).one_or_none()
    if row is None:
        return None
    key, weft_name, user_id = row
    key.last_used_at = utcnow()
    await session.commit()
    return Caller(
        user_id=user_id,
        weft_id=key.weft_id,
        weft_name=weft_name,
        hint=key.hint,
        created_at=key.created_at,
    )


# ---------------------------------------------------------------------------
# Upload tickets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ticket:
    user_id: str
    weft_id: str
    name: str
    expires: float


class DoorBusy(Exception):
    """A weft asked for more upload addresses than its limits allow."""


#: Open tickets, by the digest of the address's secret part.
_tickets: dict[str, Ticket] = {}

#: When each weft asked for its recent tickets, oldest first.
_asked: dict[str, deque[float]] = {}


def _prune(now: float) -> None:
    for digest in [d for d, t in _tickets.items() if t.expires <= now]:
        del _tickets[digest]


def open_ticket(caller: Caller, name: str | None) -> str:
    """A one-time upload secret for this caller's weft. Raises DoorBusy past a limit."""
    now = time.monotonic()
    _prune(now)

    asked = _asked.setdefault(caller.weft_id, deque())
    while asked and asked[0] <= now - 3600:
        asked.popleft()
    if len(asked) >= TICKETS_PER_HOUR:
        raise DoorBusy(f"This weft has asked for {TICKETS_PER_HOUR} uploads in the last hour.")
    if sum(t.weft_id == caller.weft_id for t in _tickets.values()) >= MAX_OPEN_TICKETS:
        raise DoorBusy(
            f"This weft already has {MAX_OPEN_TICKETS} upload addresses waiting. "
            "Use one, or wait ten minutes for them to lapse."
        )

    raw = secrets.token_urlsafe(32)
    _tickets[hash_token(raw)] = Ticket(
        user_id=caller.user_id,
        weft_id=caller.weft_id,
        name=attachments.clean_name(name),
        expires=now + TICKET_SECONDS,
    )
    asked.append(now)
    return raw


def take_ticket(raw: str) -> Ticket | None:
    """The ticket for an upload secret, used up by this call, or None."""
    ticket = _tickets.pop(hash_token(raw), None)
    if ticket is None or ticket.expires <= time.monotonic():
        return None
    return ticket


def forget(weft_ids: set[str]) -> None:
    """Close every open upload address and invite of these wefts. For when their key
    goes or is replaced: nothing handed out under the old arrangement outlives it."""
    for table in (_tickets, _invites):
        for digest in [d for d, t in table.items() if t.weft_id in weft_ids]:
            del table[digest]


def upload_url(raw: str) -> str:
    return f"{config.public_origin()}/api/door/upload/{raw}"


def reference(digest: str, name: str) -> str:
    """The line an agent writes in its reply to show a picture."""
    return f"![{name}]({config.public_origin()}/api/attachments/{digest})"


# ---------------------------------------------------------------------------
# Invites: the key, delivered without passing through the chat
# ---------------------------------------------------------------------------
#
# The user wants their weft's agent connected, and the agent is reached through the
# chat. But everything said in the chat is kept: in the session store, in the agent's
# memory, in logs. A key said there is a key kept everywhere. So the chat carries an
# invite instead: a one-time address of a page that mints the key and explains what to
# do with it. The first read uses it up, so what the chat keeps is a dead link.

#: How long an invite waits to be read. Long enough to send it and for the agent to get
#: round to it; short enough that one never read is not a standing way in.
INVITE_SECONDS = 15 * 60


@dataclass(frozen=True)
class Invite:
    user_id: str
    weft_id: str
    expires: float


#: Open invites, by the digest of the address's secret part. One per weft at most.
_invites: dict[str, Invite] = {}


def open_invite(user_id: str, weft_id: str) -> str:
    """A one-time invite secret for this weft, replacing any it had waiting."""
    now = time.monotonic()
    for digest in [d for d, i in _invites.items() if i.weft_id == weft_id or i.expires <= now]:
        del _invites[digest]
    raw = secrets.token_urlsafe(32)
    _invites[hash_token(raw)] = Invite(user_id=user_id, weft_id=weft_id,
                                       expires=now + INVITE_SECONDS)
    return raw


def take_invite(raw: str) -> Invite | None:
    """The invite for a secret, used up by this call, or None."""
    invite = _invites.pop(hash_token(raw), None)
    if invite is None or invite.expires <= time.monotonic():
        return None
    return invite


def invite_url(raw: str) -> str:
    return f"{config.public_origin()}/api/door/invite/{raw}"


def guide(key: str, weft_name: str) -> str:
    """What an agent needs to connect, and to use the door well. Markdown, for a model
    to read: the invite page serves it, and the manual route hands it to the user."""
    url = mcp_url()
    return f"""# Connect to Weft

Weft is the chat window your user reads you in. This connects you to Weft's MCP server,
which lets you show your user pictures in your replies, and leave them notifications to
read when they are away. You are connecting as the weft "{weft_name}".

## Your connection

- MCP server, streamable HTTP: {url}
- Header: `Authorization: Bearer {key}`

**Keep the key secret.** Put it in your MCP client's configuration and nowhere else.
Never write it in a chat reply, a memory, a note or a log: everything said in the chat
is kept. If it leaks, your user can replace it on the weft's page in Weft.

## Add the server

Add it however your harness adds a remote MCP server over streamable HTTP. For example:

Claude Code:

    claude mcp add --transport http weft {url} --header "Authorization: Bearer {key}"

Hermes Agent, in `~/.hermes/config.yaml`:

    mcp_servers:
      weft:
        url: "{url}"
        headers:
          Authorization: "Bearer {key}"

Many harnesses load MCP servers only at startup, so you may need a restart, or to ask
your user for one. Then call the `status` tool: it names the weft "{weft_name}".

## Showing a picture

1. Call `upload` with a caption. It gives you a one-time address.
2. Send the file there from your shell: `curl -sS -T <file> '<address>'`
3. The answer is JSON. Write its `reference` line in your reply exactly as given, on
   its own line and not inside a code block. Weft shows the picture there.

JPEG, PNG, WebP and GIF are accepted, up to 20 MB. An address works once, for ten
minutes. Your conversation keeps the reference line, not the picture, so to look at one
again later call `view` with its reference.

If your shell cannot reach the address (some sandboxes block local network addresses),
tell your user rather than guessing at another way in.

## Keeping your user posted

When your user sets you a long job and leaves you to it, call `notify` to report in: a
short `title`, and a `body` saying what happened and anything you need from them. It
waits in Weft until they open it, and a bell on your name tells them it is there. The
answer says it was delivered, so there is no need to send it again.

Send what your user would want to read when they get back: a job finished, a question
only they can answer, something gone wrong. Not every step. A weft can send
{notifications.PER_HOUR} an hour, and only the newest {notifications.MAX_WAITING} wait,
so many small reports bury each other. A picture shows too: put its `reference` line
from `upload` in the body. `status` says how many of yours are still waiting, which is
how you can tell whether they have been read.

## When you are done

Tell your user whether it worked, without repeating the key. This page has done its job:
it worked once, and its address is dead now.
"""


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------

def _caller(ctx) -> Caller:
    return ctx.request_context.request.state.weft_caller


def build_server():
    """The MCP server, with its four tools. One per app: its session manager runs
    once, in the app's lifespan."""
    from mcp.server.fastmcp import Context, FastMCP, Image
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings

    server = FastMCP(
        "weft",
        instructions=(
            "Weft is the chat window your user reads you in. Use upload to show them "
            "a picture: it gives you an address to send the file to with curl, and the "
            "line to write in your reply. Use view to look at a picture you showed. "
            "Use notify to leave your user a report to read when they are away, such as "
            "when a long job you were left to do finishes."
        ),
        streamable_http_path=PATH,
        # No sessions to keep: every call stands alone, and a restart loses nothing.
        stateless_http=True,
        json_response=True,
        # FastMCP's rebinding guard allows only localhost Host headers, which would
        # refuse every real caller with a 421. The key gate in front is the boundary,
        # and a rebinding attacker driving a browser cannot supply the key. ind-bridge's
        # app/mcp_serve.py makes the same call at more length.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @server.tool(structured_output=False)
    def upload(ctx: Context, name: str = "picture") -> str:
        """Get a one-time address to upload a picture to, so it shows in your reply.

        Send the file's bytes to the address with an HTTP PUT within ten minutes, for
        example: curl -sS -T screenshot.png '<address>'. The answer is JSON whose
        "reference" is a line like ![name](https://.../api/attachments/<hash>). Write
        that line in your reply, exactly, and the user sees the picture there. JPEG,
        PNG, WebP and GIF are accepted, up to 20 MB.

        name: what the picture is called, shown as its caption.
        """
        try:
            raw = open_ticket(_caller(ctx), name)
        except DoorBusy as exc:
            raise ToolError(str(exc)) from exc
        address = upload_url(raw)
        return (
            f"Upload address (one use, ten minutes): {address}\n"
            f"Send the file: curl -sS -T <file> '{address}'\n"
            'The answer\'s "reference" is the line to write in your reply.'
        )

    @server.tool(structured_output=False)
    def view(ctx: Context, reference: str) -> Image:
        """Look at a picture you uploaded, as an image.

        reference: the line upload gave you, its address, or just its hash.
        """
        match = _HASH_ANYWHERE.search(reference or "")
        path = attachments.path_for(_caller(ctx).user_id, match.group(0)) if match else None
        if path is None or not path.is_file():
            raise ToolError("No such picture for this weft's user.")
        return Image(data=path.read_bytes(), format="webp")

    @server.tool(structured_output=False)
    async def notify(ctx: Context, title: str, body: str = "") -> str:
        """Leave your user a notification: a report they read when they next open Weft.

        For keeping them posted on work they left you to do: a job finished, a question
        only they can answer, something gone wrong. It waits until they dismiss it, with
        a bell on your name to say it is there. Send what is worth reading later, not
        every step: a weft can send only so many an hour.

        title: one line saying what this is about, up to 120 characters.
        body: the report, as plain text, up to 4000 characters. A picture's reference
            line from upload, on a line of its own, shows as the picture.
        """
        caller = _caller(ctx)
        async with db.get_sessionmaker()() as session:
            try:
                note = await notifications.send(
                    session, user_id=caller.user_id, weft_id=caller.weft_id,
                    title=title, body=body,
                )
            except (notifications.TooMany, ValueError) as exc:
                raise ToolError(str(exc)) from exc
            waiting = await notifications.waiting(session, caller.weft_id)
        # The user's devices, rung in the background: the agent does not wait on it.
        push.announce(caller.user_id, caller.weft_id)
        return (
            f"Delivered: notification {note.id}. Your user sees it when they next open "
            f"Weft; there is no need to send it again. {waiting} from this weft waiting."
        )

    @server.tool(structured_output=False)
    async def status(ctx: Context) -> str:
        """Check this door: which weft your key opens, that it still works, and how many
        of your notifications are still waiting for your user."""
        caller = _caller(ctx)
        async with db.get_sessionmaker()() as session:
            waiting = await notifications.waiting(session, caller.weft_id)
        return (
            f'The door is open for the weft "{caller.weft_name}". '
            f"Key ending {caller.hint}, made {caller.created_at:%Y-%m-%d %H:%M} UTC. "
            f"Notifications waiting for your user: {waiting}."
        )

    return server


class _KeyGate:
    """The MCP app behind a key check. Every request, including ``initialize`` and
    ``tools/list``, so a caller without a key cannot even list the tools.

    A class rather than a function so Starlette treats it as an ASGI app, not as a
    request handler (see ``mount``).
    """

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send) -> None:
        if db.migration_failed():
            await _bare(send, 503)
            return

        raw = None
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                scheme, _, token = value.decode("latin-1").partition(" ")
                if scheme.lower() == "bearer":
                    raw = token.strip()
                break

        async with db.get_sessionmaker()() as session:
            caller = await resolve_key(session, raw)
        if caller is None:
            await _bare(send, 401)
            return

        scope.setdefault("state", {})["weft_caller"] = caller
        await self.inner(scope, receive, send)


async def _bare(send, status: int) -> None:
    """A response with no body: a refused caller learns nothing about what is here."""
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


def mount(app, server) -> None:
    """Serve the door at exactly ``PATH``.

    A Route rather than a Mount. A Mount at ``/api/mcp`` only matches ``/api/mcp/...``,
    so a client given the address without a trailing slash would fall through to the
    static mount and get a 404 that does not look like an MCP problem. The inner app
    is told the same full path, so it routes the request unchanged.
    """
    app.add_route(PATH, _KeyGate(server.streamable_http_app()), include_in_schema=False)
