/* Tests for the pure half of web/js/icons.js — run under node.
 *
 *   node --test "tests/client/*.test.mjs"
 *
 * The icons themselves need a DOM; what the Pick bar SAYS a weft can do does not.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { capabilitiesOf } from "../../web/js/icons.js";

const names = (caps) => caps.map((c) => c.icon);
/** A weft with an engine. */
const bound = (extra = {}) => ({ provider_id: "p", model_id: "m", ...extra });

describe("capabilitiesOf", () => {
  it("shows nothing for no weft, even when push to talk is set up", () => {
    assert.deepEqual(capabilitiesOf(null, { hears: true }), []);
  });

  it("shows a voice only on a weft that has one", () => {
    assert.deepEqual(names(capabilitiesOf(bound({ has_voice: true }))), ["voice"]);
    assert.deepEqual(names(capabilitiesOf(bound({ has_voice: false }))), []);
  });

  it("shows push to talk on any weft when the user has it", () => {
    assert.deepEqual(names(capabilitiesOf(bound(), { hears: true })), ["mic"]);
  });

  it("puts the voice before the mic", () => {
    assert.deepEqual(names(capabilitiesOf(bound({ has_voice: true }), { hears: true })), ["voice", "mic"]);
  });

  it("shows a plug on a weft whose door is set up, between the voice and the mic", () => {
    assert.deepEqual(names(capabilitiesOf(bound({ has_door: true }))), ["plug"]);
    assert.deepEqual(
      names(capabilitiesOf(bound({ has_voice: true, has_door: true }), { hears: true })),
      ["voice", "plug", "mic"],
    );
  });

  it("shows ⊘ for no engine, then the plug if its door is open, and nothing else", () => {
    const hollow = { has_voice: true, has_door: true };
    assert.deepEqual(names(capabilitiesOf(hollow, { hears: true })), ["no-entry", "plug"]);
    assert.deepEqual(names(capabilitiesOf({ has_voice: true }, { hears: true })), ["no-entry"]);
    assert.deepEqual(names(capabilitiesOf({ builtin: true }, { hears: true })), ["mic"]);
  });

  it("labels every capability, so the icon is never the only way to know", () => {
    for (const cap of [
      ...capabilitiesOf(bound({ has_voice: true, has_door: true }), { hears: true }),
      ...capabilitiesOf({ has_door: true }),
    ]) {
      assert.ok(cap.label.length > 0);
    }
  });
});
