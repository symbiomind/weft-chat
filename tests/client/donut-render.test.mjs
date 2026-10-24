/* A smoke render for the donut.
 *
 * WHY THIS EXISTS. On 2026-09-14 a rewrite of the knobs block deleted `cssVar()` — an
 * unrelated helper that happened to sit between the two anchors being replaced. The
 * file was still valid JavaScript, so `node --check` passed. The unit tests imported
 * the module and called the pure exports, so they passed too. Nothing failed until the
 * app booted in a browser and the whole client died on a ReferenceError from inside the
 * first animation frame.
 *
 * The gap was that nothing ever RAN the drawing code. So this does: it builds a donut
 * against stub globals and drives one frame in each mode. It asserts almost nothing
 * about the output — whether the ring looks good is a question for eyes — but any
 * identifier that does not exist, any property read off something undefined, anywhere
 * in the render path, becomes a failing test here instead of a blank screen.
 *
 * The context stub accepts every method and every assignment. It is not pretending to
 * be a canvas; it is pretending to be indifferent, so the code under test can run to
 * the end without drawing anything.
 */

import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";

/** Every 2D method is a no-op; gradients are the one thing that must hand back an object. */
function stubContext() {
  const gradient = { addColorStop() {} };
  return new Proxy({}, {
    get(_target, prop) {
      if (prop === "createRadialGradient") return () => gradient;
      return () => {};
    },
    set() {
      return true;      // globalAlpha, lineWidth, strokeStyle, filter
    },
  });
}

function stubCanvas() {
  return {
    clientWidth: 800,
    clientHeight: 600,
    width: 0,
    height: 0,
    getContext: () => stubContext(),
  };
}

/** bloom.js builds its own offscreen canvases, so the document has to make them. */
function stubOffscreen() {
  return { width: 0, height: 0, getContext: () => stubContext() };
}

/** Frames are captured rather than scheduled, so the test drives the loop by hand. */
let frames = [];
const saved = {};

before(async () => {
  for (const key of [
    "document", "getComputedStyle", "devicePixelRatio", "Image",
    "requestAnimationFrame", "cancelAnimationFrame", "addEventListener",
    "removeEventListener", "performance", "innerWidth", "innerHeight",
    "ResizeObserver",
  ]) {
    saved[key] = globalThis[key];
  }

  // `hidden` is writable because the frame gate and the visibility pause both read it
  // every frame; the listener pair is recorded so a test can fire visibilitychange.
  globalThis.document = {
    hidden: false,
    documentElement: {
      style: { setProperty() {}, removeProperty() {} },
    },
    createElement: (tag) => (tag === "canvas" ? stubOffscreen() : {}),
    listeners: new Map(),
    addEventListener(type, fn) {
      this.listeners.set(type, fn);
    },
    removeEventListener(type) {
      this.listeners.delete(type);
    },
  };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#12141a" });
  globalThis.devicePixelRatio = 2;
  globalThis.Image = class {
    constructor() {
      this.complete = false;
      this.naturalWidth = 0;
    }
  };
  globalThis.requestAnimationFrame = (fn) => {
    frames.push(fn);
    return frames.length;
  };
  globalThis.cancelAnimationFrame = () => {};
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.performance = globalThis.performance ?? { now: () => 0 };
  // resize() clamps the canvas box to the window, so the window has to exist. The
  // numbers are larger than the stub canvas on purpose: the clamp should not bite.
  globalThis.innerWidth = 1280;
  globalThis.innerHeight = 900;
  // Observed but never fired — the donut only has to be able to construct one and
  // disconnect it. What it does on an actual layout change is the browser's business.
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
});

after(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

const { createDonut } = await import("../../web/js/canvas/donut.js");

/** Run the most recently scheduled frame, and only that one. */
function runFrame(at) {
  const fn = frames.pop();
  frames = [];
  assert.ok(fn, "expected a frame to have been scheduled");
  fn(at);
}

function boot() {
  frames = [];
  const donut = createDonut(stubCanvas());
  donut.start();
  return donut;
}

/**
 * The loop must stay a loop.
 *
 * rAF callbacks do not run in a hidden tab, they queue — and the keepalive nudges the
 * loop four times a second whether or not anything is drawing. Before schedule() held
 * the "one frame pending" guard, every one of those nudges appended another callback,
 * all of which fired together on the frame the tab came back, each tail-calling rAF:
 * one hidden tab in, a herd of independent render loops out, with stop() unable to
 * catch any but the last.
 *
 * Five minutes of mocked clock is twelve hundred nudges. The count afterwards is one.
 */
/**
 * Both bloom settings have to survive every mode.
 *
 * `off` is what ships. `gaussian` exists only so demo/4-perf.html can still price what
 * was taken away, which means it is code nobody runs — exactly the code that rots. The
 * two replacement strategies that were built before the halo was cut altogether now
 * live in demo/bloom.js and are covered by demo/5-fidelity.html instead.
 *
 * This asserts nothing about how the glow LOOKS, which is a question for eyes; it
 * asserts that neither path references something that is not there.
 */
describe("the bloom draws whichever way it is asked", () => {
  for (const how of ["off", "gaussian"]) {
    it(`renders every mode with ${how} bloom`, () => {
      const donut = boot();
      donut.setBloom(how);
      for (const mode of ["idle", "waiting", "speaking", "inbound"]) {
        donut.setMode(mode);
        runFrame(16);
        runFrame(32);
      }
      donut.stop();
    });
  }

  it("ignores a bloom setting it does not have", () => {
    const donut = boot();
    donut.setBloom("interpretive dance");
    donut.setMode("speaking");
    runFrame(16);
    donut.stop();
  });
});

/**
 * The shipped donut does not blur. Not anywhere, not in any mode.
 *
 * `ctx.filter` was 90.5% of the frame at 1440p and the halo it bought banded visibly
 * on exactly the large screens the work was meant to serve (see the note on `bloom` in
 * donut.js). Turning it off is the fix, and "off" being the default is the kind of
 * thing a well-meant later edit restores without noticing — so it is asserted rather
 * than trusted. The offscreen canvases bloom.js builds are separate contexts and do
 * not report here, which is exactly right: this asks what reaches the stage.
 */
describe("the shipped default", () => {
  it("paints no bloom onto the canvas it was handed", () => {
    // Two assertions, because a returning halo could arrive either way: `gaussian`
    // sets ctx.filter, and the offscreen strategies in demo/bloom.js arrive as a
    // drawImage. With no weft there is no backdrop either, so a stage that has been
    // asked to draw an image at all is a stage wearing a halo.
    const assigned = [];
    const called = [];
    const recording = {
      clientWidth: 800,
      clientHeight: 600,
      width: 0,
      height: 0,
      getContext: () => new Proxy({}, {
        get(_t, prop) {
          if (prop === "createRadialGradient") return () => ({ addColorStop() {} });
          return (...args) => { called.push([prop, args]); };
        },
        set(_t, prop, value) {
          assigned.push([prop, value]);
          return true;
        },
      }),
    };

    frames = [];
    const donut = createDonut(recording);
    donut.start();
    for (const mode of ["idle", "waiting", "speaking", "inbound"]) {
      donut.setMode(mode);
      runFrame(16);
      runFrame(32);
    }
    donut.stop();

    const filters = assigned.filter(([prop]) => prop === "filter");
    assert.deepEqual(filters, [], `expected no filter on the stage, saw ${JSON.stringify(filters)}`);

    const blits = called.filter(([name]) => name === "drawImage");
    assert.equal(blits.length, 0, `expected no drawImage on a backdropless stage, saw ${blits.length}`);

    // The stage should still have been drawn on, or the other two assertions are
    // passing for the boring reason.
    assert.ok(called.some(([name]) => name === "stroke"), "the ring itself should still be stroked");
  });
});

describe("the loop is a loop, not a herd", () => {
  it("queues no second frame however hard the keepalive nudges", (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    try {
      frames = [];
      const donut = createDonut(stubCanvas());
      donut.start();
      assert.equal(frames.length, 1, "start() should schedule exactly one frame");

      // The tab goes away. Nothing drains `frames`, which is precisely what a hidden
      // tab does to the rAF queue.
      t.mock.timers.tick(5 * 60 * 1000);

      assert.equal(frames.length, 1, "still exactly one frame pending after 1200 nudges");
      donut.stop();
    } finally {
      t.mock.timers.reset();
    }
  });

  it("schedules one frame per frame once it is running", () => {
    frames = [];
    const donut = createDonut(stubCanvas());
    donut.start();
    for (let i = 1; i <= 5; i += 1) {
      const fn = frames.shift();
      assert.ok(fn, "expected a pending frame");
      assert.equal(frames.length, 0, "a frame should not be pending while it runs");
      fn(i * 16);
      assert.equal(frames.length, 1, "a frame should schedule its successor, once");
    }
    donut.stop();
  });
});

describe("the render path runs", () => {
  it("starts without throwing", () => {
    // This alone would have caught the missing cssVar: start() calls refreshPalette().
    const donut = boot();
    donut.stop();
  });

  it("draws a frame in every mode", () => {
    for (const mode of ["idle", "waiting", "speaking", "inbound"]) {
      const donut = boot();
      donut.setMode(mode);
      runFrame(16);
      runFrame(32);
      donut.stop();
    }
  });

  it("survives a theme change", () => {
    const donut = boot();
    donut.refreshPalette();
    runFrame(16);
    donut.stop();
  });

  it("dresses and undresses for a weft", () => {
    const donut = boot();
    donut.setPair({ a: "#8fd0e0", b: "#d9a87e" });
    donut.setBackdrop("blob:x");
    runFrame(16);
    donut.setPair(null);
    donut.setBackdrop(null);
    runFrame(32);
    donut.stop();
  });
});

/**
 * The background picture.
 *
 * Two things matter and neither is visible in a single screenshot: a picture is drawn
 * whether or not the pick has colours (they are separate choices), and a picture that
 * finishes loading AFTER the pick changed is never drawn — or switching quickly from
 * one pick to another would put the first pick's picture behind the second's ring.
 */
describe("the backdrop", () => {
  /** Images built by the donut, so the test can decide when each one "loads". */
  let images = [];
  let RealImage;

  before(() => {
    RealImage = globalThis.Image;
    globalThis.Image = class {
      constructor() {
        this.complete = false;
        this.naturalWidth = 0;
        this.width = 0;
        this.height = 0;
        images.push(this);
      }
      load(w = 400, h = 300) {
        this.complete = true;
        this.naturalWidth = this.width = w;
        this.height = h;
        this.onload?.();
      }
    };
  });
  after(() => {
    globalThis.Image = RealImage;
  });

  /** A canvas that reports every drawImage onto the STAGE (not the offscreen layer). */
  function recordingDonut() {
    const blits = [];
    const canvas = {
      clientWidth: 800,
      clientHeight: 600,
      width: 0,
      height: 0,
      getContext: () => new Proxy({}, {
        get(_t, prop) {
          if (prop === "createRadialGradient") return () => ({ addColorStop() {} });
          if (prop === "drawImage") return (...args) => blits.push(args);
          return () => {};
        },
        set: () => true,
      }),
    };
    images = [];
    frames = [];
    const donut = createDonut(canvas);
    donut.start();
    return { donut, blits };
  }

  it("is drawn once loaded, with or without colours", () => {
    const { donut, blits } = recordingDonut();
    donut.setBackdrop("blob:first");
    runFrame(16);
    assert.equal(blits.length, 0, "nothing to draw before the picture loads");

    images[0].load();
    runFrame(100);
    assert.equal(blits.length, 1, "the cached layer should be blitted once per frame");
    donut.stop();
  });

  it("is gone the moment it is taken away", () => {
    const { donut, blits } = recordingDonut();
    donut.setBackdrop("blob:first");
    images[0].load();
    donut.setBackdrop(null);
    runFrame(16);
    assert.equal(blits.length, 0);
    donut.stop();
  });

  it("never paints a picture that arrives after the pick moved on", () => {
    const { donut, blits } = recordingDonut();
    donut.setBackdrop("blob:first");
    donut.setBackdrop("blob:second");
    assert.equal(images.length, 2);

    images[0].load(); // the first lands late
    runFrame(16);
    assert.equal(blits.length, 0, "a superseded picture must not be drawn");

    images[1].load();
    runFrame(100);
    assert.equal(blits.length, 1);
    donut.stop();
  });

  it("does not reload the picture it is already showing", () => {
    const { donut } = recordingDonut();
    donut.setBackdrop("blob:first");
    donut.setBackdrop("blob:first");
    assert.equal(images.length, 1, "the window re-dresses on every menu refresh");
    donut.stop();
  });
});

describe("the audio path runs", () => {
  /** Enough frames for cur.waveAmp to ease up, so the wave branch is actually taken. */
  function speakFor(donut, source) {
    donut.setMode("speaking");
    donut.setLevelSource(source);
    for (let i = 1; i <= 12; i += 1) runFrame(i * 16);
  }

  it("draws while a voice is loud", () => {
    const donut = boot();
    speakFor(donut, () => 0.7);
    donut.stop();
  });

  it("draws when the source returns null", () => {
    // Nothing playing. The generated motion alone, exactly as before any audio existed.
    const donut = boot();
    speakFor(donut, () => null);
    donut.stop();
  });

  it("draws when the source throws", () => {
    // The level reaches into an AudioContext owned by another module. If that ever
    // throws mid-frame, the donut must not take the whole client down with it — and
    // worse, must not skip the line that schedules the next frame.
    const donut = boot();
    let threw = false;
    try {
      speakFor(donut, () => {
        throw new Error("audio graph went away");
      });
    } catch {
      threw = true;
    }
    donut.stop();

    assert.equal(threw, false, "a failing level source must not escape the frame");
  });

  it("draws with a nonsense reading", () => {
    const donut = boot();
    speakFor(donut, () => NaN);
    donut.stop();
    const other = boot();
    speakFor(other, () => 99);
    other.stop();
  });
});

/**
 * THE FRAME GATE, and the reason it is worth a test at all.
 *
 * Measured on an integrated Vega: an idle window, halo long gone, cost
 * 36.4% of the GPU against 5.2% for a blank page — while reporting a clean 60fps the
 * whole time. Frame rate is not the instrument; the gate is the fix; and a fix whose
 * only symptom when removed is "somebody else's video stutters" is exactly the kind
 * that gets quietly undone by a well-meant later edit.
 *
 * A counting canvas rather than the shared stub, because what is being asserted here
 * is how OFTEN the ring is painted, which no other test in this file cares about.
 */
function countingCanvas(counts) {
  const gradient = { addColorStop() {} };
  const ctx = new Proxy({}, {
    get(_target, prop) {
      if (prop === "createRadialGradient") return () => gradient;
      if (prop === "stroke") return () => { counts.painted += 1; };
      return () => {};
    },
    set() {
      return true;
    },
  });
  return { clientWidth: 800, clientHeight: 600, width: 0, height: 0, getContext: () => ctx };
}

/** Deliver `n` vsyncs at `step` ms and report how many of them actually painted. */
function deliver(donut, counts, n, step, from = 0) {
  const before = counts.painted;
  for (let i = 1; i <= n; i += 1) {
    const fn = frames.pop();
    frames = [];
    if (!fn) break;              // a hidden page schedules nothing; that is the point
    fn(from + i * step);
  }
  return counts.painted - before;
}

describe("the frame gate", () => {
  /** One second of a 60Hz display. */
  const VSYNC = 1000 / 60;

  it("holds idle near 20fps instead of taking every frame the display offers", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("idle");

    const painted = deliver(donut, counts, 60, VSYNC);

    // 60 vsyncs is one second, so the ceiling is the fps number itself. Allowed a
    // little either way: the first frame always paints, and 50ms does not divide
    // into 16.67ms evenly.
    assert.ok(painted <= 22, `idle painted ${painted} of 60 delivered — gate not holding`);
    assert.ok(painted >= 18, `idle painted only ${painted} of 60 — gate too aggressive`);
    donut.stop();
  });

  it("lets speaking run free, because an 80ms attack cannot be drawn at 20fps", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("speaking");

    const painted = deliver(donut, counts, 60, VSYNC);
    assert.equal(painted, 60, "speaking must not be gated");
    donut.stop();
  });

  it("takes an override, which is the knob the issue asked for", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("idle");
    donut.setFps({ idle: 60 });

    assert.equal(deliver(donut, counts, 30, VSYNC), 30, "override ignored");
    donut.stop();
  });

  it("ignores a nonsense ceiling rather than painting never or always", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("idle");
    donut.setFps({ idle: 0, waiting: NaN, nonsense: 120 });

    const painted = deliver(donut, counts, 60, VSYNC);
    assert.ok(painted >= 18 && painted <= 22, `nonsense ceiling changed the rate: ${painted}`);
    donut.stop();
  });
});

/**
 * THE VISIBILITY PAUSE — the mobile half of the frame-rate work, and NOT
 * the desktop half.
 *
 * With the screen off, TTS keeps playing (it is an <audio> element) while the OS
 * throttles the page around it; the ring had been carrying on breathing into that.
 * A weft window merely sitting behind another window is NOT hidden, so this never
 * fires there and the gate above is what holds that case down. Both are needed.
 */
describe("a hidden page", () => {
  function hide(value) {
    document.hidden = value;
    document.listeners.get("visibilitychange")?.();
  }

  it("paints nothing and leaves nothing queued", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("speaking");
    deliver(donut, counts, 2, 16);

    hide(true);
    const painted = deliver(donut, counts, 30, 16, 1000);
    assert.equal(painted, 0, "a hidden page is still painting");
    assert.equal(frames.length, 0, "a hidden page has frames queued — the herd is back");

    hide(false);
    donut.stop();
  });

  it("picks the loop back up when the screen comes on", () => {
    const counts = { painted: 0 };
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.start();
    donut.setMode("speaking");

    hide(true);
    deliver(donut, counts, 5, 16);
    hide(false);

    // Straight away, not after waiting out a gate interval measured from before the
    // page went away.
    assert.equal(deliver(donut, counts, 3, 16, 60000), 3, "loop did not resume");
    donut.stop();
  });
});

/**
 * Calm and still motion (comfort.js): the ring holds still, tells its state by colour,
 * and stops drawing once it has. A ring that kept repainting an unchanging picture
 * would be motionless and still cost what a moving one costs.
 *
 * The clock is the donut's own (performance.now) and the frames' timestamps, kept
 * together here as a browser keeps them.
 */
describe("calm and still motion", () => {
  const VSYNC = 1000 / 60;
  let clock = 0;
  let realPerformance;

  /** Deliver vsyncs with the shared clock advancing, until none is scheduled. */
  function deliverTimed(counts, n) {
    const before = counts.painted;
    for (let i = 0; i < n; i += 1) {
      const fn = frames.pop();
      frames = [];
      if (!fn) break;
      clock += VSYNC;
      fn(clock);
    }
    return counts.painted - before;
  }

  function bootWith(motion, counts) {
    realPerformance = globalThis.performance;
    globalThis.performance = { now: () => clock };
    clock = 1000;
    frames = [];
    const donut = createDonut(countingCanvas(counts));
    donut.setMotion(motion);
    donut.start();
    return donut;
  }

  function done(donut) {
    donut.stop();
    globalThis.performance = realPerformance;
  }

  it("still draws the ring once, then nothing until something changes", () => {
    const counts = { painted: 0 };
    const donut = bootWith("still", counts);

    const painted = deliverTimed(counts, 120);
    assert.equal(painted, 1, `still painted ${painted} frames for an unchanging ring`);
    assert.equal(frames.length, 0, "a still ring must leave nothing scheduled");

    donut.setMode("waiting");
    assert.equal(deliverTimed(counts, 120), 1, "a change of mode is drawn exactly once");
    done(donut);
  });

  it("calm fades to a change at 20fps for a moment, then rests", () => {
    const counts = { painted: 0 };
    const donut = bootWith("calm", counts);
    deliverTimed(counts, 600);

    donut.setMode("waiting");
    const painted = deliverTimed(counts, 600);
    // 1.5s of fade at 20fps is about 30 frames; then nothing.
    assert.ok(painted >= 25 && painted <= 35, `calm painted ${painted} frames for one change`);
    assert.equal(frames.length, 0, "a calm ring rests once the fade is done");
    done(donut);
  });

  it("speaking does not make a calm ring move", () => {
    const counts = { painted: 0 };
    const donut = bootWith("calm", counts);
    deliverTimed(counts, 600);
    donut.setLevelSource(() => 1);
    donut.setMode("speaking");
    deliverTimed(counts, 600);
    assert.equal(frames.length, 0, "a loud voice must not keep a calm ring drawing");
    done(donut);
  });

  it("goes back to the moving loop when motion is full again", () => {
    const counts = { painted: 0 };
    const donut = bootWith("still", counts);
    deliverTimed(counts, 60);
    donut.setMotion("full");
    const painted = deliverTimed(counts, 60);
    assert.ok(painted >= 18, `full painted only ${painted} frames in a second`);
    done(donut);
  });
});
