/* Tests for the pure half of web/js/icons.js — run under node.
 *
 *   node --test tests/client/
 *
 * The icons themselves need a DOM; what the Pick bar SAYS a weft can do does not.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { capabilitiesOf } from "../../web/js/icons.js";

const names = (caps) => caps.map((c) => c.icon);

describe("capabilitiesOf", () => {
  it("shows nothing for no weft, even when push to talk is set up", () => {
    assert.deepEqual(capabilitiesOf(null, { hears: true }), []);
  });

  it("shows a voice only on a weft that has one", () => {
    assert.deepEqual(names(capabilitiesOf({ has_voice: true })), ["voice"]);
    assert.deepEqual(names(capabilitiesOf({ has_voice: false })), []);
  });

  it("shows push to talk on any weft when the user has it", () => {
    assert.deepEqual(names(capabilitiesOf({ has_voice: false }, { hears: true })), ["mic"]);
  });

  it("puts the voice before the mic", () => {
    assert.deepEqual(names(capabilitiesOf({ has_voice: true }, { hears: true })), ["voice", "mic"]);
  });

  it("shows a plug on a weft whose door is set up, between the voice and the mic", () => {
    assert.deepEqual(names(capabilitiesOf({ has_door: true })), ["plug"]);
    assert.deepEqual(
      names(capabilitiesOf({ has_voice: true, has_door: true }, { hears: true })),
      ["voice", "plug", "mic"],
    );
  });

  it("labels every capability, so the icon is never the only way to know", () => {
    for (const cap of capabilitiesOf({ has_voice: true, has_door: true }, { hears: true })) {
      assert.ok(cap.label.length > 0);
    }
  });
});
