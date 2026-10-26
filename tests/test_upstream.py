"""
Tests for app/upstream.py — URL validation and model-list parsing.

The URL tests are the security half. ``file://`` reads the container's filesystem;
credentials in a URL are a secret stored outside the vault, in a field we display in
the clear. Neither is caught anywhere else.

The parsing tests are the compatibility half. "OpenAI-compatible" is a spectrum, and
the point of Weft is to work against gateways that only approximately keep the shape —
so the parser is deliberately tolerant about what it accepts and strict about what it
passes on.
"""

import pytest

from app import upstream


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://openrouter.ai/api/v1",
    "http://bridge.lan:8080/v1",
    "http://127.0.0.1:11434/v1",
])
def test_good_urls_pass(url):
    """The last two are the reason this module does not block private address space:
    a gateway on the compose network and a local model server are the intended targets,
    not an attack. See the module docstring for the trigger to revisit that."""
    assert upstream.normalise_base_url(url) == url


def test_a_trailing_slash_is_stripped():
    assert upstream.normalise_base_url("https://host.test/v1/") == "https://host.test/v1"


@pytest.mark.parametrize("url", [
    "",
    "   ",
    "file:///etc/passwd",
    "gopher://host.test/",
    "ftp://host.test/v1",
    "https://",
    "https://user:pass@host.test/v1",
    "https://host.test/v1?key=abc",
    "https://host.test/v1#fragment",
    "not a url at all",
])
def test_bad_urls_are_refused(url):
    with pytest.raises(ValueError):
        upstream.normalise_base_url(url)


def test_an_overlong_url_is_refused():
    with pytest.raises(ValueError):
        upstream.normalise_base_url("https://host.test/" + "a" * upstream.MAX_URL_CHARS)


def test_models_url_appends_one_segment():
    assert upstream.models_url("https://host.test/v1") == "https://host.test/v1/models"
    assert upstream.models_url("https://host.test/v1/") == "https://host.test/v1/models"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

def test_an_empty_secret_sends_no_authorization_header():
    """Some servers 401 on ``Authorization: Bearer`` with an empty value rather than
    treating it as absent, which would make a keyless local gateway unusable."""
    assert "Authorization" not in upstream._auth_headers("")


def test_a_secret_becomes_a_bearer_header():
    assert upstream._auth_headers("abc")["Authorization"] == "Bearer abc"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_the_openai_shape_parses():
    payload = {"object": "list", "data": [{"id": "gpt-4o-mini", "owned_by": "openai"}]}
    assert upstream._parse_models(payload) == [
        {"id": "gpt-4o-mini", "owned_by": "openai"}
    ]


def test_a_bare_list_parses():
    """Several self-hosted servers answer with the array rather than wrapping it."""
    assert upstream._parse_models([{"id": "llama-3"}]) == [{"id": "llama-3", "owned_by": ""}]


def test_a_list_of_plain_strings_parses():
    assert upstream._parse_models(["llama-3"]) == [{"id": "llama-3", "owned_by": ""}]


def test_entries_without_an_id_are_dropped():
    """Rendering them would put blank rows in the model picker with nothing to tick."""
    assert upstream._parse_models({"data": [{"id": ""}, {"nope": 1}, {"id": "ok"}]}) == [
        {"id": "ok", "owned_by": ""}
    ]


def test_duplicate_ids_are_collapsed():
    assert len(upstream._parse_models({"data": [{"id": "a"}, {"id": "a"}]})) == 1


def test_only_id_and_owned_by_survive():
    """A third party must not get to decide the shape of our own API responses, nor
    what a client ends up rendering."""
    parsed = upstream._parse_models({"data": [{"id": "a", "evil": "<script>"}]})
    assert set(parsed[0]) == {"id", "owned_by"}


def test_a_non_list_payload_raises_a_readable_error():
    """The overwhelmingly common cause is a base_url missing its /v1, so the message
    says so instead of reporting a type error."""
    with pytest.raises(upstream.UpstreamError) as exc:
        upstream._parse_models({"error": "not found"})
    assert "/v1" in str(exc.value)


def test_brief_collapses_whitespace_and_truncates():
    """An upstream error body goes into an HTTP detail and a log line. Neither wants
    an unbounded multi-line blob."""
    assert upstream._brief("a\n\n  b") == "a b"
    assert len(upstream._brief("x" * 1000)) == upstream.MAX_UPSTREAM_DETAIL + 1


# ---------------------------------------------------------------------------
# Chat: pulling text out of whatever shape arrived
# ---------------------------------------------------------------------------

def test_chat_url_appends_one_segment():
    assert upstream.chat_url("https://host.test/v1") == "https://host.test/v1/chat/completions"


def test_delta_reads_the_streaming_shape():
    assert upstream._delta_of({"choices": [{"delta": {"content": "hi"}}]}) == "hi"


def test_delta_reads_the_non_streaming_shape():
    """A gateway that ignored `stream: true` answers with `message`, not `delta`.

    Without this branch the reply renders as empty — which looks like the model having
    nothing to say, the most confusing failure available.
    """
    assert upstream._delta_of({"choices": [{"message": {"content": "hi"}}]}) == "hi"


def test_delta_reads_the_legacy_text_shape():
    assert upstream._delta_of({"choices": [{"text": "hi"}]}) == "hi"


@pytest.mark.parametrize("event", [
    {},
    {"choices": []},
    {"choices": [{}]},
    {"choices": [{"delta": {}}]},
    {"choices": [{"delta": {"content": None}}]},
    {"choices": "not a list"},
    {"choices": [{"delta": {"role": "assistant"}}]},   # the opening frame, no text yet
])
def test_delta_of_an_empty_or_odd_frame_is_empty(event):
    """Every one of these is a frame a real stream sends. None is an error."""
    assert upstream._delta_of(event) == ""


@pytest.mark.asyncio
async def test_events_parses_a_stream_and_stops_at_done():
    import httpx

    body = (
        b'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        b"data: [DONE]\n\n"
        b'data: {"choices":[{"delta":{"content":"never"}}]}\n\n'
    )
    events = [e async for e in upstream._events(httpx.Response(200, content=body))]

    assert [upstream._delta_of(e) for e in events] == ["He", "llo"]


@pytest.mark.asyncio
async def test_events_skips_comments_and_unparseable_frames():
    """SSE keep-alive comments are normal traffic, and one malformed frame is not worth
    tearing down an answer that is otherwise arriving fine."""
    import httpx

    body = (
        b": keep-alive\n\n"
        b"event: ping\n\n"
        b"data: {not json}\n\n"
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
    )
    events = [e async for e in upstream._events(httpx.Response(200, content=body))]

    assert [upstream._delta_of(e) for e in events] == ["ok"]


# ---------------------------------------------------------------------------
# Voices — four shapes, because there is no standard here at all
# ---------------------------------------------------------------------------

def test_voices_url_and_speech_url():
    assert upstream.voices_url("https://k.test/v1") == "https://k.test/v1/audio/voices"
    assert upstream.speech_url("https://k.test/v1") == "https://k.test/v1/audio/speech"


@pytest.mark.parametrize("payload", [
    {"voices": ["af_bella", "am_michael"]},            # Kokoro-FastAPI
    ["af_bella", "am_michael"],                        # a bare list
    {"data": [{"id": "af_bella"}, {"id": "am_michael"}]},   # borrowed from /models
    {"voices": [{"name": "af_bella"}, {"voice": "am_michael"}]},
])
def test_every_voices_shape_parses(payload):
    """Accepting all four costs nothing. The alternative is a user typing a voice name
    from memory because their server used the wrong key."""
    assert upstream._parse_voices(payload) == ["af_bella", "am_michael"]


def test_voices_from_an_unexpected_payload_is_empty_not_an_error():
    """OpenAI has no voices endpoint. A provider that does not enumerate itself is not
    broken — the client offers a text field instead of a dropdown."""
    assert upstream._parse_voices({"error": "not found"}) == []
    assert upstream._parse_voices(None) == []


def test_duplicate_voices_are_collapsed():
    assert upstream._parse_voices(["a", "a", "b"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# Extra request headers
# ---------------------------------------------------------------------------

def test_extra_headers_never_replace_what_weft_sets():
    """Belt and braces for a row that reached the column without passing the route:
    a stored Authorization is dropped, not sent."""
    sent = upstream._extra_headers([
        ["X-Session-Id", "demo"], ["Authorization", "Bearer stolen"], ["bad row"],
    ])
    assert sent == {"X-Session-Id": "demo"}


@pytest.mark.asyncio
async def test_stream_chat_sends_extra_headers_beside_the_bearer(monkeypatch):
    import httpx

    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        upstream.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )

    deltas = await upstream.stream_chat(
        "http://gw.test/v1", "sk-1", {"model": "m"},
        extra_headers=[["X-Session-Id", "demo"]],
    )
    assert [d async for d in deltas] == ["hi"]
    assert seen["x-session-id"] == "demo"
    assert seen["authorization"] == "Bearer sk-1"
    assert seen["accept"] == "text/event-stream"


# ---------------------------------------------------------------------------
# Speech models
# ---------------------------------------------------------------------------

def models_server(monkeypatch, answer):
    """Answer /models with `answer(request)`. Returns the requests seen."""
    import httpx

    seen = []

    def handler(request):
        seen.append(request)
        return answer(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        upstream.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


@pytest.mark.asyncio
async def test_speech_models_are_asked_for_by_modality(monkeypatch):
    """OpenRouter keeps its speech models out of the plain list."""
    import httpx

    def answer(request):
        if request.url.params.get("output_modalities") == "speech":
            return httpx.Response(200, json={"data": [{"id": "elevenlabs/eleven-v3"}]})
        return httpx.Response(200, json={"data": [{"id": "@preset/chat-only"}]})

    seen = models_server(monkeypatch, answer)

    models = await upstream.fetch_speech_models("http://or.test/v1", "sk")
    assert [m["id"] for m in models] == ["elevenlabs/eleven-v3"]
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("filtered", [
    lambda httpx: httpx.Response(400, json={"error": "unknown parameter"}),
    lambda httpx: httpx.Response(200, json={"data": []}),
])
async def test_speech_models_fall_back_to_the_plain_list(monkeypatch, filtered):
    import httpx

    def answer(request):
        if "output_modalities" in request.url.params:
            return filtered(httpx)
        return httpx.Response(200, json={"data": [{"id": "kokoro"}]})

    seen = models_server(monkeypatch, answer)

    models = await upstream.fetch_speech_models("http://k.test/v1", "sk")
    assert [m["id"] for m in models] == ["kokoro"]
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# Speech retries
# ---------------------------------------------------------------------------

def speech_server(monkeypatch, answers):
    """Answer successive speech requests from `answers`. Returns the request count."""
    import httpx

    seen = []

    def handler(request):
        answer = answers[len(seen)]
        seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    real = httpx.AsyncClient
    monkeypatch.setattr(
        upstream.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(upstream, "SPEECH_RETRY_DELAY", 0)
    return seen


def audio_ok():
    import httpx
    return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})


async def say(text="Hello."):
    return await upstream.speak("http://k.test/v1", "sk", model="m", voice="v", text=text)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_a_gateway_hiccup_is_asked_again(monkeypatch, status):
    """A proxy whose health check queued behind a long clip answers 502 for a moment.
    Without a retry, that moment cost a whole paragraph."""
    import httpx

    seen = speech_server(monkeypatch, [httpx.Response(status, text="busy"), audio_ok()])

    assert await say() == ("audio/mpeg", b"ID3audio")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_refused_connection_is_asked_again(monkeypatch):
    import httpx

    seen = speech_server(monkeypatch, [httpx.ConnectError("refused"), audio_ok()])

    assert await say() == ("audio/mpeg", b"ID3audio")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_second_hiccup_reaches_the_user(monkeypatch):
    import httpx

    seen = speech_server(monkeypatch, [
        httpx.Response(502, text="down"), httpx.Response(502, text="still down"),
    ])

    with pytest.raises(upstream.UpstreamError, match="502"):
        await say()
    assert len(seen) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "400", "401", "500", "read-timeout",
])
async def test_what_would_fail_again_is_not_retried(monkeypatch, answer):
    """A bad request, a bad key or a broken server gives the same answer twice. A read
    timeout means the server spent minutes on this clip: asking again only queues more."""
    import httpx

    responses = {
        "400": httpx.Response(400, text="bad voice"),
        "401": httpx.Response(401, text="no"),
        "500": httpx.Response(500, text="boom"),
        "read-timeout": httpx.ReadTimeout("slow"),
    }
    seen = speech_server(monkeypatch, [responses[answer], audio_ok()])

    with pytest.raises(upstream.UpstreamError):
        await say()
    assert len(seen) == 1
