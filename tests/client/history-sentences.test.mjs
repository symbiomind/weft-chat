/* Tests for the one claim the history dials rest on: the beginner's sentence and the
 * Advanced number are the SAME VALUE, seen twice.
 *
 * Before this, `send` was an enum. Adding a "how many turns" dial beside it would have
 * made two fields that can disagree — and then something has to arbitrate, and whatever
 * it decides, it is ignoring what somebody just typed. Making the number the value and
 * the sentence a rendering of it removes the arbitration rather than getting it right.
 *
 * These tests are what stops that collapsing back into two fields by accident. If a
 * sentence ever describes a number other than the one it carries, the panel has started
 * lying about where somebody's words go and how many of them are being sent.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { sendSentence, sendOptionValues } from "../../web/js/views/weft.js";

describe("sendSentence", () => {
  it("says what the default actually does", () => {
    assert.equal(sendSentence(20), "the last 20 turns");
  });

  it("does not say 'everything', because the window makes that untrue", () => {
    // The old sentence was "everything — the whole thread". Once the window has a size,
    // the whole thread is not a thing anybody can send: turns that have fallen out of
    // it are gone from the array too. What you can send is everything still held.
    const sentence = sendSentence(0);
    assert.ok(!/whole thread/.test(sentence), sentence);
    assert.equal(sentence, "everything this window keeps");
  });

  it("has a sentence for the gateway case", () => {
    // 1 is what `last` became: the setting for a far end that owns the session itself.
    assert.equal(sendSentence(1), "this turn only");
  });

  it("describes a number Advanced invented, rather than rounding it to a preset", () => {
    assert.equal(sendSentence(7), "the last 7 turns");
    assert.equal(sendSentence(500), "the last 500 turns");
  });
});

describe("sendOptionValues", () => {
  it("offers two sane entries and nothing else", () => {
    assert.deepEqual(sendOptionValues(20), [20, 1]);
    assert.deepEqual(sendOptionValues(1), [20, 1]);
  });

  it("does not offer 'everything' to somebody who did not ask for it", () => {
    // Reachable by typing 0 into Advanced, and only that way. It is the expensive
    // answer, and the one that used to be the default by accident.
    assert.ok(!sendOptionValues(20).includes(0));
  });

  it("grows an entry for whatever Advanced is actually holding", () => {
    // The list must always be able to describe the truth. A dropdown with no entry for
    // the current value would show the wrong sentence, or none.
    assert.deepEqual(sendOptionValues(0), [0, 20, 1]);
    assert.deepEqual(sendOptionValues(7), [7, 20, 1]);
  });

  it("always contains the current value, so the select can never be blank", () => {
    for (const turns of [0, 1, 2, 7, 20, 21, 199, 10_000]) {
      assert.ok(sendOptionValues(turns).includes(turns), `missing ${turns}`);
    }
  });
});
