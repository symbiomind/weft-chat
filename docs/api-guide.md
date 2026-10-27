# Weft service — client guide

For whoever writes a client against this API. `openapi.json` beside this file is the
generated, authoritative description of *what the endpoints are*; it is regenerated from
the code and verified by a test, so it cannot drift.

This document covers what OpenAPI structurally cannot express: the rules that span
several endpoints, and the ones that will bite you silently if you do not follow them.

---

## The three credentials

| credential | lives in | lifetime | who can read it |
|---|---|---|---|
| **access token** | a JavaScript variable | 15 min | your code, and any XSS |
| **refresh token** | httpOnly cookie | 30 days | nobody in the page — only the server |
| **CSRF token** | readable cookie + header | 24 h | same-origin script only |

Three credentials with three different exposure profiles, on purpose: compromising one
does not yield the others.

**The access token must never be written to `localStorage`, `sessionStorage`, or a
readable cookie.** The entire benefit of the split is that an XSS steals a 15-minute
credential that dies with the tab, rather than a 30-day one it can exfiltrate and use
from somewhere else. Putting it in storage throws that away.

---

## The five rules

These are server guarantees that depend on client behaviour. The server cannot enforce
them; it can only misbehave when they are broken.

### 1. One shared in-flight refresh promise

```js
let refreshPromise = null;

function refresh() {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;          // every caller awaits the SAME request
}
```

**This is the big one.** Refresh tokens rotate, and presenting a spent token is treated
as theft: the server revokes the whole session family and logs a security warning.
There is deliberately no grace window.

So two tabs — or two concurrent 401 retries — that each fire their own refresh will
present the same token twice and log the user out. The symptom is random logouts nobody
can reproduce, because they depend on tab timing.

### 2. Re-sync the CSRF token after every token-issuing response

`POST /api/auth/login`, `POST /api/auth/refresh` and `POST /api/auth/password` each mint
a **fresh** CSRF token, return it in the response body as `csrf`, and set the matching
cookie. A client that keeps sending its previous token gets `403` on its next
state-changing request, because the cookie moved on and the header did not.

```js
const body = await response.json();
csrfToken = body.csrf;            // after EVERY one of those three
```

### 3. Refresh at ~80% of `expires_in`, and on the first 401

`expires_in` is **relative seconds**, deliberately. Never compare a server timestamp
against `Date.now()` — a browser with a skewed clock would then refresh far too early or
far too late.

### 4. Never put a token in a URL

Not a query string, not a path segment. URLs land in access logs, proxy logs, browser
history and `Referer` headers. Invite tokens ride in `location.hash` (never sent to the
server) and are read by script, then POSTed in a body.

### 5. `textContent`, not `innerHTML`

Nothing the server returns should be interpolated as HTML. This matters more once model
output starts flowing through the chat.

---

## Flows

### First contact

```
GET  /api/auth/csrf          -> { csrf }   + sets the CSRF cookie
```

Unauthenticated by necessity — login is itself a protected POST, so a token must be
obtainable before anyone is logged in. The token proves nothing about identity; it only
proves the request came from script on our own origin.

### Login

```
POST /api/auth/login
     X-Weft-CSRF: <token>
     Origin: <your origin>
     { "username": "...", "password": "..." }

  -> 200 { access_token, expires_in, csrf, user }
         + httpOnly refresh cookie
```

Then: keep `access_token` in memory, update `csrfToken` from `csrf`, schedule a refresh.

If `user.must_change_password` is `true`, the only routes that will answer are
`/api/auth/password` and `/api/auth/logout`. Everything else returns `409`. Send the
user straight to a change-password screen.

### Authenticated requests

```
Authorization: Bearer <access_token>
X-Weft-CSRF:   <csrf_token>        (on POST/PUT/PATCH/DELETE only)
```

### Refresh

```
POST /api/auth/refresh
     X-Weft-CSRF: <token>

  -> 200 { access_token, expires_in, csrf, user }   + rotated refresh cookie
  -> 401                                            + CLEARED refresh cookie
```

**A 401 here means "go to login", not "retry".** The cookie has already been cleared;
retrying presents a token the server has rejected, and if the rejection was reuse
detection then every retry looks like a fresh attack in the logs.

### Logout

```
POST /api/auth/logout       ends this device's session
POST /api/auth/logout-all   ends every session, everywhere (needs a bearer token)
```

`logout` authenticates from the **cookie**, not the access token, so it works after the
access token has expired — which is the common case, since people log out after leaving
a tab open overnight.

Both clear the CSRF cookie too, so a client that continues afterwards must re-fetch
`/api/auth/csrf`, exactly as a freshly loaded page would.

### Signed-in devices

```
GET    /api/auth/sessions        every device this account is signed in on, this one first
DELETE /api/auth/sessions/{id}   sign one device out (bearer token + CSRF)
```

Each device carries `current`, `active` (refreshed within the access-token lifetime),
`browser`, `os`, `ip`, the raw `user_agent`, its timestamps, and `seen_seconds_ago`.
Show "last seen" from `seen_seconds_ago`, not by comparing `last_seen_at` with the
device's own clock.

Signing a device out takes effect on **its next request**, not when its access token
runs out. A device that is not yours, already signed out, or never existed is the same
`404`. Deleting the device you are on is a logout: the response clears both cookies.

Devices that have been signed out or have expired are deleted about a week later
(`security.session_purge_grace_days`), along with their refresh tokens.

---

## Status codes and what they mean for you

| code | meaning | what the client should do |
|---|---|---|
| `401` | not authenticated, or the credential is stale | refresh once; if that 401s, go to login |
| `403` | CSRF or Origin rejected, **or** insufficient role | re-fetch CSRF and retry once; if it persists, it is a permissions problem — do not loop |
| `409` | `must_change_password` is set | send the user to the change-password screen |
| `429` | rate limited | honour `Retry-After`; do not retry sooner |
| `503` | a subsystem is unconfigured on the server | show a service message, not a login error |

**Authentication failures are deliberately indistinguishable.** Unknown username, wrong
password and deactivated account all return the same `401` with the same body, and take
the same time. Do not try to tell them apart or surface a guess to the user — that is
the property that stops this endpoint enumerating accounts.

---

## Rate limiting

Two independent limits:

- **Per username** — 5 consecutive failures since the last success, then a 15-minute
  lockout. Applies to usernames that do not exist, too; otherwise the 429 itself would
  confirm which accounts are real.
- **Per IP** — 20 failures in 5 minutes.

Lockout expires on its own and never disables an account, so nobody can lock a user out
permanently by deliberately failing their login.

---

## Providers, models and wefts

A provider is one OpenAI-compatible endpoint the signed-in user owns: a name, a base URL
including its API path, and a bearer token. `GET /api/providers` lists them,
`POST` adds one, `PATCH` edits, `DELETE` removes.

**No endpoint returns a stored provider secret in plaintext** — not to its owner, not to
an admin, and there is a test that searches every provider response for the submitted
token. What reads return is `hint`: the last four characters, enough to recognise a key
and not enough to be one. The service makes the upstream calls itself, so the browser
never holds a provider key.

That has one consequence every client has to handle. On `PATCH`, the `secret` field has
**three** meanings, not two:

| you send | the server does |
|---|---|
| no `secret` key at all | leaves the stored token alone |
| `"secret": ""` | clears it — the provider then sends no `Authorization` header |
| `"secret": "sk-…"` | replaces it |

So an edit form leaves the token field blank (there is nothing to prefill it with) and
omits the field entirely unless the user typed something. Treating an empty input as
`""` would silently unauthenticate a working provider every time somebody fixed a typo
in its name.

### Models

`GET /api/providers/{id}/models` fetches that provider's `/models` **live**, every time.
Nothing is cached: it is a third party's catalogue, and a stale copy produces the worst
class of bug report — "I added it upstream and Weft cannot see it".

Failures upstream come back as **502**, carrying the provider's own words in `detail`
("connection refused", "401", whatever it said). A 502 here means your provider is
unreachable or unhappy; it does not mean this service is broken. Expect the call to be
slow — it is somebody else's network — and expect large answers: a real provider
returned 445 models the first time this code called one.

Each model carries `wefts`: the names of the caller's wefts already bound to it on this
provider. Models some weft uses are listed first.

### Wefts

A weft is whoever you talk to, by name, and owns its settings. Providers are bindings:
a weft names the provider and model that answer it, and either may be `null`. Deleting a
provider unbinds its wefts (`provider_id` becomes `null`; `model_id` is kept, so a client
can say what it was on) and touches nothing else about them.

| route | what it does |
|---|---|
| `GET /api/wefts` | every weft, sorted by name. This is the Pick menu. No secret is opened and no outbound call is made. |
| `POST /api/wefts` | `{name, provider_id?, model_id?}`. **201** with the weft. A name the user already has, compared ignoring case and extra spaces, is a **409** naming it. |
| `GET /api/wefts/{id}` | one weft |
| `PATCH /api/wefts/{id}` | `{name}`: rename. The thread's session id does not change. |
| `DELETE /api/wefts/{id}` | **204**. The weft and everything it owns; its picture goes too once nothing else wears it. A thread in a session store stays in the store. |
| `PUT /api/wefts/{id}/engine` | `{provider_id, model_id}`, both or neither (`{}` unbinds). Only a `kind: "llm"` provider can be an engine (**409** otherwise). |
| `PUT /api/wefts/{id}/history` | see Session stores |
| `PUT /api/wefts/{id}/colours` | `{colour_a, colour_b}`: `#rrggbb` both, or both `null` for the theme's |
| `PUT /api/wefts/{id}/headers` | `{headers: [{name, value}]}`: extra request headers, replaced as a list |
| `PUT /api/wefts/{id}/voice` | `{tts_provider_id, tts_model, tts_voice}`: all three, or none to silence it |

Every one of these returns the whole weft, the same shape `GET /api/wefts` lists. Another
user's weft is a **404** on every verb.

`POST /api/chat` takes `{weft_id, messages}`. A weft with no engine is a **409** whose
`detail` says so.

The server has no notion of a *current* weft. Which one a given device is using is
per-device furniture, and Weft holds no sessions — so the client keeps
that choice itself and re-validates it against `GET /api/wefts` on mount.

### Session stores

A provider of `kind: "session"` is a memory-mcp-ce server: its base URL is the server
itself (a trailing `/v1` is tolerated) and its secret is that server's
`SESSION_BEARER_TOKEN`. It has no models; `GET /api/providers/{id}/models` on one is a
**409**.

A weft keeps its thread there when `PUT /api/wefts/{id}/history` sets `history:
"session"` with `session_provider_id` naming the store. The body carries five fields,
all required (nullable ones may be `null`, but must be present):

| field | meaning |
|---|---|
| `history` | `memory`, `local` or `session` |
| `send_turns`, `window_turns` | as before |
| `session_provider_id` | the store; kept when `history` moves away from `session` |
| `session_id` | a session to join, `[A-Za-z0-9._:-]{1,128}`, or `null` for automatic |

Wefts report `session_provider_id`, `session_id` and `session_key`, the id in use. An
automatic id is fixed when the weft is made, `weft.<username>.<weft id>`, the same on
every device, and neither a rename nor a new engine moves it. (Wefts that existed before
wefts were named individuals kept the id they had, built from their provider and model.) A typed id
joins a session as it stands, whoever created it.

The service does the talking, so the browser never holds the store's token:

- **Writing:** after `POST /api/chat` has streamed a reply, the relay appends the user's
  message and the reply (partial, if the stream broke) to the store. If the store
  fails, the reply is still delivered and one `{"error": "This exchange was not saved…"}`
  frame follows it. An empty reply is not kept.
- **Reading:** `GET /api/session/thread?weft_id=` returns
  `{session_key, messages, has_more}`: the last `window_turns` exchanges as
  `{role, content, attachments}` messages. Other kinds of event a harness may have written (tool
  calls, reasoning) are skipped. A session not created yet reads as empty; a store
  that fails is a **502**; a weft that keeps no session is a **409**. It also returns
  `last_seq`, where the session ends.
- **Checking:** `GET /api/session/head?weft_id=` returns
  `{session_key, last_seq}` and reads no events, so it is cheap enough to poll. The web
  client asks every 15 seconds (and on focus) while a store-kept thread is on screen,
  and re-reads `/thread` only when `last_seq` differs from what it last read.

### Background pictures

A weft can have one still picture, painted behind the donut while it is the current
weft. Wefts carry `background`: the picture's hash, or `null` for none.

- **Setting:** `PUT /api/wefts/{id}/background` with the image itself
  as the body and an `image/*` `Content-Type`. JPEG, PNG, WebP and GIF (first frame
  only) are accepted, up to 20 MB and 64 megapixels. SVG and anything that is not an
  image are a **415**; a file that does not decode as one of those formats, or is too
  large in pixels, is a **422**; too large in bytes is a **413**. Returns the weft.
- **What is stored is not what was sent.** The server applies the EXIF orientation,
  shrinks the picture to 2560 px on its long edge, and re-encodes it as WebP, keeping
  alpha. All metadata is dropped, GPS position included. The hash is the SHA-256 of the
  stored WebP, so uploading the same file twice gives the same hash.
- **Removing:** `DELETE /api/wefts/{id}/background`. Returns the weft.
- **Fetching:** `GET /api/wefts/background/{hash}` returns `image/webp` with
  `Cache-Control: private, max-age=31536000, immutable`. It needs the bearer like any
  other route, and answers only for a picture on one of the caller's own wefts;
  any other hash is a **404**. Since an `<img src>` cannot carry the bearer, fetch it
  and display it from a blob URL (`URL.createObjectURL`).

The window covers the stage with the picture and crops the overflow evenly from both
sides: a portrait screen keeps the top and bottom of a square picture, a landscape one
keeps the sides.

### Pictures on a message

A message can carry pictures. Upload each one first, then name it by
hash in the chat request.

- **Uploading:** `PUT /api/attachments` with the image itself as the body and an
  `image/*` `Content-Type`. The same formats, limits and status codes as a background
  picture, and the same re-encode, except the long edge is 2048 px. Returns `{hash}`.
- **Fetching:** `GET /api/attachments/{hash}` returns `image/webp`, cacheable privately
  forever. Pictures are kept per user: any hash the caller did not upload is a **404**.
- **Sending:** a message in `POST /api/chat` may carry
  `attachments: [{hash, name}]`, at most 6. Only the **last** message's pictures go to
  the provider as pictures, as OpenAI `image_url` content parts (WebP `data:` URIs,
  before the text). Every message's text, the last one included, starts with a marker
  such as `[image: cat.png]`, so earlier pictures reach the provider as their marker
  alone, and a message with pictures and no words still has text. A hash the caller
  does not own is a **422**, and nothing is sent upstream.
- **Session stores:** the user's event is written with the marked text and
  `body: {attachments: [{hash, name}]}`, never the picture itself. `/api/session/thread`
  gives each message back with its `attachments` (empty for most) and its text without
  the marker.
- **Retention:** uploaded pictures are not deleted yet. Cleanup settings are future
  work.

### Push to talk

Speech to text uses one provider per user, not per weft. A provider of
`kind: "stt"` is any endpoint that answers the OpenAI `/audio/transcriptions` shape.

- **Settings:** `GET /api/stt` returns `{provider_id, provider_name, model, language,
  send_on_release, tell_weft, available}`. `available` is false until a provider is
  chosen, and the web client hides its microphone until then. `PUT /api/stt` replaces
  the whole block; choosing a provider that is not `stt`, or not yours, is a **409** or
  a **404**. Deleting the provider turns push to talk off.
- **Models:** `GET /api/stt/models?provider_id=` lists what the provider's `/models`
  says, or `[]` when it will not say.
- **Transcribing:** `POST /api/stt/transcribe` with the recording itself as the body
  and its `audio/*` content type (`audio/webm` from Chrome, `audio/mp4` from Safari),
  up to 25 MB. Returns `{text}`. The audio is relayed and never stored. No provider
  chosen is a **409**; a provider that fails is a **502**.
- **Spoken messages:** a message in `POST /api/chat` may carry `spoken: true`. When
  the user's `tell_weft` is on, its text reaches the provider with `[spoken]` in front
  (after any picture markers). Session stores record `body: {spoken: true}` either way,
  and `/api/session/thread` gives the flag back with the marker taken off.

### Notifications

A weft's agent leaves its user a report with the MCP door's `notify` tool.
Only a weft with a door key can send one. A notification waits until it
is dismissed, and dismissing deletes it: there is no separate read state.

- **Counts:** `GET /api/notifications/counts` returns `{wefts: {weft_id: n}}`, leaving
  out wefts with none waiting. It is cheap, and the web client polls it every 15 s and
  on focus.
- **Listing:** `GET /api/notifications` returns every weft's notifications, newest
  first, as `[{id, weft_id, title, body, created_at}]`. Add `?weft=<id>` for one weft's
  only. `body` is plain text and may hold a picture reference, as a reply can. A weft
  that is not yours is a **404**.
- **Dismissing:** `DELETE /api/notifications/{id}` dismisses one (**204**).
  `POST /api/notifications/dismiss` dismisses them all and returns `{dismissed: n}`.
  With `{weft_id}` in the body, only that weft's.
- **Limits:** a weft can send 30 an hour. At most 100 wait per weft, and past that the
  oldest goes. Titles are cut to 120 characters and bodies to 4000.
- **History:** notifications are kept in Weft's own database whatever the weft's
  `history` setting, and are never part of its conversation.

---

## Not implemented yet

Listed so you know what is coming rather than what is missing by accident:

- user management and invite redemption
- the authenticated WebSocket, for server-pushed inbound messages

---

## Regenerating `openapi.json`

```sh
docker exec dev-weft-service python3 -m scripts.export_openapi           # write
docker exec dev-weft-service python3 -m scripts.export_openapi --check   # verify
```

`tests/test_openapi_spec.py` fails when the committed file disagrees with the running
app, so the spec cannot silently go stale. Do not hand-edit it — there is a test for
that too.
