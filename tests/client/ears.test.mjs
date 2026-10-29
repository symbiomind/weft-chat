/* Tests for which ears a pick hears you with.
 *
 * The account's provider unless a weft chose its own or none. The built-in pick never
 * hears. A weft whose own provider was deleted hears nothing: falling back to the
 * account's would compare the usual server with itself.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { earsOf } from "../../web/js/ptt.js";

const ACCOUNT = { available: true, provider_id: "whisper" };
const NONE = { available: false, provider_id: null };

describe("earsOf", () => {
  it("hears with the account by default", () => {
    assert.equal(earsOf({ stt_mode: "account" }, ACCOUNT), "whisper");
  });

  it("treats a weft from before ears existed as the account's", () => {
    assert.equal(earsOf({}, ACCOUNT), "whisper");
  });

  it("hears nothing when the account has no provider", () => {
    assert.equal(earsOf({ stt_mode: "account" }, NONE), null);
    assert.equal(earsOf({ stt_mode: "account" }, null), null);
  });

  it("hears with a weft's own provider, account or not", () => {
    const own = { stt_mode: "own", stt_provider_id: "ollama" };
    assert.equal(earsOf(own, ACCOUNT), "ollama");
    assert.equal(earsOf(own, NONE), "ollama");
  });

  it("hears nothing when its own provider was deleted", () => {
    assert.equal(earsOf({ stt_mode: "own", stt_provider_id: null }, ACCOUNT), null);
  });

  it("hears nothing when off", () => {
    assert.equal(earsOf({ stt_mode: "off", stt_provider_id: "ollama" }, ACCOUNT), null);
  });

  it("never hears as the built-in pick", () => {
    assert.equal(earsOf({ builtin: true }, ACCOUNT), null);
  });

  it("hears nothing with no pick", () => {
    assert.equal(earsOf(null, ACCOUNT), null);
  });
});
