/* A smoke render for the login page's breathers.
 *
 * WHY THIS EXISTS. breathers.js had no test at all until 2026-09-21, which was fine
 * while nobody touched it and stopped being fine the moment the bloom work did — twice
 * in one day, first to blit a sprite instead of blurring, then to drop the halo
 * entirely so the babies still match the chat donut.
 *
 * It is the same bargain as donut-render.test.mjs: drive the real drawing code against
 * stub globals and assert almost nothing about the result. Whether the babies look
 * right is a question for eyes. Whether the render path references something that does
 * not exist is a question for this, and the login page is the first thing anyone sees
 * of Weft — a ReferenceError inside the first animation frame there is a blank window
 * with a password box floating on it.
 */

import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";

/** Indifferent to everything, like donut-render.test.mjs's. */
function stubContext(record) {
  const gradient = { addColorStop() {} };
  return new Proxy({}, {
    get(_target, prop) {
      if (prop === "createRadialGradient") return () => gradient;
      return (...args) => { record?.calls.push([prop, args]); };
    },
    set(_target, prop, value) {
      record?.assigned.push([prop, value]);
      return true;
    },
  });
}

function stubCanvas(record) {
  return {
    clientWidth: 1280,
    clientHeight: 900,
    width: 0,
    height: 0,
    getContext: () => stubContext(record),
  };
}

let frames = [];
let clock = 0;
const saved = {};

before(() => {
  for (const key of [
    "document", "getComputedStyle", "devicePixelRatio", "requestAnimationFrame",
    "cancelAnimationFrame", "addEventListener", "removeEventListener", "performance",
    "innerWidth", "innerHeight", "ResizeObserver",
  ]) {
    saved[key] = globalThis[key];
  }

  // `hidden` is writable and the listener pair is recorded: the frame gate reads the
  // first every frame and the visibility pause is registered through the second.
  globalThis.document = {
    hidden: false,
    documentElement: {},
    listeners: new Map(),
    addEventListener(type, fn) { this.listeners.set(type, fn); },
    removeEventListener(type) { this.listeners.delete(type); },
  };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#12141a" });
  globalThis.devicePixelRatio = 2;
  globalThis.innerWidth = 1280;
  globalThis.innerHeight = 900;
  globalThis.requestAnimationFrame = (fn) => frames.push(fn);
  globalThis.cancelAnimationFrame = () => {};
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  // Driven by hand so a handover can actually be reached: a spot lasts 14 seconds and
  // hands over across 3, and waiting for that in real time is not a test.
  globalThis.performance = { now: () => clock };
});

after(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

const { createBreathers } = await import("../../web/js/canvas/breathers.js");

function runFrame() {
  const fn = frames.pop();
  frames = [];
  assert.ok(fn, "expected a frame to have been scheduled");
  fn();
}

describe("the breathers render", () => {
  it("starts, breathes, hands over and stops without throwing", () => {
    frames = [];
    clock = 0;
    const breathers = createBreathers(stubCanvas());
    breathers.start();

    // Two breaths, the whole cross-fade, and out the other side into the next spot.
    for (const at of [0, 1000, 6000, 11500, 12500, 13500, 16000, 17500, 20000, 31000]) {
      clock = at;
      runFrame();
    }
    breathers.stop();
  });

  it("survives a canvas that is not laid out yet", () => {
    frames = [];
    clock = 0;
    const bare = stubCanvas();
    bare.clientWidth = 0;
    bare.clientHeight = 0;
    const breathers = createBreathers(bare);
    breathers.start();
    runFrame();
    breathers.stop();
  });

  it("paints no halo — the babies match the chat donut", () => {
    // The same assertion donut-render.test.mjs makes, for the same reason: the law is
    // "in every way except size", and a later edit restoring the glow here alone would
    // break it silently. No filter, and no blit standing in for one.
    const record = { calls: [], assigned: [] };
    frames = [];
    clock = 0;
    const breathers = createBreathers(stubCanvas(record));
    breathers.start();
    for (const at of [0, 3000, 12500, 16000]) {
      clock = at;
      runFrame();
    }
    breathers.stop();

    const filters = record.assigned.filter(([prop]) => prop === "filter");
    assert.deepEqual(filters, [], `expected no filter, saw ${JSON.stringify(filters)}`);
    const blits = record.calls.filter(([name]) => name === "drawImage");
    assert.equal(blits.length, 0, `expected no drawImage, saw ${blits.length}`);
    assert.ok(record.calls.some(([name]) => name === "stroke"), "the rings should still be stroked");
  });
});

/**
 * THE FRAME GATE on the login page.
 *
 * Measured against the live app: the sign-in screen cost 17.1% of the
 * GPU where an already-gated chat window cost 11.3%. The donut had been gated and its
 * babies had not, which made the first screen anyone sees the most expensive thing in
 * weft — while showing a blameless 60fps, as all of this does.
 *
 * `clearRect` opens every painted frame exactly once, so counting it counts paints.
 */
function paintCountingCanvas(counts) {
  const gradient = { addColorStop() {} };
  const ctx = new Proxy({}, {
    get(_target, prop) {
      if (prop === "createRadialGradient") return () => gradient;
      if (prop === "clearRect") return () => { counts.painted += 1; };
      return () => {};
    },
    set() { return true; },
  });
  return { clientWidth: 1280, clientHeight: 900, width: 0, height: 0, getContext: () => ctx };
}

/** Deliver `n` vsyncs `stepMs` apart, and report how many of them painted. */
function deliver(counts, n, stepMs) {
  const before = counts.painted;
  for (let i = 1; i <= n; i += 1) {
    const fn = frames.pop();
    frames = [];
    if (!fn) break;                 // a hidden page schedules nothing
    clock += stepMs;
    fn();
  }
  return counts.painted - before;
}

describe("the breathers' frame gate", () => {
  const VSYNC = 1000 / 60;

  it("holds the babies near 20fps instead of taking every frame offered", () => {
    const counts = { painted: 0 };
    frames = [];
    clock = 0;
    const breathers = createBreathers(paintCountingCanvas(counts));
    breathers.start();
    // start() paints nothing by itself; the first delivered frame is the first paint.
    const painted = deliver(counts, 60, VSYNC);

    assert.ok(painted <= 22, `painted ${painted} of 60 delivered — gate not holding`);
    assert.ok(painted >= 18, `painted only ${painted} of 60 — gate too aggressive`);
    breathers.stop();
  });

  it("paints nothing and queues nothing while the page is hidden", () => {
    const counts = { painted: 0 };
    frames = [];
    clock = 0;
    const breathers = createBreathers(paintCountingCanvas(counts));
    breathers.start();
    deliver(counts, 2, 100);

    document.hidden = true;
    document.listeners.get("visibilitychange")?.();
    const painted = deliver(counts, 30, 100);
    assert.equal(painted, 0, "a hidden login page is still breathing");
    assert.equal(frames.length, 0, "a hidden login page has frames queued");

    document.hidden = false;
    document.listeners.get("visibilitychange")?.();
    assert.equal(deliver(counts, 1, 100), 1, "did not resume when shown again");
    breathers.stop();
  });
});
