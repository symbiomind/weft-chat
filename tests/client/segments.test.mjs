/* Tests for web/js/segments.js — cutting a streaming reply into speakable pieces.
 *
 * The segmenter sees a prefix and can never look ahead, so every case here feeds text
 * in pieces the way the network actually delivers it. A rule that only works when the
 * whole reply arrives at once is not a rule that works.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createSegmenter, forSpeech, speakableSegments } from "../../web/js/segments.js";

/** Feed a whole string one character at a time — the meanest possible chunking. */
function drip(segmenter, text) {
  const out = [];
  for (const char of text) out.push(...segmenter.push(char));
  return out;
}

describe("paragraph cutting", () => {
  it("emits a paragraph as soon as the blank line arrives", () => {
    const s = createSegmenter();

    assert.deepEqual(s.push("First thought."), []);
    assert.deepEqual(s.push("\n\nSecond"), ["First thought."]);
  });

  it("keeps the unfinished tail back", () => {
    const s = createSegmenter();
    s.push("One.\n\nTwo and a bit");

    assert.equal(s.pending.trim(), "Two and a bit");
  });

  it("emits a short paragraph on its own", () => {
    // "Right." alone on a line is a complete thought. The writer put it there.
    const s = createSegmenter();
    assert.deepEqual(s.push("Right.\n\nNow then"), ["Right."]);
  });

  it("handles several paragraphs arriving in one chunk", () => {
    const s = createSegmenter();
    assert.deepEqual(s.push("One.\n\nTwo.\n\nThree."), ["One.", "Two."]);
    assert.deepEqual(s.flush(), ["Three."]);
  });

  it("works the same when the text is dripped one character at a time", () => {
    const s = createSegmenter();
    const got = drip(s, "First thought.\n\nSecond thought.\n\nThird");

    assert.deepEqual(got, ["First thought.", "Second thought."]);
    assert.deepEqual(s.flush(), ["Third"]);
  });

  it("tolerates trailing spaces on the blank line", () => {
    const s = createSegmenter();
    assert.deepEqual(s.push("One.\n   \nTwo"), ["One."]);
  });
});

describe("the sentence fallback", () => {
  const long = "This is a sentence that goes on. ".repeat(20);

  it("cuts at a sentence end when no paragraph ever comes", () => {
    // Plenty of models answer in one unbroken block. Waiting for a blank line that
    // never arrives would mean waiting for the whole reply.
    const s = createSegmenter({ softMax: 100 });
    const got = s.push(long);

    assert.ok(got.length > 0, "expected the fallback to produce something");
    assert.ok(got[0].endsWith("."), `expected a sentence end, got ${JSON.stringify(got[0])}`);
  });

  it("does not cut below the soft maximum", () => {
    const s = createSegmenter({ softMax: 1000 });
    assert.deepEqual(s.push("Short. Also short. Still short."), []);
  });

  it("takes the last complete sentence inside softMax, not the first", () => {
    // One natural chunk rather than a stutter of tiny ones. "Three." ends past
    // softMax, so it starts the next segment rather than stretching this one.
    const s = createSegmenter({ softMax: 10 });
    const got = s.push("One. Two. Three. ");

    assert.deepEqual(got, ["One. Two."]);
  });

  it("takes the first sentence end past softMax when none ends inside it", () => {
    const s = createSegmenter({ softMax: 10 });
    assert.deepEqual(s.push("A rather long opening sentence. Then"), [
      "A rather long opening sentence.",
    ]);
  });

  it("keeps a closing quote with its sentence", () => {
    const s = createSegmenter({ softMax: 10 });
    const got = s.push('He said "stop." Then he left. ');

    assert.ok(got[0].includes('"stop."'), got[0]);
  });
});

describe("the same cuts however the text arrives", () => {
  // A replay cuts the stored reply in one go and asks for each clip by its words. If
  // the live stream had cut anywhere else, every cached clip would be missed.
  const replies = {
    "paragraphs": "Short one.\n\nA second paragraph here. With two sentences.\n\nEnd.",
    "one long run": Array.from({ length: 40 }, (_, i) => `Sentence ${i} goes on a bit.`).join(" "),
    "long run then paragraph":
      "Hello.\n\n" + "Words and words and more words. ".repeat(30) + "\n\nLast bit, no stop",
    "paragraph just past softMax":
      "x".repeat(380) + " end. " + "y".repeat(30) + "\n\nNext.",
    "no sentence ends early": "z".repeat(500) + ". Then a short one. And another.",
    "code in the middle":
      "Look:\n\n```js\nconst a = 1.\n\nconst b = 2. \n```\n\n" + "After. ".repeat(80),
    "quotes and brackets": 'He said "stop." (Then he left.) '.repeat(25),
    "blank line with spaces": "One.\n   \nTwo.\n\t\nThree. ".repeat(20),
  };

  function streamed(text, size) {
    const s = createSegmenter();
    const out = [];
    for (let i = 0; i < text.length; i += size) out.push(...s.push(text.slice(i, i + size)));
    return [...out, ...s.flush()];
  }

  for (const [name, text] of Object.entries(replies)) {
    it(`cuts "${name}" identically at every chunk size`, () => {
      const whole = streamed(text, text.length);
      for (const size of [1, 2, 3, 5, 7, 13, 64, 401]) {
        assert.deepEqual(streamed(text, size), whole, `chunk size ${size}`);
      }
    });
  }

  it("speakableSegments is the stream's segments, tidied for speech", () => {
    const text = replies["long run then paragraph"];
    assert.deepEqual(
      speakableSegments(text),
      streamed(text, 3).map(forSpeech).filter(Boolean),
    );
  });

  it("speakableSegments drops a segment with nothing left to say", () => {
    assert.deepEqual(speakableSegments("Hi.\n\n```\ncode\n```\n\nBye."), ["Hi.", "Bye."]);
  });
});

describe("code fences", () => {
  it("never cuts inside an open fence", () => {
    const s = createSegmenter({ softMax: 20 });
    const got = s.push("Here:\n\n```py\nx = 1\n\ny = 2\n");

    // "Here:" went out before the fence opened; nothing since, because the fence is
    // still open and the blank line inside it is not a paragraph break.
    assert.deepEqual(got, ["Here:"]);
  });

  it("resumes once the fence closes", () => {
    const s = createSegmenter({ softMax: 20 });
    s.push("Here:\n\n```py\nx = 1\n\ny = 2\n");
    const got = s.push("```\n\nAnd that is it.");

    assert.equal(got.length, 1);
    assert.ok(got[0].includes("```"), "the fence should leave with its segment");
  });

  it("flush releases an unterminated fence at the end of a stream", () => {
    const s = createSegmenter();
    s.push("```py\nx = 1\n");

    assert.equal(s.flush().length, 1);
  });
});

describe("flush", () => {
  it("returns the remainder even when it looks unfinished", () => {
    // An unterminated sentence at the end of a reply is still something that was said.
    const s = createSegmenter();
    s.push("Half a thought");

    assert.deepEqual(s.flush(), ["Half a thought"]);
  });

  it("returns nothing when the buffer is empty or blank", () => {
    const s = createSegmenter();
    assert.deepEqual(s.flush(), []);
    s.push("   \n  ");
    assert.deepEqual(s.flush(), []);
  });

  it("empties the buffer so a second flush is silent", () => {
    const s = createSegmenter();
    s.push("Something");
    s.flush();

    assert.deepEqual(s.flush(), []);
  });
});

describe("forSpeech", () => {
  it("removes fenced code entirely", () => {
    assert.equal(forSpeech("Try this:\n```js\nconst x = 1;\n```\nDone."), "Try this: Done.");
  });

  it("keeps the words inside inline code", () => {
    assert.equal(forSpeech("Run `npm test` now"), "Run npm test now");
  });

  it("strips emphasis and heading markers", () => {
    assert.equal(forSpeech("## Title\n**bold** and *italic*"), "Title bold and italic");
  });

  it("strips bullet markers but keeps the items", () => {
    assert.equal(forSpeech("- one\n- two"), "one two");
  });

  it("leaves a lone asterisk in prose alone", () => {
    assert.equal(forSpeech("2 * 3 = 6"), "2 * 3 = 6");
  });

  it("does NOT touch square brackets", () => {
    // Kokoro's inline voice tags live in prose as [voice:zm_yunjian].
    // Stripping them would silently remove a feature.
    assert.equal(
      forSpeech("[voice:zm_yunjian] hello"),
      "[voice:zm_yunjian] hello",
    );
  });

  it("collapses whitespace so a clip does not start with a pause", () => {
    assert.equal(forSpeech("  lots\n\n   of   space  "), "lots of space");
  });
});
