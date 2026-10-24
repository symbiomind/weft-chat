/* Tests for web/js/speaker.js — the synthesise-ahead, play-in-order queue.
 *
 * The point of the module is that two loops run independently: the next paragraph is
 * being turned into audio while the current one is being read aloud. That is exactly
 * the kind of thing that looks right, sounds right most of the time, and reorders
 * itself on a slow network — so the ordering is asserted rather than assumed.
 *
 * The voice is injected, so none of this needs a browser. `playToEnd` here is a promise
 * the test resolves by hand, which is what makes "was segment 2 synthesised while
 * segment 1 was still playing" an answerable question.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createSpeaker } from "../../web/js/speaker.js";

/** Let every already-resolved promise settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * A voice whose playback finishes only when the test says so.
 *
 * `synthesised` records the order text was turned into audio; `played` records the
 * order clips actually started. Those two lists are the whole subject of this file.
 */
function fakeVoice({ failOn = [], blockPlayback = false } = {}) {
  const synthesised = [];
  const played = [];
  let finishCurrent = null;

  return {
    synthesised,
    played,
    /** Resolve the clip that is currently playing. */
    finish() {
      const done = finishCurrent;
      finishCurrent = null;
      done?.();
      return settle();
    },
    stopped: false,
    blockPlayback,

    async clipFor({ text }) {
      synthesised.push(text);
      if (failOn.includes(text)) throw new Error(`cannot say ${text}`);
      return `clip:${text}`;
    },

    playToEnd(url) {
      played.push(url);
      if (this.blockPlayback) return Promise.reject(new Error("NotAllowedError"));
      return new Promise((resolve) => {
        finishCurrent = resolve;
      });
    },

    stop() {
      this.stopped = true;
    },
  };
}

describe("ordering", () => {
  it("plays clips in the order the segments were queued", async () => {
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.say("one");
    speaker.say("two");
    speaker.say("three");
    await settle();

    await voice.finish();
    await voice.finish();
    await voice.finish();

    assert.deepEqual(voice.played, ["clip:one", "clip:two", "clip:three"]);
  });

  it("synthesises the next segment while the current one is playing", async () => {
    // THE ONE. Without this the voice stops dead between paragraphs while it waits for
    // the next clip, which is the whole thing this module exists to avoid.
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.say("one");
    speaker.say("two");
    await settle();

    assert.deepEqual(voice.played, ["clip:one"], "the first clip should be playing");
    assert.ok(
      voice.synthesised.includes("two"),
      "the second segment should already have been synthesised",
    );
  });

  it("synthesises one at a time, never in parallel", async () => {
    // Parallel synthesis would be faster and would, on an unlucky day, deliver the
    // third paragraph before the second.
    let inFlight = 0;
    let peak = 0;
    const voice = fakeVoice();
    const slow = {
      ...voice,
      async clipFor({ text }) {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await settle();
        inFlight -= 1;
        return `clip:${text}`;
      },
    };

    const speaker = createSpeaker(slow);
    speaker.say("one");
    speaker.say("two");
    speaker.say("three");
    await settle();
    await settle();
    await settle();

    assert.equal(peak, 1);
  });

  it("accepts a segment queued after playback has already started", async () => {
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.say("one");
    await settle();
    speaker.say("two");
    await settle();

    await voice.finish();
    await voice.finish();

    assert.deepEqual(voice.played, ["clip:one", "clip:two"]);
  });
});

describe("failure", () => {
  it("a segment that will not synthesise does not stop the rest", async () => {
    // A reply with a gap in it beats a reply that stops dead.
    const voice = fakeVoice({ failOn: ["two"] });
    const errors = [];
    const speaker = createSpeaker(voice, { onError: (e) => errors.push(e) });

    speaker.say("one");
    speaker.say("two");
    speaker.say("three");
    await settle();
    await settle();

    await voice.finish();
    await voice.finish();

    assert.deepEqual(voice.played, ["clip:one", "clip:three"]);
    assert.equal(errors.length, 1);
  });

  it("a refused autoplay parks playback and reports once", async () => {
    // If the browser will not start audio it will not start the next clip either.
    const voice = fakeVoice({ blockPlayback: true });
    const blocked = [];
    const speaker = createSpeaker(voice, { onBlocked: (e) => blocked.push(e) });

    speaker.say("one");
    speaker.say("two");
    speaker.say("three");
    await settle();
    await settle();

    assert.equal(blocked.length, 1);
    assert.ok(speaker.blocked);
    assert.equal(speaker.cancelled, false);
    assert.equal(speaker.busy, false, "nothing will be heard until a tap");
    assert.equal(voice.played.length, 1, "should not have tried a second clip");
  });

  it("keeps making the rest of the reply after a refusal", async () => {
    // THE iOS BUG. Cancelling here meant paragraphs two and three were never
    // made, so the tap that followed played paragraph one and stopped.
    const voice = fakeVoice({ blockPlayback: true });
    const clips = [];
    const speaker = createSpeaker(voice, { onClip: (url) => clips.push(url) });

    speaker.say("one");
    speaker.say("two");
    await settle();
    speaker.say("three"); // still arriving after the refusal
    await settle();
    await settle();

    assert.deepEqual(voice.synthesised, ["one", "two", "three"]);
    assert.deepEqual(clips, ["clip:one", "clip:two", "clip:three"]);
  });

  it("resume plays on from the refused clip, in order", async () => {
    const voice = fakeVoice({ blockPlayback: true });
    const speaker = createSpeaker(voice);

    speaker.say("one");
    speaker.say("two");
    await settle();
    await settle();

    voice.blockPlayback = false;
    voice.played.length = 0;
    assert.equal(speaker.resume(), true);
    assert.equal(speaker.blocked, false);
    assert.deepEqual(voice.played, ["clip:one"], "the first clip starts inside the tap");

    await voice.finish();
    await voice.finish();
    assert.deepEqual(voice.played, ["clip:one", "clip:two"]);
  });

  it("drains while blocked once there is nothing left to make", async () => {
    // A replay from the words keeps its clips on drain, so the next tap is free.
    const voice = fakeVoice({ blockPlayback: true });
    let drains = 0;
    const speaker = createSpeaker(voice, { onDrain: () => { drains += 1; } });

    speaker.say("one");
    speaker.say("two");
    await settle();
    await settle();

    assert.equal(drains, 1);
    assert.equal(speaker.resume(), true);
  });

  it("resume does nothing when nothing was refused, or after a cancel", async () => {
    const voice = fakeVoice({ blockPlayback: true });
    const speaker = createSpeaker(voice);
    assert.equal(speaker.resume(), false);

    speaker.say("one");
    await settle();
    speaker.cancel();
    assert.equal(speaker.resume(), false);
  });
});

describe("cancel", () => {
  it("drops everything queued and stops the voice", async () => {
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.say("one");
    speaker.say("two");
    speaker.say("three");
    await settle();

    speaker.cancel();

    assert.ok(voice.stopped);
    assert.equal(speaker.busy, false);
  });

  it("tells the caller whether it cut anything off", async () => {
    // The view marks a cut reply so its replay is said again from the words.
    const voice = fakeVoice();
    const cuts = [];
    const busy = createSpeaker(voice, { onCancel: (unfinished) => cuts.push(unfinished) });
    busy.say("one");
    busy.say("two");
    await settle();
    busy.cancel();

    const idle = createSpeaker(fakeVoice(), { onCancel: (unfinished) => cuts.push(unfinished) });
    idle.cancel();

    assert.deepEqual(cuts, [true, false]);
  });

  it("refuses anything said after it", async () => {
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.cancel();
    speaker.say("nope");
    await settle();

    assert.deepEqual(voice.synthesised, []);
  });

  it("does not play a clip that was already in flight when it landed", async () => {
    // The clip is paid for either way, but playing it would talk over whatever
    // replaced it — a new message, or a different pick.
    let release;
    const voice = fakeVoice();
    const slow = {
      ...voice,
      clipFor: ({ text }) => new Promise((resolve) => {
        release = () => resolve(`clip:${text}`);
      }),
    };

    const speaker = createSpeaker(slow);
    speaker.say("one");
    await settle();

    speaker.cancel();
    release();
    await settle();

    assert.deepEqual(voice.played, []);
  });
});

describe("busy", () => {
  it("is true while there is work and false once it drains", async () => {
    const voice = fakeVoice();
    const speaker = createSpeaker(voice);

    speaker.say("one");
    assert.equal(speaker.busy, true);

    await settle();
    await voice.finish();

    assert.equal(speaker.busy, false);
  });
});

describe("onDrain", () => {
  it("fires once, after the last clip has finished playing", async () => {
    // The text stream ends well before the audio does. The view holds the donut in its
    // speaking mode until this fires, so it does not stop mid-sentence.
    const voice = fakeVoice();
    let drains = 0;
    const speaker = createSpeaker(voice, { onDrain: () => { drains += 1; } });

    speaker.say("one");
    speaker.say("two");
    await settle();

    assert.equal(drains, 0, "not while there is still audio to play");

    await voice.finish();
    assert.equal(drains, 0, "not between clips");

    await voice.finish();
    assert.equal(drains, 1);
  });

  it("does not fire for a speaker nobody used", async () => {
    const voice = fakeVoice();
    let drains = 0;
    createSpeaker(voice, { onDrain: () => { drains += 1; } });
    await settle();

    assert.equal(drains, 0);
  });

  it("does not fire on cancel", async () => {
    // Cancelling is the caller's own doing; it already knows.
    const voice = fakeVoice();
    let drains = 0;
    const speaker = createSpeaker(voice, { onDrain: () => { drains += 1; } });

    speaker.say("one");
    await settle();
    speaker.cancel();
    await settle();

    assert.equal(drains, 0);
  });
});

describe("a pause is not the end", () => {
  it("keeps working after it has drained", async () => {
    // The voice catching up with the model is an ordinary thing mid-reply: the queue
    // empties, everything goes quiet, and then the next paragraph arrives. Nothing
    // about draining may retire the speaker.
    const voice = fakeVoice();
    const drains = [];
    const speaker = createSpeaker(voice, { onDrain: () => drains.push(1) });

    speaker.say("first");
    await settle();
    await voice.finish();
    assert.equal(drains.length, 1, "should have gone quiet");
    assert.equal(speaker.busy, false);

    speaker.say("second");
    await settle();

    assert.deepEqual(voice.synthesised, ["first", "second"]);
    assert.deepEqual(voice.played, ["clip:first", "clip:second"]);
    assert.equal(speaker.busy, true, "busy again the moment there is more to say");
  });

  it("announces every drain, not only the first", async () => {
    // The view uses onDrain to decide whether to settle the donut, and it asks that
    // question after every lull. A latch here would leave the ring animating forever
    // after the second paragraph.
    const voice = fakeVoice();
    const drains = [];
    const speaker = createSpeaker(voice, { onDrain: () => drains.push(1) });

    for (const text of ["one", "two", "three"]) {
      speaker.say(text);
      await settle();
      await voice.finish();
    }

    assert.equal(drains.length, 3);
  });
});
