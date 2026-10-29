"""
Talking to somebody else's OpenAI-compatible endpoint.

This is the first code in weft-service that makes an outbound request, and the first
that handles a URL a user typed. Both of those are worth being explicit about.

**What this deliberately does NOT do: block private address ranges.** A service that
fetches a user-supplied URL is textbook SSRF, and the usual mitigation is to refuse
RFC1918, loopback and link-local destinations. We do not, because for Weft those are
the *intended* destinations: the gateways this client exists to speak to — ind-bridge,
hermes, OpenClaw — live on the same private subnet as this container, and a local
llama.cpp lives on localhost. Blocking private space would block the product.

So the exposure is real and accepted: anyone who can create a provider can make this
container issue a GET to a host it can reach, and see the status code and a truncated
body. The things that keep that from being a hole are deployment properties, not code:
accounts here are invite-only, and the container's network reach is whatever the
compose network gives it. **If weft-service is ever opened to untrusted signup, this
decision has to be revisited** — that is the trigger to look for, not a code smell.

What is enforced instead, because each of these costs nothing and closes a real gap:

* **http and https only.** ``file://`` would read the container's filesystem;
  ``gopher://`` and friends are the classic protocol-smuggling vectors.
* **No redirects.** A redirect is how an allowed-looking URL becomes a request
  somewhere else, and it is also how a bearer token gets forwarded to a host the user
  never named. No OpenAI-compatible endpoint needs one.
* **No credentials in the URL.** ``https://user:pass@host/v1`` would put a secret in
  a field we show back to the user in the clear.
* **A byte ceiling and a timeout.** An endpoint that streams forever, or answers with a
  gigabyte, must not be able to hold a worker or fill memory.
"""

import asyncio
import json
import logging
import random
import re
from urllib.parse import quote, urlsplit

import httpx

logger = logging.getLogger(__name__)

#: Long enough for a cold model index on a loaded gateway, short enough that a dead
#: host does not hold a request open while the user stares at a spinner.
TIMEOUT_SECONDS = 15.0

#: A model list is a few kilobytes. A megabyte is three orders of magnitude of headroom
#: and still bounds what a hostile endpoint can make this process allocate.
MAX_RESPONSE_BYTES = 1_048_576

#: How much of an upstream error body we quote back. The provider belongs to the user
#: asking, so its own error text is the single most useful thing we can show them —
#: capped, because we do not control its length.
MAX_UPSTREAM_DETAIL = 240

MAX_URL_CHARS = 512


class UpstreamError(Exception):
    """The provider could not be reached, or answered with something unusable.

    Carries a message written to be shown to the user who owns that provider: it is
    their endpoint, and "connection refused" is exactly the diagnostic they need.
    """


def normalise_base_url(raw: str) -> str:
    """Validate a user-supplied provider URL and return it without a trailing slash.

    Raises ``ValueError`` with a message meant for the person typing the form.

    Note what is NOT checked: that the path ends in ``/v1``. Plenty of gateways mount
    the OpenAI surface elsewhere, and guessing on the user's behalf would make the
    field lie about what it wants. The form label says "must include the path"; this
    function takes them at their word.
    """
    url = (raw or "").strip()
    if not url:
        raise ValueError("A provider URL is required.")
    if len(url) > MAX_URL_CHARS:
        raise ValueError("That URL is too long.")

    parts = urlsplit(url)

    if parts.scheme not in ("http", "https"):
        raise ValueError("The URL must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("That URL has no host.")
    if parts.username or parts.password:
        raise ValueError(
            "Put the bearer token in the token field, not in the URL — a URL is shown "
            "back to you in the clear and ends up in logs."
        )
    if parts.query or parts.fragment:
        raise ValueError("The URL should be just the base path, with no ? or #.")

    return url.rstrip("/")


def models_url(base_url: str) -> str:
    """``https://host/v1`` -> ``https://host/v1/models``."""
    return f"{base_url.rstrip('/')}/models"


def _auth_headers(secret: str) -> dict[str, str]:
    """Authorization only when there is something to send.

    An empty bearer is a legitimate configuration — a llama.cpp or an ind-bridge on the
    private network often wants no key — and sending ``Authorization: Bearer`` with an
    empty value makes some servers 401 rather than treat it as absent.
    """
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _brief(text: str) -> str:
    """Squash an upstream body into one short, log-safe, render-safe line."""
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_UPSTREAM_DETAIL:
        return collapsed[:MAX_UPSTREAM_DETAIL] + "…"
    return collapsed


def _parse_models(payload: object) -> list[dict[str, str]]:
    """Pull model ids out of an OpenAI-shaped response.

    Two shapes are accepted: ``{"data": [...]}`` as the OpenAI spec defines it, and a
    bare list, which several self-hosted servers return instead. Entries without a
    usable ``id`` are dropped rather than rendered as blanks.

    Only ``id`` and ``owned_by`` are carried forward. Passing the whole upstream object
    through would put a third party in control of the shape of our own API responses —
    and eventually of what a client renders.
    """
    if isinstance(payload, dict):
        rows = payload.get("data")
    else:
        rows = payload

    if not isinstance(rows, list):
        raise UpstreamError(
            "That endpoint answered, but not with a model list. Check the URL includes "
            "the API path (often /v1)."
        )

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            model_id, owner = row, ""
        elif isinstance(row, dict):
            model_id = row.get("id") or row.get("name") or ""
            owner = row.get("owned_by") or ""
        else:
            continue

        model_id = str(model_id).strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({"id": model_id, "owned_by": str(owner).strip()[:64]})

    return models


async def fetch_models(
    base_url: str, secret: str, *, output_modalities: str | None = None
) -> list[dict[str, str]]:
    """GET ``{base_url}/models`` as this provider's owner, and return what it lists.

    Sorted by id, because the upstream order is arbitrary and a list that reshuffles
    between visits is miserable to pick from.

    ``output_modalities`` is OpenRouter's filter, passed as a query parameter when
    given. See ``fetch_speech_models``.
    """
    url = models_url(base_url)
    params = {"output_modalities": output_modalities} if output_modalities else None

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            # See the module docstring: a redirect is how a request goes somewhere the
            # user did not name, carrying their bearer token with it.
            follow_redirects=False,
        ) as client:
            response = await client.get(url, params=params, headers=_auth_headers(secret))
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{base_url} did not answer within {TIMEOUT_SECONDS:.0f}s.") from exc
    except httpx.RequestError as exc:
        # Connection refused, DNS failure, TLS failure. The class name is the useful
        # part; httpx's message often repeats the URL back with no new information.
        raise UpstreamError(f"Could not reach {base_url} ({type(exc).__name__}).") from exc

    if response.is_redirect:
        raise UpstreamError(
            f"{base_url} redirected to somewhere else. Point the provider straight at "
            f"the real endpoint — a redirect would carry your token to a host you did "
            f"not name."
        )

    if response.status_code in (401, 403):
        raise UpstreamError(
            f"{base_url} rejected the token ({response.status_code}). "
            f"Check the bearer on this provider."
        )

    if response.status_code >= 400:
        raise UpstreamError(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )

    if len(response.content) > MAX_RESPONSE_BYTES:
        raise UpstreamError(f"{base_url} returned an implausibly large model list.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(
            f"{base_url} did not answer with JSON. Check the URL includes the API path "
            f"(often /v1)."
        ) from exc

    models = _parse_models(payload)
    models.sort(key=lambda row: row["id"].lower())
    return models


async def fetch_speech_models(base_url: str, secret: str) -> list[dict[str, str]]:
    """The models a speech provider can speak with.

    OpenRouter's plain ``/models`` lists only its chat models, hundreds of them, and none
    of the speech ones; those appear only when asked for with
    ``?output_modalities=speech``. Every other server seen so far ignores the parameter
    and answers its usual list, so it is always sent. A server that refuses it, or
    answers with nothing, is asked again without it.
    """
    try:
        models = await fetch_models(base_url, secret, output_modalities="speech")
    except UpstreamError:
        models = []
    return models or await fetch_models(base_url, secret)


# ---------------------------------------------------------------------------
# Extra request headers (2026-10-06)
# ---------------------------------------------------------------------------
#
# A pick may send headers of its own with its chat requests: a harness behind the
# endpoint that pins its session from one, a gateway that wants a title. The user types
# them; this is the one place that decides what is allowed onto the wire.

#: A ceiling on how many, and on each part. Real uses are two or three short headers.
MAX_EXTRA_HEADERS = 16
MAX_HEADER_NAME_CHARS = 64
MAX_HEADER_VALUE_CHARS = 1024

#: An RFC 9110 field name: a "token", nothing else. Rules out spaces, colons and
#: anything that would let a name smuggle a second header in.
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

#: Printable ASCII and spaces. No CR or LF (header injection), no other control bytes,
#: and no non-ASCII, which servers disagree about how to read.
_HEADER_VALUE = re.compile(r"^[\x20-\x7e]*$")

#: Names Weft owns, compared lowercased. ``authorization`` is the provider's token
#: field, and a header here must never be a second, unsealed place to keep a key or a
#: way to replace the sealed one. The rest describe the request itself (its framing,
#: encoding and connection), which httpx sets from what is actually sent; overriding
#: them would make the request lie about its own body. ``cookie`` has no place on a
#: request a server makes.
RESERVED_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie",
    "host", "content-length", "content-type", "content-encoding", "transfer-encoding",
    "connection", "keep-alive", "proxy-connection", "te", "trailer", "upgrade", "expect",
    "accept", "accept-encoding",
})


def clean_headers(rows: list[tuple[str, str]]) -> list[list[str]]:
    """Validate the headers a user typed for a pick, and return them as stored.

    Names and values are stripped; a row with a blank name is dropped, so an empty
    line left in the form is not an error. Names keep the case they were typed in
    (HTTP ignores it, people reading the panel do not), but duplicates are compared
    without it.

    Raises ``ValueError`` with a message meant for the person typing the form.
    """
    cleaned: list[list[str]] = []
    seen: set[str] = set()
    for raw_name, raw_value in rows:
        name = (raw_name or "").strip()
        value = (raw_value or "").strip()
        if not name:
            continue
        if len(name) > MAX_HEADER_NAME_CHARS or not _HEADER_NAME.match(name):
            raise ValueError(
                f"'{name[:MAX_HEADER_NAME_CHARS]}' is not a header name. Letters, digits "
                f"and - are the usual; no spaces or colons."
            )
        lowered = name.lower()
        if lowered in RESERVED_HEADERS:
            raise ValueError(
                f"{name} is a header Weft sets itself."
                + (" The provider's token goes in its token field."
                   if "authorization" in lowered else "")
            )
        if lowered in seen:
            raise ValueError(f"{name} is in the list twice.")
        if len(value) > MAX_HEADER_VALUE_CHARS:
            raise ValueError(f"The value for {name} is too long.")
        if not _HEADER_VALUE.match(value):
            raise ValueError(
                f"The value for {name} has a character a header cannot carry. Plain "
                f"ASCII only, on one line."
            )
        seen.add(lowered)
        cleaned.append([name, value])

    if len(cleaned) > MAX_EXTRA_HEADERS:
        raise ValueError(f"At most {MAX_EXTRA_HEADERS} headers per pick.")
    return cleaned


def _extra_headers(stored: list | None) -> dict[str, str]:
    """A pick's stored headers as a dict to send, re-checked on the way out.

    The route already refused anything bad, so this is belt and braces: a row that
    somehow reached the column without passing ``clean_headers`` (an old row, a hand
    edit) is dropped rather than sent, and a reserved name can never override what
    Weft set.
    """
    out: dict[str, str] = {}
    for row in stored or []:
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            continue
        try:
            (name, value), = clean_headers([(str(row[0]), str(row[1]))])
        except ValueError:
            logger.warning("Dropped a stored header that no longer validates.")
            continue
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

#: Chat needs a different clock from model discovery. The connection should still fail
#: fast, but a model that thinks for four minutes before its first token is a model
#: doing its job, not a hung request.
CHAT_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)


def chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _delta_of(event: dict) -> str:
    """Pull the text out of one upstream chunk, whatever shape it arrived in.

    Streaming puts it in ``choices[0].delta.content``; a non-streaming completion puts
    it in ``choices[0].message.content``. Accepting both means a gateway that quietly
    ignores ``stream: true`` still works instead of producing an empty reply, which is
    the most confusing possible failure — it looks like the model had nothing to say.
    """
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""

    for key in ("delta", "message"):
        part = first.get(key)
        if isinstance(part, dict):
            content = part.get("content")
            if isinstance(content, str):
                return content

    # Some servers stream a bare `text` field (the legacy completions shape).
    text = first.get("text")
    return text if isinstance(text, str) else ""


async def _events(response: httpx.Response):
    """Yield decoded JSON objects from an upstream SSE body.

    Deliberately minimal: SSE has comments, event names, ids and retry directives, and
    an OpenAI-compatible stream uses exactly none of them. Anything that is not a
    ``data:`` line is skipped rather than parsed, because the alternative is a full SSE
    implementation defending against fields no server here will ever send.

    ``aiter_lines`` handles the chunk-boundary problem — a JSON object split across two
    TCP reads — which is the bug that makes hand-rolled stream parsers work perfectly
    until the day a reply gets long.
    """
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            # A malformed frame is not worth tearing the answer down for; the rest of
            # the stream is usually fine.
            logger.warning("Skipped an unparseable SSE frame from upstream.")
            continue
        if isinstance(event, dict):
            yield event


async def stream_chat(base_url: str, secret: str, payload: dict, *,
                      extra_headers: list | None = None):
    """POST a chat completion and yield its text deltas as they arrive.

    ``extra_headers`` is the pick's own ``[[name, value], ...]``, sent alongside
    Weft's. It can add headers and never replace one: see ``RESERVED_HEADERS``.

    **Errors before the first token raise ``UpstreamError``; errors after it do not.**
    That asymmetry is forced by HTTP, not chosen: once a 200 and its headers are on the
    wire, there is no status code left to change. So this function does all the failing
    it can *before* returning its first chunk — which is what lets the route answer a
    clean 502 — and a mid-stream failure is raised to the caller to be reported inside
    the stream instead.

    The client and the response context manager are entered by hand rather than with
    ``async with``, because their lifetime has to outlive this function's first yield:
    the route needs the status checked *now* and the body drained *later*, by Starlette,
    after the response has already begun.
    """
    url = chat_url(base_url)
    headers = _extra_headers(extra_headers)
    headers.update(_auth_headers(secret))
    headers["Accept"] = "text/event-stream"

    client = httpx.AsyncClient(timeout=CHAT_TIMEOUT, follow_redirects=False)
    stream = client.stream("POST", url, json=payload, headers=headers)

    try:
        response = await stream.__aenter__()
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise UpstreamError(f"{base_url} did not answer in time.") from exc
    except httpx.RequestError as exc:
        await client.aclose()
        raise UpstreamError(f"Could not reach {base_url} ({type(exc).__name__}).") from exc

    async def abort() -> None:
        await stream.__aexit__(None, None, None)
        await client.aclose()

    if response.is_redirect:
        await abort()
        raise UpstreamError(
            f"{base_url} redirected the chat request. Point the provider straight at "
            f"the real endpoint."
        )

    if response.status_code >= 400:
        # The body has not been read yet on a streamed response, and the error text is
        # the single most useful thing we can show — so read it before giving up on it.
        body = await response.aread()
        await abort()
        if response.status_code in (401, 403):
            raise UpstreamError(
                f"{base_url} rejected the token ({response.status_code}). "
                f"Check the bearer on this provider."
            )
        raise UpstreamError(
            f"{base_url} answered {response.status_code}: "
            f"{_brief(body.decode('utf-8', 'replace'))}"
        )

    async def drain():
        try:
            if "text/event-stream" in (response.headers.get("content-type") or ""):
                async for event in _events(response):
                    delta = _delta_of(event)
                    if delta:
                        yield delta
            else:
                # A gateway that ignored `stream: true` and answered with one JSON
                # document. Not an error — plenty of small servers do this — so take
                # the whole answer and emit it as a single delta.
                whole = await response.aread()
                try:
                    event = json.loads(whole)
                except ValueError as exc:
                    raise UpstreamError(
                        f"{base_url} answered with neither a stream nor JSON."
                    ) from exc
                delta = _delta_of(event) if isinstance(event, dict) else ""
                if delta:
                    yield delta
        finally:
            await abort()

    return drain()


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------

#: Generating a minute of audio takes a while on CPU, and the whole clip arrives in one
#: response — we are not streaming audio yet. Read generously; connect fast.
SPEECH_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)

#: A ceiling on one clip. Kokoro at 24kHz mp3 is roughly 1MB per two minutes, so this
#: is minutes of speech and still bounds what one reply can make this process hold.
MAX_AUDIO_BYTES = 20_971_520

#: What we ask for. mp3 because every browser plays it and it is the OpenAI default;
#: whatever the server actually sends back is passed through with its own content type,
#: so a provider that only does wav still works.
DEFAULT_AUDIO_FORMAT = "mp3"

#: What we ask for when a server refuses mp3 by name. Ollama's speech endpoint does wav
#: and pcm only, and new models served that way are arriving fast. wav, not "leave it
#: out": a server whose own default is raw pcm would send audio with no header, which no
#: browser plays. wav is about five times the size of mp3, which is why it is not first.
FALLBACK_AUDIO_FORMAT = "wav"

#: The statuses a refused format comes back as: a plain 400, or a 422 from a server
#: whose request validation rejects the field.
FORMAT_REFUSED_STATUSES = frozenset({400, 422})

#: (base_url, model) pairs that refused mp3 once. Per process, in memory, so a reply in
#: twelve paragraphs is not twelve refusals; a restart asks for mp3 again, which is
#: how a server that has learned mp3 since gets noticed.
_REFUSED_DEFAULT_FORMAT: set[tuple[str, str]] = set()

#: Asking twice, at most. A speech server that answers 502 for a moment —
#: a proxy whose health check queued behind a long clip and marked the backend down — is
#: usually fine a second later, and without a retry that moment cost a whole paragraph.
SPEECH_ATTEMPTS = 2

#: The pause before asking again. Long enough for a proxy to see the backend come back;
#: short enough that the paragraph is still on its way before the one ahead finishes.
SPEECH_RETRY_DELAY = 1.5

#: The answers worth asking again. A gateway that could not reach the server, or a
#: server saying it is busy. Anything else will give the same answer twice.
SPEECH_RETRY_STATUSES = frozenset({502, 503, 504})


class _SpeechHiccup(UpstreamError):
    """A failure that a second attempt has a fair chance of not seeing."""


class _FormatRefused(UpstreamError):
    """The server will not make audio in the format we asked for."""


def voices_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/voices"


def speech_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/speech"


def _parse_voices(payload: object) -> list[str]:
    """Pull voice names out of whatever the endpoint answered with.

    There is no standard here. OpenAI has no voices endpoint at all (its six voices are
    documented, not discoverable); Kokoro-FastAPI answers ``{"voices": [...]}``; others
    return a bare list, or the ``{"data": [...]}`` shape borrowed from /models. All four
    are cheap to accept, and the alternative is a user typing a voice name from memory.
    """
    if isinstance(payload, dict):
        rows = payload.get("voices")
        if rows is None:
            rows = payload.get("data")
    else:
        rows = payload

    if not isinstance(rows, list):
        return []

    voices: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            name = row
        elif isinstance(row, dict):
            name = row.get("id") or row.get("name") or row.get("voice") or ""
        else:
            continue

        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            voices.append(name)

    return voices


async def fetch_voices(base_url: str, secret: str) -> list[str]:
    """List a speech provider's voices, or return [] if it does not say.

    Returning empty rather than raising is deliberate: a provider with no voices
    endpoint is not broken, it is just OpenAI-shaped. The caller offers a text field
    instead of a dropdown, and everything still works — which is better than refusing to
    configure a provider because it would not enumerate itself.
    """
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.get(voices_url(base_url), headers=_auth_headers(secret))
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not reach {base_url} ({type(exc).__name__}).") from exc

    if response.status_code == 404:
        # The common case for an OpenAI-proper endpoint. Not an error.
        return []
    if response.status_code >= 400:
        raise UpstreamError(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise UpstreamError(f"{base_url} returned an implausibly large voice list.")

    try:
        payload = response.json()
    except ValueError:
        return []

    return sorted(_parse_voices(payload), key=str.lower)


async def speak(
    base_url: str, secret: str, *, model: str, voice: str, text: str
) -> tuple[str, bytes]:
    """Turn text into audio. Returns ``(content_type, audio_bytes)``.

    Not streamed, on purpose and for now. Streaming speech properly means detecting
    sentence or paragraph boundaries in the reply as it arrives and pipelining clips, so
    the voice starts before the model has finished thinking. That is the right end state
    and it is a separate piece of work; this waits for the whole reply and speaks it
    once, which is enough to find out whether the room sounds better with voices in it.

    The upstream's content type is passed back rather than assumed, so a provider that
    answers wav when asked for mp3 still plays.

    A 502, 503 or 504, or a server that could not be reached, is asked once more after a
    short pause. Only the second failure reaches the user.

    A server that refuses mp3 by name is asked again for wav, and that model on that
    server is asked for wav from then on.
    """
    refused = (base_url, model) in _REFUSED_DEFAULT_FORMAT
    payload = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": FALLBACK_AUDIO_FORMAT if refused else DEFAULT_AUDIO_FORMAT,
    }

    try:
        return await _speak_retrying(base_url, secret, payload)
    except _FormatRefused:
        if refused:
            raise
        logger.info(
            "%s refused %s for %s; asking for %s.",
            base_url, DEFAULT_AUDIO_FORMAT, model, FALLBACK_AUDIO_FORMAT,
        )
        _REFUSED_DEFAULT_FORMAT.add((base_url, model))
        payload["response_format"] = FALLBACK_AUDIO_FORMAT
        return await _speak_retrying(base_url, secret, payload)


async def _speak_retrying(base_url: str, secret: str, payload: dict) -> tuple[str, bytes]:
    """One clip, with the hiccup retry."""
    for attempt in range(1, SPEECH_ATTEMPTS + 1):
        try:
            return await _speak_once(base_url, secret, payload)
        except _SpeechHiccup as exc:
            if attempt == SPEECH_ATTEMPTS:
                raise UpstreamError(str(exc)) from exc
            logger.info("Speech from %s hiccuped (%s); asking again.", base_url, exc)
            await asyncio.sleep(SPEECH_RETRY_DELAY)
    raise AssertionError("unreachable")  # pragma: no cover


async def _speak_once(base_url: str, secret: str, payload: dict) -> tuple[str, bytes]:
    """One request for one clip. Raises ``_SpeechHiccup`` for what is worth retrying.

    A READ timeout is deliberately not a hiccup. It means the server took three minutes
    over this clip, and asking again would only queue another three minutes behind
    whatever it is still doing. A connect timeout or a dropped connection is retried:
    those say the server was not there, not that it was busy.
    """
    try:
        async with httpx.AsyncClient(
            timeout=SPEECH_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.post(
                speech_url(base_url), json=payload, headers=_auth_headers(secret)
            )
    except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise _SpeechHiccup(f"{base_url} took too long to answer.") from exc
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{base_url} took too long to speak.") from exc
    except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
        raise _SpeechHiccup(
            f"Could not reach {base_url} ({type(exc).__name__})."
        ) from exc
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not reach {base_url} ({type(exc).__name__}).") from exc

    if response.status_code in (401, 403):
        raise UpstreamError(
            f"{base_url} rejected the token ({response.status_code}). "
            f"Check the bearer on this speech provider."
        )
    if response.status_code in SPEECH_RETRY_STATUSES:
        raise _SpeechHiccup(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )
    if (
        response.status_code in FORMAT_REFUSED_STATUSES
        and "response_format" in response.text
    ):
        # Ollama: 'response_format "mp3" is not supported, use wav or pcm'. Matching the
        # field name rather than the wording keeps a bad voice or model a plain error.
        raise _FormatRefused(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )
    if response.status_code >= 400:
        raise UpstreamError(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )

    audio = response.content
    if len(audio) > MAX_AUDIO_BYTES:
        raise UpstreamError(f"{base_url} returned more audio than Weft will hold.")
    if not audio:
        raise UpstreamError(f"{base_url} answered with no audio.")

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
    if not content_type.startswith("audio/"):
        # A JSON error body behind a 200 is a real thing some servers do. Rendering it
        # as audio would produce silence and no explanation.
        raise UpstreamError(
            f"{base_url} answered {content_type or 'an unknown type'} instead of audio: "
            f"{_brief(audio[:512].decode('utf-8', 'replace'))}"
        )

    return content_type, audio


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------

#: Transcribing two minutes of speech on a small CPU can take a while; the read
#: timeout is what that costs, not what a healthy server needs.
TRANSCRIBE_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=60.0, pool=15.0)

#: OpenAI's own ceiling on an upload to /audio/transcriptions.
MAX_TRANSCRIBE_BYTES = 25 * 1_048_576

#: What a transcript may be. Two minutes of fast speech is a few thousand characters.
MAX_TRANSCRIPT_CHARS = 32_768


def transcriptions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/transcriptions"


#: The statuses a server refuses a recording it cannot decode with.
RECORDING_REFUSED_STATUSES = frozenset({400, 415, 422})

#: What every server that takes a recording at all can read.
WAV_TYPES = frozenset({"audio/wav", "audio/x-wav", "audio/wave"})


class RecordingRefused(UpstreamError):
    """The server would not take a recording that was not wav; wav may yet work."""


async def transcribe(
    base_url: str, secret: str, *, audio: bytes, content_type: str,
    model: str, language: str | None = None,
) -> str:
    """Turn a recording into words. Returns the transcript, stripped.

    Multipart, as the OpenAI protocol has it: the recording as ``file`` and the model
    name beside it. ``response_format=json`` asks for ``{"text": ...}``, which every
    OpenAI-shaped server answers; anything fancier (segments, timestamps) is not needed
    to put words in a text box.
    """
    extension = {
        "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
        "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac",
    }.get(content_type, "webm")
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language

    try:
        async with httpx.AsyncClient(
            timeout=TRANSCRIBE_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.post(
                transcriptions_url(base_url),
                data=data,
                # The file name matters more than it should: some servers pick the
                # decoder from its extension rather than from the content type.
                files={"file": (f"speech.{extension}", audio, content_type)},
                headers=_auth_headers(secret),
            )
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{base_url} took too long to transcribe.") from exc
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not reach {base_url} ({type(exc).__name__}).") from exc

    if response.status_code in (401, 403):
        raise UpstreamError(
            f"{base_url} rejected the token ({response.status_code}). "
            f"Check the bearer on this speech-to-text provider."
        )
    if response.status_code in RECORDING_REFUSED_STATUSES and content_type not in WAV_TYPES:
        # Ollama: "Failed to load image or audio file" for webm, ogg and flac alike. A
        # 400 cannot say whether it was the container or something else, so the browser
        # finds out by sending wav once; a 400 about the model comes back again.
        raise RecordingRefused(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )
    if response.status_code >= 400:
        raise UpstreamError(
            f"{base_url} answered {response.status_code}: {_brief(response.text)}"
        )
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise UpstreamError(f"{base_url} returned an implausibly large transcript.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(
            f"{base_url} did not answer with JSON: {_brief(response.text)}"
        ) from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise UpstreamError(f"{base_url} answered without a transcript.")
    return text.strip()[:MAX_TRANSCRIPT_CHARS]


# ---------------------------------------------------------------------------
# Session store (memory-mcp-ce /v1/sessions, 2026-10-04)
# ---------------------------------------------------------------------------
#
# A session provider's base_url is the memory-mcp-ce server itself, not an OpenAI-shaped
# /v1. Its session API lives at {base}/v1/sessions. A trailing /v1 is tolerated, because
# typing one is the habit every other provider on this screen teaches.
#
# The store is compare-and-append: every event carries a client-stated `seq`, and the
# store answers 409 `gap` or `diverged` if that seq is not where the session ends.
# Weft only ever appends whole exchanges at the end, so either answer just means
# another device got there first; the fix is to read where it ends now and append there.

SESSION_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

#: A thread tail is text, a few hundred events at most. Generous, still bounded.
MAX_SESSION_BYTES = 16 * 1_048_576


class SessionStoreError(UpstreamError):
    """The session store refused or failed. ``status`` and ``code`` are the store's own."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def sessions_url(base_url: str, *parts: str) -> str:
    """``http://mmce:5005`` -> ``http://mmce:5005/v1/sessions[/part...]``."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    tail = "".join(f"/{quote(part, safe='')}" for part in parts)
    return f"{base}/v1/sessions{tail}"


async def _session_call(
    method: str, base_url: str, secret: str, *parts: str,
    params: dict | None = None, body: dict | None = None,
) -> tuple[int, object]:
    """One request to the store. Returns (status, json) for 2xx and 404/409; raises
    SessionStoreError for anything else, worded for the user who owns the store."""
    headers = _auth_headers(secret)
    try:
        async with httpx.AsyncClient(
            timeout=SESSION_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.request(
                method, sessions_url(base_url, *parts),
                params=params, json=body, headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise SessionStoreError(f"The session store at {base_url} did not answer in time.") from exc
    except httpx.RequestError as exc:
        raise SessionStoreError(
            f"Could not reach the session store at {base_url} ({type(exc).__name__})."
        ) from exc

    if response.is_redirect:
        raise SessionStoreError(
            f"{base_url} redirected. Point the session provider straight at the server."
        )
    if len(response.content) > MAX_SESSION_BYTES:
        raise SessionStoreError(f"{base_url} returned an implausibly large answer.")

    try:
        payload = response.json() if response.content else None
    except ValueError:
        payload = None

    if response.status_code in (401, 403):
        raise SessionStoreError(
            f"The session store at {base_url} rejected the token ({response.status_code}). "
            f"Check the bearer on this session provider — it is the store's "
            f"SESSION_BEARER_TOKEN.",
            status=response.status_code,
        )
    if response.status_code < 300 or response.status_code in (404, 409):
        if response.status_code == 404 and not isinstance(payload, dict):
            # A plain 404 is the store saying its session API is switched off (or the
            # URL is not a memory-mcp-ce at all), not "no such session".
            raise SessionStoreError(
                f"{base_url} has no session API. Check the URL, and that the server has "
                f"SESSION_BEARER_TOKEN set.",
                status=404,
            )
        return response.status_code, payload

    detail = payload.get("message") if isinstance(payload, dict) else _brief(response.text)
    raise SessionStoreError(
        f"The session store answered {response.status_code}: {detail}",
        status=response.status_code,
        code=payload.get("error") if isinstance(payload, dict) else None,
    )


async def session_handshake(base_url: str, secret: str, session_id: str) -> dict | None:
    """The session's summary (last_seq, last_turn, agent, ...), or None if it does not exist."""
    status, payload = await _session_call("GET", base_url, secret, session_id)
    return payload if status == 200 else None


async def ensure_session(
    base_url: str, secret: str, session_id: str, *, agent: str, user: str | None,
) -> dict:
    """Create the session, or confirm it is ours. Raises on agent or user mismatch."""
    body: dict = {"agent": agent}
    if user:
        body["user"] = user
    status, payload = await _session_call("PUT", base_url, secret, session_id, body=body)
    if status == 409:
        raise SessionStoreError(
            f"Session '{session_id}' already exists and belongs to someone else "
            f"({payload.get('message') if isinstance(payload, dict) else 'conflict'}).",
            status=409,
            code=payload.get("error") if isinstance(payload, dict) else None,
        )
    return payload


async def session_tail(
    base_url: str, secret: str, session_id: str, *, last_turns: int,
) -> dict | None:
    """The last ``last_turns`` exchanges, or None if the session does not exist.

    The full view, bodies included. A message with pictures keeps them in its body as
    references, and the text view leaves bodies out, so a thread read
    that way came back with every picture turned into its text marker: on another
    device, and on this one the next time the window re-read the store.
    """
    status, payload = await _session_call(
        "GET", base_url, secret, session_id, "events",
        params={"last_turns": str(last_turns), "view": "full"},
    )
    return payload if status == 200 else None


async def session_since(
    base_url: str, secret: str, session_id: str, *, after_seq: int, limit: int,
) -> dict | None:
    """Every event after ``after_seq``, up to ``limit``, or None if the session does not exist.

    The catch-up read. A window that already holds the thread up to some seq needs only
    what came after it, and the store decrypts each event it returns, so asking for the
    two new events instead of the whole tail is the difference between milliseconds and
    tens of seconds on a long thread. ``has_more`` in the answer means there were more
    than ``limit``.
    """
    status, payload = await _session_call(
        "GET", base_url, secret, session_id, "events",
        params={"after_seq": str(after_seq), "limit": str(limit), "view": "full"},
    )
    return payload if status == 200 else None


async def append_session_events(
    base_url: str, secret: str, session_id: str, events: list[dict], *, attempts: int = 5,
) -> dict:
    """Append ``events`` (without seq) at the end of the session.

    Reads where the session ends, numbers the events from there, and posts them. A 409
    means another device appended in between; read again and retry, up to ``attempts``,
    with a short jittered pause.
    """
    last_error: SessionStoreError | None = None
    for attempt in range(attempts):
        if attempt:
            # Jittered, so two devices that collided do not collide again in lockstep.
            await asyncio.sleep(random.uniform(0.05, 0.3) * attempt)
        handshake = await session_handshake(base_url, secret, session_id)
        if handshake is None:
            raise SessionStoreError(f"Session '{session_id}' does not exist.", status=404)
        start = int(handshake.get("last_seq") or 0) + 1
        numbered = [{**event, "seq": start + i} for i, event in enumerate(events)]
        status, payload = await _session_call(
            "POST", base_url, secret, session_id, "events", body={"events": numbered},
        )
        if status in (200, 201):
            return payload
        code = payload.get("error") if isinstance(payload, dict) else None
        last_error = SessionStoreError(
            f"The session store refused the append ({code or status}).", status=status, code=code,
        )
        if status != 409:
            break
    raise last_error
