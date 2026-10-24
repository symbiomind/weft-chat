/* Tests for web/js/health.js and session.ping() — run under node.
 *
 *   node --test "tests/client/*.test.mjs"
 *
 * The LED is only worth having if it tells the truth, and the two ways it lies are
 * both silent: staying green while every request fails, or flashing red because one
 * request was aborted on purpose. These pin both.
 */

import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { watchHealth } from "../../web/js/health.js";
import * as session from "../../web/js/session.js";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const HEALTHY = {
  status: "ok",
  config_loaded: true,
  auth: "ok",
  csrf: "ok",
  vault: "ok",
  db: "ok",
};

/** Route /health to `health()`, everything else to `other()`, counting health calls. */
function stubFetch({ health, other = () => jsonResponse({}) }) {
  const seen = { health: 0 };
  globalThis.fetch = async (url, options = {}) => {
    if (url === "/health") {
      seen.health += 1;
      return health(options);
    }
    return other(url, options);
  };
  return seen;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

let watcher = null;
afterEach(() => {
  watcher?.stop();
  watcher = null;
  session.__reset();
});

describe("ping", () => {
  it("is up when every subsystem says ok", async () => {
    stubFetch({ health: () => jsonResponse(HEALTHY) });
    assert.equal(await session.ping(), "up");
  });

  it("is degraded when the service answers but a subsystem does not", async () => {
    stubFetch({ health: () => jsonResponse({ ...HEALTHY, db: "unreachable" }) });
    assert.equal(await session.ping(), "degraded");
  });

  it("is degraded when config failed to load", async () => {
    stubFetch({ health: () => jsonResponse({ ...HEALTHY, config_loaded: false }) });
    assert.equal(await session.ping(), "degraded");
  });

  it("is down when nothing answers", async () => {
    stubFetch({ health: () => { throw new TypeError("Failed to fetch"); } });
    assert.equal(await session.ping(), "down");
  });

  it("is down when a proxy answers for a missing service", async () => {
    stubFetch({ health: () => new Response("<html>Bad Gateway</html>", { status: 502 }) });
    assert.equal(await session.ping(), "down");
  });

  it("sends no credential", async () => {
    let options = null;
    stubFetch({ health: (o) => { options = o; return jsonResponse(HEALTHY); } });
    await session.ping();
    assert.equal(options.credentials, "omit");
    assert.equal(options.headers, undefined);
  });
});

describe("watchHealth", () => {
  it("reports the first answer, then only changes", async () => {
    let answer = HEALTHY;
    stubFetch({ health: () => jsonResponse(answer) });
    const seen = [];
    watcher = watchHealth((s) => seen.push(s));

    await watcher.check();
    await watcher.check();
    answer = { ...HEALTHY, db: "unreachable" };
    await watcher.check();
    await watcher.check();

    assert.deepEqual(seen, ["up", "degraded"]);
  });

  it("checks at once when a real request fails to arrive", async () => {
    let up = true;
    const seen = stubFetch({
      health: () => {
        if (!up) throw new TypeError("Failed to fetch");
        return jsonResponse(HEALTHY);
      },
      other: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const statuses = [];
    watcher = watchHealth((s) => statuses.push(s));
    await watcher.check();
    const before = seen.health;

    up = false;
    await assert.rejects(session.apiFetch("/api/wefts"));
    await tick();

    assert.ok(seen.health > before, "the failed request should have prompted a /health check");
    assert.deepEqual(statuses, ["up", "down"]);
  });

  it("checks at once when a proxy answers 502 for a request", async () => {
    const seen = stubFetch({
      health: () => jsonResponse(HEALTHY),
      other: () => new Response("Bad Gateway", { status: 502 }),
    });
    watcher = watchHealth(() => {});
    await watcher.check();
    const before = seen.health;

    await assert.rejects(session.apiFetch("/api/wefts"));
    await tick();

    assert.ok(seen.health > before);
  });

  it("does not treat an aborted request as the service going away", async () => {
    const seen = stubFetch({
      health: () => jsonResponse(HEALTHY),
      other: () => {
        throw new DOMException("The user aborted a request.", "AbortError");
      },
    });
    watcher = watchHealth(() => {});
    await watcher.check();
    const before = seen.health;

    await assert.rejects(session.apiFetch("/api/chat"));
    await tick();

    assert.equal(seen.health, before);
  });

  it("shares one request between concurrent checks", async () => {
    const seen = stubFetch({ health: () => jsonResponse(HEALTHY) });
    watcher = watchHealth(() => {});
    await Promise.all([watcher.check(), watcher.check(), watcher.check()]);
    assert.equal(seen.health, 1);
  });

  it("polls faster while the service is down", async () => {
    let up = false;
    const seen = stubFetch({
      health: () => {
        if (!up) throw new TypeError("Failed to fetch");
        return jsonResponse(HEALTHY);
      },
    });
    const statuses = [];
    watcher = watchHealth((s) => statuses.push(s), { upEvery: 10_000, downEvery: 10 });
    await watcher.check();
    up = true;
    await new Promise((resolve) => setTimeout(resolve, 40));

    assert.ok(seen.health >= 2);
    assert.deepEqual(statuses, ["down", "up"]);
  });

  it("goes quiet when stopped", async () => {
    const seen = stubFetch({ health: () => jsonResponse(HEALTHY) });
    const statuses = [];
    watcher = watchHealth((s) => statuses.push(s), { upEvery: 5, downEvery: 5 });
    await watcher.check();
    watcher.stop();
    const after = seen.health;
    await new Promise((resolve) => setTimeout(resolve, 30));

    assert.equal(seen.health, after);
    assert.deepEqual(statuses, ["up"]);
  });
});
