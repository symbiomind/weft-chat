/* Tests for web/js/eliza.js — run under node.
 *
 *   node --test "tests/client/*.test.mjs"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ELIZA, ELIZA_TIP, THINGS_TIP, answers, elizaBeckons, elizaHidden, hideEliza,
} from "../../web/js/eliza.js";

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}

const throwing = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
  removeItem() { throw new Error("blocked"); },
};

describe("hiding ELIZA", () => {
  it("is shown until hidden, and shown again after", () => {
    const storage = memoryStorage();
    assert.equal(elizaHidden(storage), false);
    hideEliza(true, storage);
    assert.equal(elizaHidden(storage), true);
    hideEliza(false, storage);
    assert.equal(elizaHidden(storage), false);
  });

  it("shrugs off storage that throws", () => {
    assert.doesNotThrow(() => hideEliza(true, throwing));
    assert.equal(elizaHidden(throwing), false);
  });
});

describe("answers", () => {
  it("is true for her, with no engine", () => {
    assert.equal(answers(ELIZA), true);
  });

  it("needs both a provider and a model for anyone else", () => {
    assert.equal(answers({ provider_id: "p", model_id: "m" }), true);
    assert.equal(answers({ provider_id: "p", model_id: null }), false);
    assert.equal(answers(null), false);
  });
});

describe("her thread", () => {
  it("is all sent, and always fits in one request", () => {
    // The route takes at most 200 messages; a full window is 2n-1 of them.
    assert.equal(ELIZA.send_turns, 0);
    assert.ok(ELIZA.window_turns * 2 - 1 <= 200);
  });

  it("is kept on the device, so her memory survives a reload", () => {
    assert.equal(ELIZA.history, "local");
  });

  it("is silent", () => {
    assert.equal(ELIZA.has_voice, false);
  });
});

describe("the wordmark's glitch", () => {
  const found = (...ids) => (id) => ids.includes(id);

  it("waits until the second door has been found", () => {
    assert.equal(elizaBeckons(found()), false);
  });

  it("beckons while that door is found and she is not", () => {
    assert.equal(elizaBeckons(found(THINGS_TIP)), true);
  });

  it("stops for good once she is found, whichever way", () => {
    assert.equal(elizaBeckons(found(THINGS_TIP, ELIZA_TIP)), false);
    assert.equal(elizaBeckons(found(ELIZA_TIP)), false);
  });
});
