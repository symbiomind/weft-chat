/* Weft — the session: the one module that holds a credential.
 *
 * THE RULE: no other module calls fetch(), and no other module knows a token exists.
 * Everything else asks for apiFetch() and never learns why a request sometimes took
 * 200ms longer than usual.
 *
 * That is not tidiness. Three guarantees the server relies on only hold if exactly one
 * place is doing the work:
 *
 *   1. The shared in-flight refresh promise is only shared if one module owns it.
 *      Refresh tokens rotate, and the server treats a replayed token as theft: it
 *      revokes the whole session family. Two tabs — or two concurrent 401 retries —
 *      each firing their own refresh will present the same token twice and log the
 *      user out. The symptom is random logouts nobody can reproduce, because they
 *      depend on timing.
 *
 *   2. "Re-sync the CSRF token after every token-issuing response" only holds if one
 *      place handles those responses. Login, refresh and password-change each mint a
 *      fresh token; a client that keeps sending the previous one gets 403 on its next
 *      write.
 *
 *   3. Retry-on-401 only works if one place can pause every caller during a refresh.
 *
 * The access token lives in a module-private variable with no export. That does not
 * stop an XSS — it could call apiFetch() itself — but it makes the invariant
 * auditable: grep for who touches the token and the answer is "this file".
 *
 * NO DOM IN HERE. This module is pure transport, which is what lets its logic be
 * tested under node (see tests/client/session.test.mjs). Views listen via subscribe().
 */

/** @typedef {{id:string, username:string, display_name:string|null, role:string, must_change_password:boolean}} User */

const BASE = "/api/auth";
const CSRF_HEADER = "X-Weft-CSRF";

/** Methods the server guards with CSRF. Mirrors app/csrf.py PROTECTED_METHODS. */
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Refresh at this fraction of the token's life. */
const REFRESH_AT = 0.8;

/** Never schedule a refresh closer than this — guards against a pathological TTL. */
const MIN_REFRESH_MS = 5_000;

/**
 * Refresh answers that mean the session is over. Everything else — a proxy's 502, the
 * service's own 503, a 429, no answer at all — means "not now", and the refresh cookie
 * is still good. See refresh().
 */
const SESSION_OVER = new Set([401, 403]);

/** Retry a refresh that failed for a reason that was not the session ending... */
const RETRY_FIRST_MS = 5_000;
/** ...backing off to no slower than this, so recovery is noticed within a minute. */
const RETRY_MAX_MS = 60_000;

// ---- private state. Nothing below is exported. -----------------------------

let accessToken = null;
let csrfToken = null;
/** @type {User|null} */
let currentUser = null;

/** The mutex. See guarantee (1) above — this being a single shared promise IS the fix. */
let refreshPromise = null;

let refreshTimer = null;

/** The next retry's delay after a "not now" refresh. Reset by any success. */
let retryDelay = RETRY_FIRST_MS;

/** @type {Set<(state: SessionState) => void>} */
const listeners = new Set();

/**
 * @typedef {{status:"unknown"|"anonymous"|"active"|"must-change-password", user:User|null}} SessionState
 */

function state() {
  if (!accessToken || !currentUser) return { status: "anonymous", user: null };
  if (currentUser.must_change_password) {
    return { status: "must-change-password", user: currentUser };
  }
  return { status: "active", user: currentUser };
}

/**
 * Hand one listener a state snapshot without letting it take anything else down.
 *
 * A view that throws during render must not stop its siblings being told, or the UI
 * ends up half-updated and the cause is three stack frames away in an unrelated
 * component.
 */
function notify(listener, snapshot) {
  try {
    listener(snapshot);
  } catch (err) {
    console.error("session listener failed", err);
  }
}

function emit() {
  const snapshot = state();
  for (const listener of listeners) notify(listener, snapshot);
}

/** Subscribe to session changes. Returns an unsubscribe function. */
export function subscribe(listener) {
  listeners.add(listener);
  // Guarded like any other delivery. This first call is if anything the MOST likely
  // one to throw — it is the view's initial render, before any state has settled — and
  // leaving it unguarded meant a broken view took down whoever called subscribe().
  notify(listener, state());
  return () => listeners.delete(listener);
}

export function getState() {
  return state();
}

// ---- credential bookkeeping -------------------------------------------------

/**
 * Absorb a token-issuing response body (login / refresh / password change).
 * This is the ONLY place the credential is written.
 */
function adopt(body) {
  accessToken = body.access_token ?? accessToken;
  csrfToken = body.csrf ?? csrfToken;
  if (body.user) currentUser = body.user;
  scheduleRefresh(body.expires_in);
  emit();
}

function clear() {
  accessToken = null;
  currentUser = null;
  retryDelay = RETRY_FIRST_MS;
  cancelScheduledRefresh();
  // csrfToken is deliberately NOT cleared here: the server clears its cookie on logout,
  // and the caller re-bootstraps. Holding a stale value costs nothing; a null one would
  // make the next login attempt fail its CSRF check for no reason.
  emit();
}

function scheduleRefresh(expiresIn) {
  cancelScheduledRefresh();
  if (!expiresIn || expiresIn <= 0) return;

  // expires_in is RELATIVE seconds, deliberately. Never compare a server timestamp
  // against Date.now() — a browser with a skewed clock would refresh far too early or
  // far too late.
  const delay = Math.max(MIN_REFRESH_MS, expiresIn * 1000 * REFRESH_AT);
  refreshTimer = setTimeout(() => {
    refresh().catch(() => {
      /* Already handled: a failed refresh clears the session and notifies listeners. */
    });
  }, delay);
}

/**
 * Try the refresh again later. Shares refreshTimer with scheduleRefresh, so there is
 * only ever one pending, and clear() cancels either.
 */
function scheduleRetry() {
  cancelScheduledRefresh();
  const delay = retryDelay;
  retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
  refreshTimer = setTimeout(() => {
    refresh().catch(() => {
      /* Handled inside: it either ended the session or scheduled the next retry. */
    });
  }, delay);
}

function cancelScheduledRefresh() {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

// ---- HTTP -------------------------------------------------------------------

function headersFor(method, extra) {
  const headers = new Headers(extra || {});
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (csrfToken && WRITE_METHODS.has(method)) headers.set(CSRF_HEADER, csrfToken);
  return headers;
}

async function raw(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  let response;
  try {
    response = await fetch(path, {
      ...options,
      method,
      headers: headersFor(method, options.headers),
      // The refresh token is an httpOnly cookie; without this it is simply not sent.
      credentials: "same-origin",
    });
  } catch (err) {
    // An abort is the caller changing its mind, not the service going away.
    if (err?.name !== "AbortError") noticeUnreachable();
    throw err;
  }
  // What a reverse proxy answers when the service behind it is not there.
  if (response.status === 502 || response.status === 504) noticeUnreachable();
  return response;
}

/** Parse a JSON body, tolerating an empty one. */
async function json(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

export class ApiError extends Error {
  constructor(status, detail, retryAfter) {
    super(detail || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

async function toError(response) {
  const body = await json(response);
  const retryAfter = Number(response.headers.get("Retry-After")) || undefined;
  return new ApiError(response.status, body.detail, retryAfter);
}

// ---- the public surface -----------------------------------------------------

/**
 * Fetch an API path with the credential attached, refreshing once on a 401.
 *
 * Every module that talks to the server goes through here.
 */
export async function apiFetch(path, options = {}) {
  let response = await raw(path, options);

  if (response.status === 401 && accessToken) {
    // The access token expired between our scheduled refresh and now (a backgrounded
    // tab throttles timers, so this is normal, not exceptional). Refresh once, retry
    // once. Never loop: if the retry also 401s, the session is genuinely over.
    try {
      await refresh();
    } catch {
      throw await toError(response);
    }
    response = await raw(path, options);
  }

  if (!response.ok) throw await toError(response);
  return json(response);
}

/**
 * Fetch a path that answers with binary — audio, today.
 *
 * Here rather than in the caller for the same reason as everything else in this file:
 * it is the credential, the 401 recovery and the CSRF header, and those live in exactly
 * one place. The only difference from apiFetch is what comes back.
 */
export async function apiBlob(path, options = {}) {
  let response = await raw(path, options);

  if (response.status === 401 && accessToken) {
    try {
      await refresh();
    } catch {
      throw await toError(response);
    }
    response = await raw(path, options);
  }

  if (!response.ok) throw await toError(response);
  return response.blob();
}

/** The sentinel the server sends to close a stream. Not a value any frame carries. */
const DONE = Symbol("done");

/** Frame boundary. Tolerates CRLF, which some proxies introduce. */
const FRAME_BOUNDARY = /\r?\n\r?\n/;

/**
 * Decode one SSE frame into the object it carried, or null if it carried nothing.
 *
 * Multi-line `data:` fields are joined with a newline, per the SSE spec. Our server
 * never sends one — json.dumps escapes newlines — but a parser that silently drops the
 * tail of a frame is the kind of bug that only appears once a model writes a long
 * answer, which is exactly when nobody wants to be debugging the transport.
 */
function parseFrame(frame) {
  const data = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (data.length === 0) return null;      // a comment or a keep-alive

  const payload = data.join("\n");
  if (payload === "[DONE]") return DONE;
  try {
    return JSON.parse(payload);
  } catch {
    console.warn("discarded an unparseable stream frame");
    return null;
  }
}

/**
 * Stream an API path, yielding each decoded frame as it arrives.
 *
 * The streaming twin of apiFetch, and it lives here for the same reason: this is still
 * the only module that calls fetch(), holds the access token, or knows how to recover
 * from a 401. A view that reached for fetch() directly to get streaming would quietly
 * opt out of all three.
 *
 * Retry-on-401 works because the request body is a string we still hold — nothing has
 * been consumed at that point. Once the stream has begun there is no retry available
 * and none is attempted: the server reports a mid-stream failure inside the stream,
 * because by then the status code is long gone.
 */
export async function* apiStream(path, options = {}) {
  let response = await raw(path, options);

  if (response.status === 401 && accessToken) {
    try {
      await refresh();
    } catch {
      throw await toError(response);
    }
    response = await raw(path, options);
  }

  if (!response.ok) throw await toError(response);
  if (!response.body) {
    throw new ApiError(0, "This browser cannot read streaming responses.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      // stream: true — a multi-byte character can straddle two network chunks, and
      // decoding them independently would put a replacement character in the middle
      // of somebody's em dash.
      buffer += decoder.decode(value, { stream: true });

      let match;
      while ((match = FRAME_BOUNDARY.exec(buffer)) !== null) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);

        const payload = parseFrame(frame);
        if (payload === DONE) return;
        if (payload) yield payload;
      }
    }
  } finally {
    // Whether we finished, threw, or the consumer broke out of its for-await, the
    // socket has to go back. Without this, abandoning a stream leaks the connection
    // until the tab closes.
    reader.cancel().catch(() => {});
  }
}

/**
 * Fetch a CSRF token. Needed before the first write, including login itself —
 * login is a protected POST and happens before any session exists.
 */
export async function bootstrapCsrf() {
  const response = await fetch(`${BASE}/csrf`, { credentials: "same-origin" });
  if (!response.ok) throw await toError(response);
  const body = await json(response);
  csrfToken = body.csrf;
  return csrfToken;
}

export async function login(username, password) {
  if (!csrfToken) await bootstrapCsrf();

  const response = await raw(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) throw await toError(response);
  adopt(await json(response));
  return state();
}

/**
 * Rotate the refresh cookie and mint a new access token.
 *
 * THE MUTEX. Every caller awaits the same in-flight request. Firing two concurrent
 * refreshes would present the rotated token twice, which the server correctly reads as
 * theft and answers by revoking the entire session family.
 */
export function refresh() {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    let response;
    try {
      response = await raw(`${BASE}/refresh`, { method: "POST" });
    } catch (err) {
      // No answer at all. Nothing was rejected; the cookie is still good.
      scheduleRetry();
      throw err;
    }

    if (!response.ok) {
      if (SESSION_OVER.has(response.status)) {
        // A 401 here means "go to login", NOT "retry". The server has already cleared
        // the cookie; retrying presents a token it has rejected, and if the rejection
        // was reuse detection then every retry looks like a fresh attack in its logs.
        // A 403 is a CSRF refusal, and the same token would only be refused again.
        clear();
        throw await toError(response);
      }
      // ANY OTHER FAILURE IS "NOT NOW", NOT "NO". The usual one is a reverse proxy
      // answering 502 for a service that is restarting — it never saw the cookie, let
      // alone rejected it. Treating that as a rejection logged the user out of a
      // session that was fine, and they came back to a login screen under a green
      // light. Keep the session and try again; the server is the only one who can
      // end it, and it says so with a 401.
      scheduleRetry();
      throw await toError(response);
    }

    retryDelay = RETRY_FIRST_MS;
    adopt(await json(response));
    return state();
  })().finally(() => {
    refreshPromise = null;
  });

  return refreshPromise;
}

/**
 * Re-establish a session on page load, from the refresh cookie alone.
 *
 * A reload loses the in-memory access token but not the httpOnly cookie, so this is
 * what makes "still logged in after F5" work. A failure here is the normal anonymous
 * case, not an error worth showing anyone.
 */
export async function resume() {
  try {
    // CSRF FIRST. /refresh is a protected POST, so without a token it is rejected 403
    // before the cookie is ever looked at.
    //
    // On a cold load this is obviously required — module state is empty. The case that
    // makes it load-bearing is the warm one: the CSRF cookie lives 24 hours and the
    // refresh cookie lives 30 days, so a user returning the next day has a perfectly
    // valid session and a stale CSRF token. Refreshing first would 403, resume() would
    // fall back to anonymous, and they would be bounced to the login screen every day
    // while believing they were logged in for a month.
    //
    // Not a wasted request either: the login view and every later write need a token,
    // so this is the fetch that would have happened anyway.
    await bootstrapCsrf();
    await refresh();
  } catch {
    clear();
  }
  return state();
}

export async function logout() {
  try {
    await raw(`${BASE}/logout`, { method: "POST" });
  } catch {
    // Logging out must not be able to fail from the user's point of view. Whatever
    // happened on the wire, this device is done.
  }
  clear();
  // The server clears the CSRF cookie too, so the next write needs a fresh one.
  try {
    await bootstrapCsrf();
  } catch {
    /* the login view will retry */
  }
}

export async function logoutEverywhere() {
  await apiFetch(`${BASE}/logout-all`, { method: "POST" });
  clear();
  try {
    await bootstrapCsrf();
  } catch {
    /* the login view will retry */
  }
}

/** Every device this account is signed in on, this one first. */
export function listSessions() {
  return apiFetch(`${BASE}/sessions`);
}

/**
 * Sign one other device out. For this device use logout(), which also tidies up here;
 * the server would take this device's id too, but the client would not know it had left.
 */
export function endSession(id) {
  return apiFetch(`${BASE}/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function changePassword(currentPassword, newPassword) {
  const body = await apiFetch(`${BASE}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });

  // The server ends every OTHER session and hands this device a fresh pair, so the
  // user stays logged in for successfully securing their account.
  if (currentUser) currentUser = { ...currentUser, must_change_password: false };
  adopt(body);
  return state();
}

export async function me() {
  return apiFetch(`${BASE}/me`);
}

// ---- reachability -------------------------------------------------------------

/** How long /health gets before the service counts as not there. */
const PING_TIMEOUT_MS = 5_000;

/** Subsystems /health reports. Anything other than "ok" in one of them is degraded. */
const HEALTH_PARTS = ["db", "auth", "csrf", "vault"];

/** @type {Set<() => void>} */
const unreachableListeners = new Set();

function noticeUnreachable() {
  for (const listener of unreachableListeners) {
    try {
      listener();
    } catch (err) {
      console.error("reachability listener failed", err);
    }
  }
}

/**
 * Be told when a request could not reach the service — a network failure, or a proxy
 * answering for it. A hint, not a verdict: the listener should ping() to find out.
 * Returns the unsubscribe.
 */
export function onUnreachable(listener) {
  unreachableListeners.add(listener);
  return () => unreachableListeners.delete(listener);
}

/**
 * Ask the service whether it is up: "up", "degraded" or "down". Never throws.
 *
 * Here because this is the module that calls fetch(). /health is unauthenticated, so
 * no credential goes with it — it is a plain GET that cannot cost a session anything.
 *
 * "degraded" is the service answering while a subsystem it reports is not "ok": it can
 * serve the page but some routes will 503. "down" is no answer, a timeout, or anything
 * but a 2xx — a proxy's 502 included.
 */
export async function ping() {
  let response;
  try {
    response = await fetch("/health", {
      cache: "no-store",
      credentials: "omit",
      signal: AbortSignal.timeout(PING_TIMEOUT_MS),
    });
  } catch {
    return "down";
  }
  if (!response.ok) return "down";

  const body = await json(response);
  if (body.status !== "ok") return "down";
  const whole = body.config_loaded !== false
    && HEALTH_PARTS.every((part) => body[part] === undefined || body[part] === "ok");
  return whole ? "up" : "degraded";
}

/**
 * Test seam. Only tests/client/session.test.mjs should call this — it exists so the
 * module can be reset between cases without a fresh import each time.
 */
export function __reset() {
  accessToken = null;
  csrfToken = null;
  currentUser = null;
  refreshPromise = null;
  retryDelay = RETRY_FIRST_MS;
  cancelScheduledRefresh();
  listeners.clear();
  unreachableListeners.clear();
}
