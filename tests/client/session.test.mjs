/* Tests for web/js/session.js — run under node, not pytest.
 *
 *   node --test tests/client/
 *
 * The server suite runs inside the container (that is where python lives); this runs
 * on the host (that is where node lives). Two runtimes, two suites, each where it
 * belongs.
 *
 * WHY THIS FILE EXISTS. One invariant in the client fails silently and produces a bug
 * nobody can reproduce: if two callers each fire their own refresh, both present the
 * same rotated token, the server correctly reads the second as a replay, and it
 * revokes the whole session family. The user gets logged out at random, depending on
 * tab timing. `refresh coalesces concurrent callers` is the guard.
 *
 * session.js is deliberately DOM-free, which is what makes this possible — it needs
 * only fetch(), and node has one to stub.
 */

import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import * as session from "../../web/js/session.js";

// ---- a stub fetch that records what it was asked -----------------------------

function stubFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options, method: (options.method || "GET").toUpperCase() });
    return handler(url, options, calls.length);
  };
  return calls;
}

function jsonResponse(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const TOKENS = (n = 1) => ({
  access_token: `access-${n}`,
  expires_in: 900,
  csrf: `csrf-${n}`,
  user: {
    id: "u1",
    username: "alice",
    display_name: "alice",
    role: "user",
    must_change_password: false,
  },
});

/** Let queued microtasks/timers settle. */
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  session.__reset();
});

// ---- THE test ----------------------------------------------------------------

describe("refresh coalescing", () => {
  it("coalesces concurrent callers into ONE request", async () => {
    const calls = stubFetch((url, _o, n) => {
      if (url.endsWith("/refresh")) return jsonResponse(TOKENS(n));
      return jsonResponse({});
    });

    // Five callers race, as five components mounting at once would.
    const results = await Promise.all([
      session.refresh(),
      session.refresh(),
      session.refresh(),
      session.refresh(),
      session.refresh(),
    ]);

    const refreshCalls = calls.filter((c) => c.url.endsWith("/refresh"));
    assert.equal(
      refreshCalls.length,
      1,
      "five concurrent refreshes must hit the wire ONCE — presenting a rotated token " +
        "twice is what the server treats as theft, and it revokes the whole family",
    );
    // And every caller gets the same answer.
    assert.equal(new Set(results.map((r) => r.user.id)).size, 1);
  });

  it("allows a NEW refresh once the previous one has settled", async () => {
    const calls = stubFetch((url, _o, n) => jsonResponse(TOKENS(n)));

    await session.refresh();
    await session.refresh();

    assert.equal(
      calls.filter((c) => c.url.endsWith("/refresh")).length,
      2,
      "the mutex must release after settling, or the session can never be renewed",
    );
  });

  it("releases the mutex even when the refresh fails", async () => {
    let attempt = 0;
    stubFetch((url) => {
      if (!url.endsWith("/refresh")) return jsonResponse({});
      attempt += 1;
      return attempt === 1
        ? jsonResponse({ detail: "Session expired." }, 401)
        : jsonResponse(TOKENS(2));
    });

    await assert.rejects(() => session.refresh());
    // A stuck mutex after a failure would wedge the client permanently: every later
    // refresh would await a promise that already rejected.
    await session.refresh();
    assert.equal(attempt, 2);
  });
});

// ---- "not now" is not "no" ---------------------------------------------------
//
// THE BUG THIS GUARDS. A reverse proxy answers 502 for a service that is restarting.
// refresh() used to read any failure as the server rejecting the cookie, so a refresh
// that landed during a restart logged the user out of a session that was fine. Only
// the server can end a session, and it says so with a 401.

describe("refresh during an outage", () => {
  // tick() is a setTimeout, and these tests mock setTimeout, so it would never fire.
  // setImmediate is left real, and runs after every pending microtask.
  const settle = () => new Promise((resolve) => setImmediate(resolve));

  /** Log in via refresh, then make /refresh answer with `next()` from now on. */
  async function activeThen(next) {
    let outage = false;
    const calls = stubFetch((url) => {
      if (!url.endsWith("/refresh")) return jsonResponse({});
      return outage ? next() : jsonResponse(TOKENS());
    });
    await session.refresh();
    assert.equal(session.getState().status, "active");
    outage = true;
    return {
      calls,
      recover() {
        outage = false;
      },
      refreshes: () => calls.filter((c) => c.url.endsWith("/refresh")).length,
    };
  }

  for (const [label, next] of [
    ["a proxy's 502", () => new Response("Bad Gateway", { status: 502 })],
    ["a proxy's 504", () => new Response("Gateway Timeout", { status: 504 })],
    ["the service's own 503", () => jsonResponse({ detail: "Database unavailable" }, 503)],
    ["a 429", () => jsonResponse({ detail: "Slow down" }, 429)],
    ["no answer at all", () => { throw new TypeError("Failed to fetch"); }],
  ]) {
    it(`stays logged in through ${label}`, async (t) => {
      t.mock.timers.enable({ apis: ["setTimeout"] });
      await activeThen(next);
      const seen = [];
      session.subscribe((s) => seen.push(s.status));

      await assert.rejects(() => session.refresh());

      assert.equal(session.getState().status, "active");
      assert.ok(!seen.includes("anonymous"), "nothing should have been told the session ended");
    });
  }

  it("retries on its own, and recovers when the service is back", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const outage = await activeThen(() => new Response("Bad Gateway", { status: 502 }));

    await assert.rejects(() => session.refresh());
    const afterFailure = outage.refreshes();

    outage.recover();
    t.mock.timers.tick(5_000);
    await settle();
    await settle();

    assert.equal(outage.refreshes(), afterFailure + 1, "a retry should have fired");
    assert.equal(session.getState().status, "active");
  });

  it("backs off between retries while the outage lasts", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const outage = await activeThen(() => new Response("Bad Gateway", { status: 502 }));

    await assert.rejects(() => session.refresh());
    const start = outage.refreshes();

    t.mock.timers.tick(5_000);   // first retry: 5s
    await settle();
    assert.equal(outage.refreshes(), start + 1);

    t.mock.timers.tick(5_000);   // the second waits 10s, so nothing yet
    await settle();
    assert.equal(outage.refreshes(), start + 1);

    t.mock.timers.tick(5_000);
    await settle();
    assert.equal(outage.refreshes(), start + 2);
    assert.equal(session.getState().status, "active");
  });

  it("still logs out on a 401 — the server really did say no", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const outage = await activeThen(() => jsonResponse({ detail: "Session expired." }, 401));

    await assert.rejects(() => session.refresh());
    assert.equal(session.getState().status, "anonymous");

    // And does not keep presenting a cookie the server has rejected.
    const after = outage.refreshes();
    t.mock.timers.tick(120_000);
    await settle();
    assert.equal(outage.refreshes(), after);
  });

  it("still logs out on a 403", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    await activeThen(() => jsonResponse({ detail: "CSRF validation failed" }, 403));

    await assert.rejects(() => session.refresh());
    assert.equal(session.getState().status, "anonymous");
  });

  it("stops retrying once the user logs out", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const outage = await activeThen(() => new Response("Bad Gateway", { status: 502 }));

    await assert.rejects(() => session.refresh());
    await session.logout();
    const after = outage.refreshes();

    t.mock.timers.tick(120_000);
    await settle();
    assert.equal(outage.refreshes(), after);
  });
});

// ---- credential handling ------------------------------------------------------

describe("login", () => {
  it("bootstraps a CSRF token before the first write", async () => {
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "csrf-boot" });
      return jsonResponse(TOKENS());
    });

    await session.login("alice", "password");

    assert.equal(calls[0].url, "/api/auth/csrf", "CSRF must be fetched first");
    assert.equal(calls[1].options.headers.get("X-Weft-CSRF"), "csrf-boot");
  });

  it("sends the credential on subsequent requests", async () => {
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS(7));
      return jsonResponse({ ok: true });
    });

    await session.login("alice", "password");
    await session.apiFetch("/api/auth/me");

    const meCall = calls.at(-1);
    assert.equal(meCall.options.headers.get("Authorization"), "Bearer access-7");
  });

  it("re-syncs the CSRF token from every token-issuing response", async () => {
    // The server rotates it on login/refresh/password-change. A client that keeps the
    // old one gets 403 on its next write, which looks like a permissions bug.
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "stale" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS(1)); // csrf-1
      return jsonResponse({ ok: true });
    });

    await session.login("alice", "password");
    await session.apiFetch("/api/auth/logout", { method: "POST" });

    assert.equal(calls.at(-1).options.headers.get("X-Weft-CSRF"), "csrf-1");
  });

  it("does not attach a CSRF header to reads", async () => {
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS());
      return jsonResponse({});
    });

    await session.login("alice", "password");
    await session.apiFetch("/api/auth/me");

    assert.equal(calls.at(-1).options.headers.get("X-Weft-CSRF"), null);
  });

  it("sends credentials so the httpOnly refresh cookie rides along", async () => {
    const calls = stubFetch(() => jsonResponse({ csrf: "c" }));
    await session.bootstrapCsrf();
    assert.equal(calls[0].options.credentials, "same-origin");
  });
});

// ---- retry-on-401 --------------------------------------------------------------

describe("apiFetch", () => {
  it("refreshes once and retries when a request 401s", async () => {
    let meCalls = 0;
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS(1));
      if (url.endsWith("/refresh")) return jsonResponse(TOKENS(2));
      meCalls += 1;
      return meCalls === 1 ? jsonResponse({ detail: "x" }, 401) : jsonResponse({ id: "u1" });
    });

    await session.login("alice", "password");
    const result = await session.apiFetch("/api/auth/me");

    assert.equal(result.id, "u1");
    assert.equal(meCalls, 2, "should retry exactly once");
    assert.equal(calls.filter((c) => c.url.endsWith("/refresh")).length, 1);
  });

  it("does NOT loop when the retry also 401s", async () => {
    let meCalls = 0;
    stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS(1));
      if (url.endsWith("/refresh")) return jsonResponse(TOKENS(2));
      meCalls += 1;
      return jsonResponse({ detail: "nope" }, 401);
    });

    await session.login("alice", "password");
    await assert.rejects(() => session.apiFetch("/api/auth/me"));
    assert.equal(meCalls, 2, "one original + one retry, then give up");
  });

  it("surfaces Retry-After from a 429 so the UI can honour it", async () => {
    stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      return jsonResponse({ detail: "Too many login attempts." }, 429, { "Retry-After": "900" });
    });

    await assert.rejects(
      () => session.login("alice", "wrong"),
      (err) => err.status === 429 && err.retryAfter === 900,
    );
  });

  it("reports the server's detail message", async () => {
    stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      return jsonResponse({ detail: "Invalid username or password." }, 401);
    });

    await assert.rejects(
      () => session.login("alice", "wrong"),
      (err) => err.detail === "Invalid username or password.",
    );
  });
});

// ---- signed-in devices -----------------------------------------------------------

describe("devices", () => {
  it("signs another device out with a DELETE that carries the CSRF header", async () => {
    const calls = stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS(1));
      return jsonResponse({ status: "signed out" });
    });

    await session.login("alice", "password");
    await session.endSession("a/b");

    const call = calls.at(-1);
    assert.equal(call.method, "DELETE");
    assert.ok(call.url.endsWith("/api/auth/sessions/a%2Fb"), "the id is escaped");
    assert.equal(new Headers(call.options.headers).get("X-Weft-CSRF"), "csrf-1");
    assert.ok(session.getState().user, "signing another device out leaves this one alone");
  });
});

// ---- observable state -----------------------------------------------------------

describe("state", () => {
  it("starts anonymous", () => {
    assert.equal(session.getState().status, "anonymous");
  });

  it("becomes active after login", async () => {
    stubFetch((url) =>
      url.endsWith("/csrf") ? jsonResponse({ csrf: "c" }) : jsonResponse(TOKENS()),
    );
    await session.login("alice", "password");
    assert.equal(session.getState().status, "active");
  });

  it("routes a flagged account to must-change-password, not to the app", async () => {
    stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      const body = TOKENS();
      body.user.must_change_password = true;
      return jsonResponse(body);
    });

    await session.login("admin", "bootstrap-password");
    assert.equal(session.getState().status, "must-change-password");
  });

  it("notifies subscribers on every transition", async () => {
    stubFetch((url) =>
      url.endsWith("/csrf") ? jsonResponse({ csrf: "c" }) : jsonResponse(TOKENS()),
    );

    const seen = [];
    session.subscribe((s) => seen.push(s.status));

    await session.login("alice", "password");
    await tick();

    assert.deepEqual(seen, ["anonymous", "active"]);
  });

  it("keeps the other subscribers when one throws", async () => {
    stubFetch((url) =>
      url.endsWith("/csrf") ? jsonResponse({ csrf: "c" }) : jsonResponse(TOKENS()),
    );

    const seen = [];
    session.subscribe(() => {
      throw new Error("a view blew up");
    });
    session.subscribe((s) => seen.push(s.status));

    await session.login("alice", "password");
    assert.ok(seen.includes("active"), "a broken view must not freeze the rest of the UI");
  });

  it("goes anonymous when resume finds no valid cookie", async () => {
    stubFetch(() => jsonResponse({ detail: "Session expired." }, 401));
    const result = await session.resume();
    assert.equal(result.status, "anonymous");
  });

  it("restores a session from the cookie alone on reload", async () => {
    stubFetch((url) =>
      url.endsWith("/refresh") ? jsonResponse(TOKENS()) : jsonResponse({ csrf: "c" }),
    );
    const result = await session.resume();
    assert.equal(result.status, "active", "F5 must not log the user out");
  });

  it("fetches CSRF before refreshing on resume", async () => {
    // The bug this guards: /refresh is a protected POST, and the CSRF cookie lives 24h
    // while the refresh cookie lives 30 days. Refreshing first means a user returning
    // the next day gets a 403, falls back to anonymous, and is bounced to login every
    // single day despite holding a valid month-long session.
    const calls = stubFetch((url) =>
      url.endsWith("/refresh") ? jsonResponse(TOKENS()) : jsonResponse({ csrf: "c" }),
    );

    await session.resume();

    assert.ok(calls[0].url.endsWith("/csrf"), `expected /csrf first, got ${calls[0].url}`);
    assert.ok(calls[1].url.endsWith("/refresh"));
    assert.equal(calls[1].options.headers.get("X-Weft-CSRF"), "c");
  });

  it("logout clears the session even when the request fails", async () => {
    stubFetch((url) => {
      if (url.endsWith("/csrf")) return jsonResponse({ csrf: "c" });
      if (url.endsWith("/login")) return jsonResponse(TOKENS());
      throw new Error("network is down");
    });

    await session.login("alice", "password");
    await session.logout();

    assert.equal(
      session.getState().status,
      "anonymous",
      "whatever happened on the wire, this device is done",
    );
  });
});
