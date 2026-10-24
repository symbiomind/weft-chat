/* Tests for the donut's breath (web/js/canvas/donut.js).
 *
 * This file used to test a spectrum-to-ring mapping. That whole direction is gone
 *: a spectrum is a LINE with two ends and a ring is a CIRCLE with none,
 * so every continuous mapping of one onto the other has to fold — leaving only a tear
 * or a mirror available, both of which looked wrong. What replaced it is smaller: the
 * generated motion is left entirely alone and loudness simply deepens it.
 *
 * Two things are asserted here, and both are one character away from being silently
 * wrong:
 *
 * **The ripples are quantised.** A wave closes a circle only when a whole number of
 * wavelengths fits the circumference. Whole number: seamless. Anything else: torn. That
 * is not a style rule, it is why the seam this file is named after cannot come back —
 * and `a fractional wavelength tears the ring open` proves the difference rather than
 * asserting the intent.
 *
 * **The attack is fast and the release is slow.** Swap those two and nothing errors,
 * nothing looks wrong in review, and the donut stops feeling like breath and starts
 * feeling like a needle that sticks.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  RING_POINTS,
  VOICE_KNOBS,
  WAVE_HARMONICS,
  envelope,
  standingWave,
} from "../../web/js/canvas/donut.js";

/** Run an envelope forward for a while at a given frame rate. */
function run(from, target, seconds, fps = 60, knobs = VOICE_KNOBS) {
  const dt = 1 / fps;
  let value = from;
  for (let i = 0; i < seconds * fps; i += 1) value = envelope(value, target, dt, knobs);
  return value;
}

describe("the envelope", () => {
  it("rises faster than it falls", () => {
    // THE ONE. This is the difference between a lung and a needle, and it is a single
    // swapped variable away from being backwards.
    const rose = run(0, 1, 0.1);
    const fell = 1 - run(1, 0, 0.1);

    assert.ok(rose > fell, `rose ${rose.toFixed(3)} should beat fell ${fell.toFixed(3)}`);
  });

  it("is most of the way up within the attack time", () => {
    const value = run(0, 1, VOICE_KNOBS.attackSeconds);

    assert.ok(value > 0.6, `reached ${value.toFixed(3)} in one attack constant`);
  });

  it("is still audible a moment after the sound stops", () => {
    // The release is savoured. Falling to nothing the instant a syllable ends is what
    // makes a level meter look mechanical.
    const value = run(1, 0, VOICE_KNOBS.attackSeconds);

    assert.ok(value > 0.5, `dropped to ${value.toFixed(3)} in one attack constant`);
  });

  it("gets there eventually, in both directions", () => {
    assert.ok(run(0, 1, 3) > 0.99);
    assert.ok(run(1, 0, 3) < 0.01);
  });

  it("moves at the same speed whatever the frame rate", () => {
    // Easing per-frame rather than per-second would make the ring breathe twice as fast
    // on a 120Hz display — invisible on the machine it was written on.
    const at60 = run(0, 1, 0.2, 60);
    const at120 = run(0, 1, 0.2, 120);
    const at30 = run(0, 1, 0.2, 30);

    assert.ok(Math.abs(at60 - at120) < 0.02, `60Hz ${at60.toFixed(3)} vs 120Hz ${at120.toFixed(3)}`);
    assert.ok(Math.abs(at60 - at30) < 0.03, `60Hz ${at60.toFixed(3)} vs 30Hz ${at30.toFixed(3)}`);
  });

  it("never overshoots", () => {
    // A ring that swells past the loudest moment and springs back reads as a glitch.
    let value = 0;
    for (let i = 0; i < 200; i += 1) {
      value = envelope(value, 1, 1 / 60);
      assert.ok(value <= 1, `overshot to ${value}`);
    }
  });
});

describe("silence", () => {
  it("settles at exactly zero, not merely near it", () => {
    // An exponential approaches its target forever without arriving, which would leave
    // the ring carrying a vanishing but permanent fraction of the last thing it heard.
    // The swell multiplier is 1 + voice * knob, so "looks exactly as it did before any
    // audio existed" requires voice to reach 0 — not 2e-6.
    assert.equal(envelope(0, 0, 1 / 60), 0);
    assert.equal(run(0.5, 0, 5), 0);
  });

  it("treats a missing reading as silence, not as a hold", () => {
    // levelSource returns null when nothing is playing. The view coalesces that to 0;
    // this is the other half — 0 must actually bring the ring home.
    assert.ok(run(1, 0, 2) < 0.01);
  });
});

describe("bad input never freezes the ring", () => {
  it("survives a NaN reading", () => {
    assert.ok(Number.isFinite(envelope(0.5, NaN, 1 / 60)));
  });

  it("survives a NaN current value", () => {
    assert.ok(Number.isFinite(envelope(NaN, 0.5, 1 / 60)));
  });

  it("survives a zero or negative frame time", () => {
    // The first frame after a backgrounded tab can produce anything.
    assert.equal(envelope(0.4, 1, 0), 0.4);
    assert.equal(envelope(0.4, 1, -1), 0.4);
    assert.ok(Number.isFinite(envelope(0.4, 1, NaN)));
  });

  it("clamps a reading from outside the range", () => {
    // Loudness is scaled before it arrives; a scaling change must not be able to push
    // the ring inside out.
    assert.ok(run(0, 5, 2) <= 1);
    assert.ok(run(1, -5, 2) >= 0);
  });

  it("survives knobs with a zero time constant", () => {
    const value = envelope(0, 1, 1 / 60, { attackSeconds: 0, releaseSeconds: 0 });
    assert.equal(value, 1);
  });
});

describe("the ripples close the ring", () => {
  /** How big the join is, against the biggest step anywhere else on the ring. */
  function wrapRatio(values) {
    const wrap = Math.abs(values[0] - values[values.length - 1]);
    let interior = 0;
    for (let i = 0; i < values.length - 1; i += 1) {
      interior = Math.max(interior, Math.abs(values[i + 1] - values[i]));
    }
    return wrap / interior;
  }

  /**
   * The shipped harmonics with the LOUDEST lobe count bent to a non-whole number.
   *
   * The loudest, deliberately. An earlier version bent the quietest — the 42 whisper —
   * and the test silently stopped proving anything the moment that harmonic was turned
   * down to 1% of the shape: its tear was real but far smaller than the legitimate
   * gradient of the harmonics carrying the ring. A proof that quantisation matters has
   * to be run on a harmonic you could actually see.
   */
  function bend(lobes) {
    const loudest = WAVE_HARMONICS.reduce(
      (best, h, i) => (h.amplitude > WAVE_HARMONICS[best].amplitude ? i : best),
      0,
    );
    return WAVE_HARMONICS.map((h, i) => (i === loudest ? { ...h, lobes } : h));
  }

  it("has no join at all, at any moment", () => {
    // Not "a small join". The value at the last point is the value at the first,
    // because a whole number of wavelengths fits the circumference exactly.
    for (let t = 0; t < 20; t += 0.37) {
      const ratio = wrapRatio(standingWave(t));
      assert.ok(ratio <= 1.05, `t=${t.toFixed(2)} wrap/interior ${ratio.toFixed(3)}`);
    }
  });

  it("a fractional wavelength tears the ring open", () => {
    // The proof that the whole numbers are load-bearing rather than tidy. Miss the
    // quantisation by half a wavelength and the join is twice the worst real gradient.
    let worst = 0;
    for (let t = 0; t < 20; t += 0.37) {
      worst = Math.max(worst, wrapRatio(standingWave(t, bend(5.5))));
    }

    assert.ok(worst > 1.5, `fractional lobes should tear; worst ratio ${worst.toFixed(2)}`);
  });

  it("every shipped harmonic is a whole number", () => {
    for (const { lobes } of WAVE_HARMONICS) {
      assert.ok(Number.isInteger(lobes), `${lobes} wavelengths does not close a circle`);
    }
  });

  it("keeps the Answer", () => {
    // 42 whole wavelengths is both the Answer to Life, the Universe and
    // Everything AND a legal quantum — the ring closes BECAUSE the number is whole, and
    // 42 is the whole number chosen. Deleting it should be a decision, not a drift.
    assert.ok(WAVE_HARMONICS.some((h) => h.lobes === 42), "the Answer has gone missing");
  });

  it("samples the ring finely enough to draw its fastest ripple", () => {
    // Quantisation closes the ring at any resolution; resolution decides whether the
    // ripple is legible once it is there. Below about four points per wavelength it
    // aliases into jitter.
    const fastest = Math.max(...WAVE_HARMONICS.map((h) => h.lobes));

    assert.ok(
      RING_POINTS / fastest >= 6,
      `${(RING_POINTS / fastest).toFixed(1)} points per wavelength is too coarse`,
    );
  });
});

describe("the ripples stand, and drift", () => {
  const pure = (lobes, drift) => [{ lobes, flip: 0, drift, amplitude: 0.3 }];

  it("swings a point through flat, from peak to trough", () => {
    // THE difference between this and a travelling wave, and the entire complaint that
    // caused the rewrite. A travelling wave is a rigid shape sliding around: a point on
    // it goes up and down a little as lobes pass, but the ring reads as an awkward
    // object being dragged round. A standing wave holds its nodes and swings the
    // bellies between them through peak, flat and trough — a vibrating string.
    let lo = 1;
    let hi = 0;
    for (let t = 0; t < 12; t += 0.05) {
      const v = standingWave(t)[40];
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }

    assert.ok(lo < 0.45, `never reaches a trough (lowest ${lo.toFixed(3)})`);
    assert.ok(hi > 0.55, `never reaches a peak (highest ${hi.toFixed(3)})`);
  });

  it("puts opposite sides of the ring in antiphase — because the lobes are odd", () => {
    // sin(n(θ+π)) = -sin(nθ) for odd n. When the left bulges out, the right pulls IN,
    // so the donut sways instead of breathing symmetrically. Even n puts them in phase
    // and both sides bulge together, which on a landscape screen — where left and right
    // are precisely what is not clipped — is the whole difference.
    const antinode = (n) => Math.round(((Math.PI / 2) / n) / (Math.PI * 2) * RING_POINTS);

    for (const n of [3, 5, 9]) {
      const v = standingWave(0.4, [{ lobes: n, flip: 0.31, drift: 0, amplitude: 0.3 }]);
      const i = antinode(n);
      const here = v[i] - 0.5;
      const opposite = v[(i + RING_POINTS / 2) % RING_POINTS] - 0.5;

      assert.ok(opposite / here < 0, `n=${n} should sway, ratio ${(opposite / here).toFixed(2)}`);
    }

    for (const n of [4, 8]) {
      const v = standingWave(0.4, [{ lobes: n, flip: 0.31, drift: 0, amplitude: 0.3 }]);
      const i = antinode(n);
      const here = v[i] - 0.5;
      const opposite = v[(i + RING_POINTS / 2) % RING_POINTS] - 0.5;

      assert.ok(opposite / here > 0, `n=${n} pulses rather than sways`);
    }
  });

  it("keeps the shape-carrying harmonics odd", () => {
    // The whisper harmonic is exempt — at 4% amplitude its phase relationship is not
    // something an eye can resolve. Everything that carries the shape must sway.
    const carrying = WAVE_HARMONICS.filter((h) => h.amplitude > 0.03);

    assert.ok(carrying.length >= 3, "expected several shaping harmonics");
    for (const { lobes } of carrying) {
      assert.ok(lobes % 2 === 1, `${lobes} is even — opposite sides would pulse together`);
    }
  });

  it("drifts anticlockwise", () => {
    // Canvas angles increase clockwise, so a peak travelling anticlockwise is one whose
    // index falls. Tested on a pure travelling harmonic: with the flip switched on, the
    // "peak" jumps to another lobe whenever the swing changes sign.
    const peakAt = (t) => {
      const v = standingWave(t, pure(1, 0.1));
      return v.indexOf(Math.max(...v));
    };

    assert.ok(peakAt(0.5) < peakAt(0), `${peakAt(0)} -> ${peakAt(0.5)}`);
  });

  it("actually moves", () => {
    const before = standingWave(0);
    const after = standingWave(1.3);

    assert.ok(before.some((v, i) => Math.abs(v - after[i]) > 0.01), "the ring is frozen");
  });

  it("has no front — every part of the ring gets its turn", () => {
    // A standing wave alone would pin its nodes forever, leaving points that never move
    // at all. The drift is what carries the nodes around, so this needs long enough for
    // the slowest pattern to precess.
    const range = new Array(RING_POINTS).fill(0).map(() => ({ lo: 1, hi: 0 }));
    for (let t = 0; t < 180; t += 0.5) {
      standingWave(t).forEach((v, i) => {
        range[i].lo = Math.min(range[i].lo, v);
        range[i].hi = Math.max(range[i].hi, v);
      });
    }

    const movement = range.map((r) => r.hi - r.lo);
    const quietest = Math.min(...movement);
    const liveliest = Math.max(...movement);

    assert.ok(
      quietest > liveliest * 0.5,
      `quietest point moves ${quietest.toFixed(3)}, liveliest ${liveliest.toFixed(3)}`,
    );
  });

  it("stays in a drawable range", () => {
    for (let t = 0; t < 30; t += 0.7) {
      const v = standingWave(t);
      assert.ok(v.every(Number.isFinite), `t=${t}`);
      assert.ok(Math.min(...v) > -0.5 && Math.max(...v) < 1.5, `t=${t}`);
    }
  });

  it("survives a nonsense clock", () => {
    assert.ok(standingWave(NaN).every(Number.isFinite));
    assert.ok(standingWave(undefined).every(Number.isFinite));
  });
});

describe("the taste knobs", () => {
  it("ship with a fast attack and a slow release", () => {
    assert.ok(VOICE_KNOBS.attackSeconds < VOICE_KNOBS.releaseSeconds);
  });

  it("ship with both movements turned on", () => {
    assert.ok(VOICE_KNOBS.swell > 0, "the ring should grow with loudness");
    assert.ok(VOICE_KNOBS.deepen > 0, "and breathe harder, not merely inflate");
  });

  it("scales the reading rather than leaving speech in the bottom fifth", () => {
    // RMS for speech is roughly 0.05-0.2. Unscaled, the ring would move a couple of
    // pixels and look broken rather than calm.
    assert.ok(VOICE_KNOBS.sensitivity > 1);
    assert.ok(0.15 * VOICE_KNOBS.sensitivity > 0.5, "a normal voice should reach mid-range");
  });
});
