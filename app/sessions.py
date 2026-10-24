"""
Session store plumbing: which store a weft's thread lives in, and under what id.

A weft set to ``history: session`` keeps its conversation in a memory-mcp-ce session
store the user added as a ``session`` provider. The service relays: it reads the thread
for the window, and appends each finished exchange after the reply has streamed. It
keeps nothing itself — the service stores no conversation, because the store, like the gateway, is
upstream of the glass.

**The session id is fixed when the weft is made, unless the user typed one.** It is
derived from the user and the weft's own id and stored on the row
(``Weft.auto_session_id``), so every device that opens the weft lands on the same
session without anybody copying an id around, two wefts never share one, and neither a
rename nor a new engine moves it (same weft, same thread)::

    weft.<username>.<weft id>

Wefts that existed before wefts were named individuals kept the id they had then, built from the
provider and model they were bound to; the migration copied it onto the row.

**A typed id is for joining a session somebody else writes** — a harness that already
keeps one, say. So it is looked up rather than claimed: if it exists, it is used as it
stands, whoever created it; only if it does not exist is it created. An automatic id is
always claimed (created, or confirmed to be this user's), because nobody else should be
writing one.

``agent`` is the model id (the weft's name while it has none), and ``user`` is the Weft
username. The store uses ``user`` to
keep different people's chats with the same weft apart; the browser never holds the
store's token, so it cannot claim to be anyone else.
"""

import re
from dataclasses import dataclass

from app import attachments
from app.models import PickHistory, ProviderKind, ProviderSecret, User, Weft

#: The store's session id alphabet and length.
SESSION_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]+")


def derived_session_id(username: str, weft_id: str) -> str:
    """The id a new weft keeps its thread under. Stable, and within 128 chars."""
    return f"weft.{_UNSAFE.sub('.', username)}.{weft_id}"[:128]


def effective_session_id(weft: Weft) -> str:
    return weft.session_id or weft.auto_session_id


@dataclass
class Store:
    """Everything needed to talk to one weft's session, resolved before any streaming."""

    base_url: str
    secret: str
    session_id: str
    #: True when the user typed the id: join it as it stands, never claim it.
    joined: bool
    agent: str
    user: str


def keeps_session(weft: Weft, store: ProviderSecret | None) -> bool:
    """Whether this weft's thread lives in a store right now.

    A weft set to `session` whose store was deleted (the FK nulls) keeps nothing, which
    is the safe failure: the window still talks, it just stops remembering.
    """
    return (
        weft.history is PickHistory.session
        and store is not None
        and store.kind is ProviderKind.session
    )


def store_for(user: User, weft: Weft, store: ProviderSecret, secret: str) -> Store:
    return Store(
        base_url=store.base_url,
        secret=secret,
        session_id=effective_session_id(weft),
        joined=bool(weft.session_id),
        agent=weft.model_id or weft.name,
        user=user.username,
    )


def messages_from_events(events: list[dict]) -> list[dict]:
    """The window's view of a thread: user and assistant messages that have text.

    Everything else a session can hold (reasoning, tool calls, media, kinds nobody has
    invented yet) is skipped rather than rendered. The window is a chat window, and a
    harness writing richer events into the same session should not break it.

    A message with pictures carries them in its body as references, and
    its text starts with their marker so search and other readers know they were there.
    A spoken one has ``spoken: true`` in its body and, usually, its own
    marker. The window gets the references and the flag back and the text without the
    markers, which is the shape it sent them in.

    Each message also carries the store's ``turn``, so a window that adds a catch-up
    read to what it already holds can trim the result to the turns it keeps.
    """
    out = []
    for event in events:
        if (event.get("kind") != "message"
                or event.get("role") not in ("user", "assistant")
                or not isinstance(event.get("text"), str)):
            continue
        message = {"role": event["role"], "content": event["text"]}
        if event.get("turn") is not None:
            message["turn"] = event["turn"]
        body = event.get("body")
        pictures = attachments.from_body(body)
        spoken = isinstance(body, dict) and body.get("spoken") is True
        if pictures or spoken:
            message["content"] = attachments.strip_marker(
                event["text"], [p["name"] for p in pictures], spoken)
        if pictures:
            message["attachments"] = pictures
        if spoken:
            message["spoken"] = True
        out.append(message)
    return out
