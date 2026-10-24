/* Tests for web/js/audiodebug.js — when the audio log turns on, and when it stays on.
 *
 * Only the decision is tested; the panel is DOM and is looked at, not asserted. The
 * decision is the part that matters on a phone: an app on the home screen has no
 * address bar, so `?audiodebug` has to be remembered or it is gone on the first reload.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { decide } from "../../web/js/audiodebug.js";

describe("audio debug flag", () => {
  it("is off with no flag and nothing remembered", () => {
    assert.deepEqual(decide("", false), { on: false, store: null });
  });

  it("turns on from a bare ?audiodebug and asks to remember it", () => {
    assert.deepEqual(decide("?audiodebug", false), { on: true, store: true });
  });

  it("stays on from memory when the address says nothing", () => {
    assert.deepEqual(decide("?other=1", true), { on: true, store: null });
  });

  it("turns off from ?audiodebug=0 even when remembered", () => {
    assert.deepEqual(decide("?audiodebug=0", true), { on: false, store: false });
    assert.deepEqual(decide("?audiodebug=off", true), { on: false, store: false });
  });

  it("treats any other value as on", () => {
    assert.deepEqual(decide("?audiodebug=1", false), { on: true, store: true });
  });
});
