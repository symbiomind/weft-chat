/* Weft — the breathers: the login page's quiet tenants.
 *
 * The chat donut is weft's signature: grey, patient, breathing while you wait.
 * The login page borrows it — at quarter scale, several of them, one at a time —
 * so the window is inhabited before anyone has knocked. Grey only, by the colour
 * law: grey is the house; colour only when a heart beats or a weft speaks.
 *
 * The babies are the chat donut in every way except size: same idle rhythm
 * (pulse 1±0.03 at t*0.9, alpha 0.35±0.08 on the same phase), same grey sampled
 * from CSS, breathing twice at a spot before the next takes over — and, since
 * 2026-09-21, the same bare ring with no halo around it.
 *
 * Placement is deliberately dumb: a uniform random centre inside a 20px margin,
 * like dots laid out across the window — "|  .     .     .     .  |" — no
 * anchor sets, no card avoidance, no minimum gaps. A donut may drift under the
 * login card's glass; that is not a defect, it is the bubbles' behaviour too.
 *
 * Handover is respiration, not teleport: the outgoing spot's alpha decays while
 * the incoming one rises.
 */

const BREATH_PERIOD = 7; // seconds; matches donut.js idle (sin(t * 0.9))
const BREATHS_PER_SPOT = 2;
const HANDOVER = 3.0; // seconds of cross-fade between spots
const MARGIN = 20; // px viewport margin — raised to the ring radius below, so
// a centre at the margin line still keeps the whole ring on-screen.
/**
 * THE FRAME GATE, same policy as donut.js.
 *
 * Measured against the live app: the LOGIN page cost 17.1% of the GPU where a gated
 * chat window cost 11.3% — the sign-in screen was the most expensive thing in weft,
 * because the donut had been gated and its babies had not. They breathe on a 7-second
 * sine; there is nothing in that a twentieth of a second cannot draw.
 *
 * There is no per-mode map here because there is only one mode. Nothing on this page
 * has an 80ms attack to miss, which is the whole reason the chat donut keeps `speaking`
 * ungated.
 */
const FPS = 20;

const RADIUS_FRAC = 1 / 6; // ring radius = LONGEST viewport dimension ÷ 6, so

export function createBreathers(canvas) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(devicePixelRatio || 1, 2);

  let running = false;
  let frameHandle = null;
  /** When the last frame was PAINTED, in seconds. 0 means "paint the next one". */
  let lastPaint = 0;
  let width = 0;
  let height = 0;
  let spot = { x: 0, y: 0 }; // current centre
  let next = { x: 0, y: 0 }; // where the next breath will condense
  let bounds = { minX: 0, maxX: 0, minY: 0, maxY: 0 };
  let cycleStart = 0; // seconds timestamp when this spot took the breath
  /** full | calm | still (comfort.js). Anything but full: one ring, holding still,
   *  drawn once and again only when the size changes. Nothing breathes or moves. */
  let motion = "full";

  // Palette follows donut.js: idle grey re-sampled from CSS so theme changes ride along.
  function idleGrey() {
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg")
      .trim();
    return bg.startsWith("#f") || bg.startsWith("#e") ? "#b8b4a6" : "#4a5060";
  }

  function resize() {
    // Size from the canvas element itself: it is laid out with inset:0, so its
    // clientWidth/Height IS the visible area. But guard the numbers: during
    // device flips / zoom transitions an inset:0 element can transiently report
    // an enormous untransformed box, and canvas.width = box * dpr on that
    // allocates a multi-gigapixel backing store (the browser frowns). Clamp to
    // the window — the canvas can never legitimately exceed it.
    width = Math.min(canvas.clientWidth, Math.max(innerWidth, 1));
    height = Math.min(canvas.clientHeight, Math.max(innerHeight, 1));
    if (!width || !height) return; // not laid out yet; observer will re-fire
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // The render-area margin is a fixed 20px, as designed. The ring may cross the
    // edge line (and be partially off-screen) — that is intentional: the donut
    // is a wanderer, not a prisoner, and partial rings at the edge read as
    // depth, not defect.
    const m = MARGIN;
    bounds = { minX: m, maxX: width - m, minY: m, maxY: height - m };
    spot = randomSpot();
    next = randomSpot();
    cycleStart = nowSeconds();
    // Resizing cleared the canvas; a ring that is holding still must be drawn again.
    if (running && motion !== "full") {
      lastPaint = 0;
      schedule();
    }
  }

  function randomSpot() {
    // The centre lands uniformly inside the safe bounds; the safe margin is at
    // least the ring radius, so the whole circle stays on-screen. A donut may
    // still drift under the login card's glass — legal, like the bubbles.
    const x = bounds.minX + Math.random() * Math.max(1, bounds.maxX - bounds.minX);
    const y = bounds.minY + Math.random() * Math.max(1, bounds.maxY - bounds.minY);
    const s = { x, y };
    return s;
  }

  function nowSeconds() {
    return performance.now() / 1000;
  }

  function draw(t) {
    ctx.clearRect(0, 0, width, height);
    const grey = idleGrey();
    const spotSeconds = BREATHS_PER_SPOT * BREATH_PERIOD;
    const elapsed = t - cycleStart;

    // Two tenants on stage: the current spot breathes, and during the last
    // HANDOVER seconds of the hold the next spot condenses as this one fades.
    const moving = motion === "full";
    for (const [centre, isCurrent] of moving ? [[spot, true], [next, false]] : [[spot, true]]) {
      let weight = 1;
      if (!moving) {
        weight = 1;
      } else if (isCurrent && elapsed > spotSeconds - HANDOVER) {
        weight = Math.max(0, 1 - (elapsed - (spotSeconds - HANDOVER)) / HANDOVER);
      } else if (!isCurrent) {
        const localT = elapsed - (spotSeconds - HANDOVER);
        if (localT < 0) continue;
        weight = Math.min(1, Math.max(0, localT / HANDOVER));
      }

      // The donut's own idle breath, exactly as donut.js draws it.
      const breathe = moving ? Math.sin(elapsed * 0.9) : 0;
      const pulse = 1 + 0.03 * breathe;
      const alpha = (0.35 + 0.08 * breathe) * weight;
      if (alpha <= 0.004) continue;

      const R = Math.max(width, height) * RADIUS_FRAC * pulse;
      const ring = R * 0.1;

      ctx.globalAlpha = alpha;
      const gradient = ctx.createRadialGradient(
        centre.x,
        centre.y,
        R - ring,
        centre.x,
        centre.y,
        R + ring,
      );
      gradient.addColorStop(0, grey);
      gradient.addColorStop(1, grey);
      ctx.strokeStyle = gradient;
      ctx.lineWidth = ring * 1.3;
      ctx.beginPath();
      ctx.arc(centre.x, centre.y, R, 0, Math.PI * 2);
      ctx.stroke();

      // No halo, and that is the law rather than an omission.
      //
      // These used to carry the chat donut's feathered bloom at a fifth of its alpha,
      // drawn the same way: a second stroke through `ctx.filter`. The chat donut lost
      // its halo on 2026-09-21 (it banded, because eight bits of alpha
      // cannot describe a ramp that gradual, and it banded WORSE on larger screens;
      // over a backdrop it was invisible except as a veil over the portrait). The
      // babies are the chat donut in every way except size, so they lost theirs too.
      //
      // The cost that started the investigation went with it. `ctx.filter` allocates a
      // layer the size of the CLIP REGION, so this inset:0 canvas paid a full-viewport
      // blur per stroke however small the kernel — twice over during a handover, three
      // seconds in every seventeen, on the login page, before anyone has typed a
      // password. If the glow is ever wanted back, bloom.js still has it.
    }
    ctx.globalAlpha = 1;
  }

  /** Ask for exactly one frame, and none at all for a page nobody is looking at. */
  function schedule() {
    if (frameHandle !== null) return;
    if (document.hidden) return;
    frameHandle = requestAnimationFrame(frame);
  }

  function frame() {
    frameHandle = null;
    if (!running) return;
    if (document.hidden) return;

    const t = nowSeconds();
    // The gate. Returns before the spot handover is considered, so a skipped frame
    // costs nothing but the callback — and the handover is driven by `cycleStart`
    // against wall time, so nothing drifts for having been skipped.
    if (lastPaint && t - lastPaint < 1 / FPS - 0.001) {
      schedule();
      return;
    }
    lastPaint = t;

    // Holding still: drawn once, and nothing more is due.
    if (motion !== "full") {
      draw(t);
      return;
    }

    const spotSeconds = BREATHS_PER_SPOT * BREATH_PERIOD;
    if (t - cycleStart >= spotSeconds + HANDOVER) {
      cycleStart = t;
      spot = next;
      next = randomSpot();
    }
    draw(t);
    schedule();
  }

  /** Same split as donut.js: this is the backgrounded-page half, and it is not the
   *  half that helps a window merely sitting behind another one. */
  const onVisibility = () => {
    if (!running) return;
    if (document.hidden) {
      if (frameHandle !== null) cancelAnimationFrame(frameHandle);
      frameHandle = null;
      return;
    }
    lastPaint = 0;
    schedule();
  };

  const onResize = () => resize();
  // Window resize events can fire before layout settles (device-mode flips),
  // and some layout changes fire no window event at all. Observing the canvas's
  // own box covers both: it fires after layout, with the true final size.
  let observer = null;

  return {
    start() {
      if (running) return;
      running = true;
      resize();
      addEventListener("resize", onResize);
      document.addEventListener("visibilitychange", onVisibility);
      observer = new ResizeObserver(() => resize());
      observer.observe(canvas);
      lastPaint = 0;
      schedule();
    },
    /** full | calm | still (comfort.js). */
    setMotion(next) {
      const value = next === "calm" || next === "still" ? next : "full";
      if (value === motion) return;
      motion = value;
      cycleStart = nowSeconds();
      lastPaint = 0;
      schedule();
    },
    stop() {
      running = false;
      if (frameHandle !== null) cancelAnimationFrame(frameHandle);
      frameHandle = null;
      removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      ctx.clearRect(0, 0, width, height);
    },
  };
}