/* Weft — the donut: the substrate's heartbeat made visible.
 *
 * Lifted from the integration bench prototype with the render loop and
 * the mode machine intact. What changed is the shape of the seams, not the drawing:
 * the module owns a canvas it is handed, reads its palette from CSS custom properties,
 * and exposes start/stop/setMode/setPair instead of reaching for globals.
 *
 * Four modes, from the original design:
 *   idle     — slow breath, desaturated. The warp under tension, waiting for a pick.
 *   waiting  — a heartbeat in the weft's colour. The model is thinking.
 *   speaking — the ring becomes a waveform.
 *   inbound  — mint, a faster beat. Somebody reached in unbidden.
 *
 * The palette is re-sampled from CSS on theme change rather than hardcoded, so the
 * canvas follows the stylesheet instead of drifting from it.
 */

const PALETTE_KEYS = ["--bg", "--dim", "--fern", "--twilight"];

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * THE KNOBS. Deliberately one small block with names, because getting this to feel
 * right is a matter of taste and taste needs somewhere obvious to be adjusted.
 *
 * Tuned high on purpose for the first pass — better to see the effect clearly and pull
 * it back than to wonder whether it is working at all.
 */
/**
 * THE KNOBS for how a voice moves the ring.
 *
 * These replaced a spectrum analyser. The short version: a spectrum is a
 * LINE with two ends and a ring is a CIRCLE with none, so any continuous mapping of one
 * onto the other has to fold — which meant the only outcomes available were a tear or a
 * mirror. We had the tear, then we had the mirror, and both looked wrong because the
 * direction was wrong.
 *
 * The donut was never a readout. The original design has always called it the substrate's
 * heartbeat made visible, a room you share rather than a terminal you query. So it
 * breathes: the generated motion is left exactly alone and loudness simply deepens it.
 * **The baby never holds its breath; speaking just deepens it.**
 *
 * It is also the only thing that is legible on a shape which is 80% offscreen by
 * design. Peripheral vision reads size and motion, never structure.
 */
export const VOICE_KNOBS = {
  //: How hard to lean on the raw reading. speech.js reports plain RMS, which sits low
  //: for speech — roughly 0.05 to 0.2 even for a confident voice — so unscaled it would
  //: barely move anything. The measurement is left honest and the drama lives here.
  sensitivity: 4.5,
  //: How much a loud moment grows the ring's radius. This is the "expand and contract"
  //: — the part you can read from across the room, and the one worth being generous
  //: with: most of the ring is offscreen by design, so the only thing a viewer can
  //: actually see move is the edge.
  swell: 0.34,
  //: How much louder speech deepens the wibble itself, so the ring is breathing harder
  //: rather than merely inflating.
  deepen: 1.4,
  //: Envelope time constants. Fast up so a hit is felt, slow down so the release is
  //: savoured — a lung bounce rather than a needle.
  //:
  //: 200/400 was the first proposal. 80 is the instinct for "feels like a hit" rather than
  //: "follows along shortly afterwards"; both are one edit away and this is a tune-by-
  //: ear number, not a correct one.
  attackSeconds: 0.08,
  releaseSeconds: 0.4,
};

/**
 * THE RIPPLES, and why every `lobes` here is a whole number.
 *
 * A ripple travels around a circle with no join only when a whole number of
 * wavelengths fits the circumference exactly — λ = 2πr / n. The ripple is quantised:
 * only certain wavelengths are allowed around a ring, and each one closes perfectly,
 * ending bit-identical to where it began. Miss the whole number by any amount and a
 * seam appears; hit it and the join is not hidden, it is **impossible**.
 * (Newton's rings, Bohr, Hawking ringdown: the universe already only
 * permits ripples that close their own circle.)
 *
 * This is what replaced a spectrum analyser. That direction needed a line wrapped onto
 * a circle, which forced a fold, which forced a tear or a mirror. A travelling wave
 * needs no wrapping at all — it is *born* periodic, so there is nothing to join.
 *
 * It also fixed the other complaint for free. The old wave carried a Gaussian envelope
 * peaking at 9 o'clock and collapsing to 4% at 3 o'clock, so on a landscape screen one
 * visible side breathed and the other sat nearly still. A travelling wave has no front:
 * every part of the ring gets its turn.
 *
 * `lobes` is the vocabulary — low is a lazy swell, high is a nervous shimmer.
 * `speed` is revolutions per second OF THE PATTERN, positive being anticlockwise.
 *
 * **k = 42 is load-bearing.** Forty-two whole wavelengths around the ring is the Answer
 * to Life, the Universe and Everything, and it is also a legal quantum — the donut
 * closes its circle *because* it is a whole number, and 42 is the whole number we
 * chose. Remove the gag and the seam comes back. The joke and the physics are the same
 * equation. (The ring's radius has been `max(width, height) * 0.42` since the bench,
 * which nobody planned.)
 */
export const WAVE_HARMONICS = [
  //: The shape. Seven bellies swinging, with a three-lobe swell rolling underneath.
  //:
  //: ODD, and that is not a preference. For odd n opposite sides of the ring are exactly
  //: antiphase — sin(n(θ+π)) = -sin(nθ) — so when the left bulges out the right pulls
  //: in and the donut sways. An even count puts them in phase and both sides bulge
  //: together, which on a landscape screen (where left and right are precisely what is
  //: not clipped) is the difference between a creature and a pulsing washer. Going up
  //: or down from here means 5 or 9, never 6 or 8.
  { lobes: 7, flip: 0.47, drift: 0.020, amplitude: 0.27 },
  { lobes: 3, flip: 0.31, drift: 0.013, amplitude: 0.18 },
  //: A hint of finer motion so the swing is not perfectly smooth. Kept small: at any
  //: real amplitude nine lobes stop reading as a wave and start reading as bumps.
  { lobes: 9, flip: 0.63, drift: 0.031, amplitude: 0.035 },
  //: The Answer, and genuinely subliminal at roughly 1% of the total — forty-two
  //: wavelengths is texture, not shape, as the bench confirmed the hard way. It stays
  //: because it is a legal quantum: the ring closes BECAUSE the number is whole, and
  //: this is the whole number we chose. You cannot see it. It is still load-bearing.
  { lobes: 42, flip: 0.09, drift: 0.004, amplitude: 0.004 },
];

const TAU = Math.PI * 2;

/**
 * How many points the ring is drawn with.
 *
 * Raised from 128 for k = 42: forty-two wavelengths across 128 points is roughly three
 * samples per cycle, which is past Nyquist and renders as aliased jitter rather than as
 * shimmer. 384 gives about nine samples per cycle, which draws cleanly.
 *
 * Note this has nothing to do with the seam — a whole number of wavelengths closes the
 * ring at ANY resolution, because the value at point P is exactly the value at point 0.
 * Resolution only decides whether the ripple is legible once it is there.
 */
export const RING_POINTS = 384;

function clamp01(value) {
  return Number.isFinite(value) ? Math.min(Math.max(value, 0), 1) : 0;
}

/**
 * Trace the ring onto `c`, every length multiplied by `k`.
 *
 * Exported, and `k` exists, because design/archive/5-fidelity.html draws this same path onto
 * reduced-resolution canvases to compare rendering strategies. `k` is passed rather
 * than set as a transform on those contexts deliberately — see design/archive/bloom.js: filter
 * blur radii are DEVICE pixels and ignore the transform, so a scratch canvas that
 * scales its geometry with the CTM ends up dpr times too soft.
 *
 * `wave` null means a plain circle, which is three of the four modes.
 */
export function traceRing(c, k, cx, cy, r, ring, wave, waveAmp, loudness) {
  c.beginPath();

  if (!wave) {
    c.arc(cx * k, cy * k, r * k, 0, TAU);
    return;
  }

  for (let i = 0; i <= RING_POINTS; i += 1) {
    const a = (i / RING_POINTS) * TAU;
    const w = wave[i % RING_POINTS];
    const wv = r + (w - 0.5) * ring * 3.2 * waveAmp * (1 + loudness * VOICE_KNOBS.deepen);
    const x = (cx + Math.cos(a) * wv) * k;
    const y = (cy + Math.sin(a) * wv) * k;
    i ? c.lineTo(x, y) : c.moveTo(x, y);
  }

  c.closePath();
}

/**
 * The ring's own motion at a moment in time: standing waves that slowly precess.
 *
 * STANDING, not travelling, and the difference is the whole look. A travelling wave is
 * a rigid shape sliding around the circle — it never flips peak to trough, so the ring
 * reads as an awkward object being dragged round rather than as something alive. A
 * standing wave holds its nodes still and lets the bellies between them swing through
 * peak, flat, trough and back. That is a vibrating string, and a string closed into a
 * loop is the picture everyone has seen of one.
 *
 * The two are the same thing seen differently:
 *
 *     sin(nθ - ωt) + sin(nθ + ωt) = 2 sin(nθ) cos(ωt)
 *
 * — a standing wave IS two travelling waves going opposite ways. Make them unequal and
 * the standing pattern also drifts, which is what `drift` does here: the flipping
 * happens in a frame that is itself turning slowly. Waves moving up and down while
 * going slowly around.
 *
 * `lobes` must be a whole number, and that is not style. A wave closes a circle only
 * when a whole number of wavelengths fits the circumference — miss it by any amount and
 * a seam appears; hit it and the join is not hidden, it is impossible
 *. This is the property that ended a two-day argument with a spectrum
 * analyser, which needed a line wrapped onto a circle and so could only ever be torn or
 * mirrored.
 *
 * **ODD lobe counts, deliberately.** For odd n, opposite sides of the ring are exactly
 * antiphase — sin(n(θ+π)) = -sin(nθ) — so when the left bulges out the right pulls in,
 * and the donut sways. Even n puts them in phase and both sides bulge together, which
 * is symmetrical and dull. On a landscape screen, where left and right are precisely
 * what is not clipped, that is the whole character of the thing.
 *
 * `flip` is peak-to-trough-to-peak cycles per second. `drift` is revolutions per second
 * of the pattern, positive being anticlockwise.
 */
export function standingWave(t, harmonics = WAVE_HARMONICS, points = RING_POINTS) {
  const out = new Array(points);
  const time = Number.isFinite(t) ? t : 0;

  // The flipping half only depends on time, so it is computed once per harmonic rather
  // than once per point — 384 points times four harmonics, sixty times a second.
  const swing = harmonics.map((h) => Math.cos(TAU * h.flip * time));

  for (let i = 0; i < points; i += 1) {
    const angle = (i / points) * TAU;
    let value = 0.5;

    for (let h = 0; h < harmonics.length; h += 1) {
      const { lobes, drift, amplitude } = harmonics[h];
      value += amplitude * Math.sin(lobes * (angle + TAU * drift * time)) * swing[h];
    }

    out[i] = Number.isFinite(value) ? value : 0.5;
  }

  return out;
}

/**
 * Move `current` towards `target` with a fast attack and a slow release.
 *
 * Raw loudness pumps and judders — it is a measurement, and a measurement moves like a
 * needle. An envelope turns it into something with weight: it rises almost as fast as
 * the sound does and falls away slowly, which is what makes a swell read as breath
 * rather than as data.
 *
 * Exponential and frame-rate independent, matching the easing idiom already used for
 * the ring's colour. Doing this per-frame instead would make the donut breathe faster
 * on a 120Hz display than on a 60Hz one, which is the kind of bug that is invisible on
 * the machine it was written on.
 *
 * Pure and exported so the asymmetry can actually be asserted — "attack is faster than
 * release" is the whole character of it, and it is one swapped variable away from being
 * silently backwards.
 */
export function envelope(current, target, dt, knobs = VOICE_KNOBS) {
  const from = Number.isFinite(current) ? current : 0;
  const to = clamp01(target);
  if (!Number.isFinite(dt) || dt <= 0) return from;

  const tau = to > from ? knobs.attackSeconds : knobs.releaseSeconds;
  if (!(tau > 0)) return to;

  const k = 1 - Math.exp(-dt / tau);
  const next = from + (to - from) * k;
  if (!Number.isFinite(next)) return to;

  // Settle exactly rather than asymptotically. An exponential approaches its target
  // forever without arriving, which would leave the ring carrying a vanishing but
  // permanent fraction of the last thing it heard. The threshold is far below anything
  // a pixel can show, and it makes "a silent window looks precisely as it did before
  // any audio existed" literally true instead of nearly true.
  return Math.abs(to - next) < 1e-4 ? to : next;
}

/**
 * THE FRAME GATE.
 *
 * Measured on an integrated Vega at a 1440p-class stage, bloom already off: an IDLE
 * window cost 36.4% of the GPU and a fifth of a core, against 5.2%/0.9% for a blank
 * page. That is the tax that starved a YouTube window in another process — the ring
 * sustained a clean 60fps throughout, so frame rate never reported it.
 *
 * The surprise, and the reason this is a per-mode map rather than one number: idle and
 * speaking cost the SAME (36.4% against 39.3%). The earlier reasoning had it that the
 * expensive modes were also the rare ones, but the cost here is rasterisation AREA —
 * a gradient-filled annulus some 140 device pixels wide — and every mode paints the
 * same annulus. The 384-point wave is noise next to it.
 *
 * So the common case is the expensive one, and gating it is where the win is. Speaking
 * and inbound stay free because `attackSeconds` is 0.08: at 20fps a voice hit would get
 * 1.6 frames of attack and land as a stair-step. Those modes are loud, brief and looked
 * at directly; idle is none of the three.
 */
const FPS_BY_MODE = { idle: 20, waiting: 20, speaking: 60, inbound: 60 };

/**
 * THE RING WHEN MOTION IS CALM OR STILL (comfort.js).
 *
 * The donut is Weft's largest moving thing, a ring the size of the viewport, which makes
 * it the first thing to hold still for anyone whom motion makes ill. It keeps telling
 * the state, by colour and strength instead of by beat: grey and faint when idle, the
 * weft's own pair when it is thinking or speaking, mint when something arrives.
 *
 * Calm cross-fades between them (a change of colour, not of place) and then stops
 * drawing. Still snaps and draws once. Either way nothing is painted between changes.
 */
const STILL_ALPHA = { idle: 0.38, waiting: 0.75, speaking: 0.85, inbound: 0.9 };

/** How long a calm cross-fade is given to finish before the ring stops drawing. */
const CALM_SETTLE_MS = 1500;

/** Slack, in ms, so a target that divides unevenly into the display's cadence lands on
 *  the nearer vsync rather than the next one — without it a 20fps gate on a 60Hz panel
 *  rounds down to 15. */
const GATE_SLACK_MS = 1;

export function createDonut(canvas) {
  const ctx = canvas.getContext("2d");
  // Re-read on resize rather than captured once: devicePixelRatio changes under a
  // browser zoom or a drag onto a monitor of a different density, and a backing store
  // still sized for the old one is either soft or wastefully large.
  let dpr = Math.min(devicePixelRatio || 1, 2);

  let mode = "idle";
  /** full | calm | still: see STILL_ALPHA. */
  let motion = "full";
  /** While calm or still, frames are drawn only until this time, then the ring rests. */
  let settleUntil = 0;
  let pair = null; // { a, b }
  let width = 0;
  let height = 0;
  let running = false;
  let frameHandle = null;
  let keepalive = null;
  /** Per-mode frame ceiling; `setFps` replaces it. */
  let fpsByMode = { ...FPS_BY_MODE };
  /** When the last frame was actually PAINTED, as opposed to merely delivered. */
  let lastPaint = 0;

  let palette = {};

  /**
   * How the halo gets drawn, and why the answer is "it doesn't".
   *
   * The perf work started out trying to make the feathered bloom cheap
   * and ended up establishing that it should not be there at all. Two findings, in
   * the order they arrived:
   *
   * 1. It cost 164.9 ms of a 182.2 ms frame at 1440p — 90.5% of the render, eleven
   *    60Hz budgets — because `ctx.filter` blurs a layer the size of the CLIP REGION
   *    rather than of the thing being drawn. Fixable: see design/archive/bloom.js, which got it
   *    to about 3.5x faster and pixel-faithful.
   *
   * 2. Then someone looked at the thing we were saving. **The halo bands.** Its alpha
   *    ramp falls from roughly 0.56 to nothing across some 250 device pixels, and 8
   *    bits of alpha cannot describe a gradient that gradual — so it steps, in rings,
   *    every couple of pixels. "A million shades needed but only using a few." And it
   *    gets WORSE on a larger screen, because the ramp lengthens while the number of
   *    available levels stays at 256. The most expensive thing on the canvas was also
   *    the thing that looked worst on exactly the screens we were buying headroom for.
   *
   * Over a backdrop it is invisible, and the wide low-alpha stroke it needs actually
   * veils the portrait underneath — turning it off lets more of Iris through. On flat
   * background nobody could pick the blurred halo from no halo at all. So: off, and
   * the ring runs at a full 60fps.
   *
   * "gaussian" stays so design/archive/4-perf.html can still price what was taken away — six
   * lines, no import, and a claim this large should remain checkable rather than
   * become folklore. The two replacements that were built and measured before the
   * halo was cut altogether now live in design/archive/bloom.js, out of the shipped path; that
   * file's header has their numbers and design/archive/5-fidelity.html still renders them.
   * (The benches under design/archive/ are local working files, not published.)
   */
  let bloom = "off";
  /**
   * A function returning how loud the voice is right now, 0..1, or null for silence.
   *
   * ONE NUMBER. That is the whole interface, and it is deliberately smaller than the
   * spectrum it replaced — the donut does not know what WebAudio is, does not hold an
   * AudioContext, cannot make a sound, and now does not know what a frequency is
   * either. It asks how loud, once a frame.
   */
  let levelSource = null;

  //: The enveloped loudness — what the ring actually breathes on. Kept apart from the
  //: raw reading because raw loudness judders and breath does not.
  let voice = 0;
  /**
   * The pick's background picture, and the URL it was loaded from.
   *
   * A fresh Image per picture rather than one reused element with a new `src`: a load
   * still in flight when the pick changes would otherwise land late and paint the
   * previous weft's picture behind the new one. Each load's handlers check that their
   * image is still the current one before they touch anything.
   */
  let backdrop = null;
  let backdropSrc = null;
  let backdropReady = false;
  /**
   * The portrait, already scaled to this canvas.
   *
   * The cover-fit maths is fixed for a given image and a given box, so doing it inside
   * the frame meant re-scaling a full-viewport photograph sixty times a second to
   * produce the identical pixels each time. This one was called in advance:
   * "cache the static backdrop, redraw only the ring" — before there was a
   * portrait to be slow about. Now there is somewhere for it to land.
   */
  let backdropLayer = null;

  /** Re-scale the portrait into `backdropLayer`. Cheap to call; it no-ops until there
   *  is both an image and a laid-out canvas to fit it to. */
  function cacheBackdrop() {
    if (!backdrop || !backdropReady || !width || !height) return;
    if (!backdrop.complete || !backdrop.naturalWidth) return;

    const w = Math.round(width * dpr);
    const h = Math.round(height * dpr);
    if (!backdropLayer) backdropLayer = document.createElement("canvas");
    backdropLayer.width = w;
    backdropLayer.height = h;

    const c = backdropLayer.getContext("2d");
    const s = Math.max(w / backdrop.width, h / backdrop.height);
    c.drawImage(
      backdrop,
      w / 2 - (backdrop.width * s) / 2,
      h / 2 - (backdrop.height * s) / 2,
      backdrop.width * s,
      backdrop.height * s,
    );
  }

  /** Show `src` behind the ring, or nothing for null. Repeating the current one is free. */
  function loadBackdrop(src) {
    if (src === backdropSrc) return;
    backdropSrc = src;
    backdropReady = false;
    backdropLayer = null;
    backdrop = null;
    if (!src) {
      wake();
      return;
    }

    const img = new Image();
    img.onload = () => {
      if (img !== backdrop) return;
      backdropReady = true;
      cacheBackdrop();
      wake();
    };
    img.onerror = () => {
      if (img !== backdrop) return;
      // Not an error worth surfacing: a picture that will not decode leaves the stage
      // as it would be without one. The ring still breathes.
      backdropReady = false;
      backdropLayer = null;
    };
    backdrop = img;
    img.src = src;
  }

  function refreshPalette() {
    palette = Object.fromEntries(PALETTE_KEYS.map((k) => [k.slice(2), cssVar(k)]));
    // What the ring is drawn over, which is not always the page: ELIZA's floor is dark
    // in either theme (app.css, --floor). Light or dark is judged on that.
    palette.bg = cssVar("--floor") || palette.bg;
    const light = palette.bg.startsWith("#f") || palette.bg.startsWith("#e");
    palette.idle = light ? "#b8b4a6" : "#4a5060";
    palette.mint = "#4ECDC4";
    palette.mintDeep = "#2a9d8f";
    wake();
  }

  function resize() {
    // Clamp to the window, exactly as breathers.js does and for exactly the same
    // reason: during a device flip or a zoom transition an inset:0 element can
    // transiently report an enormous untransformed box, and `clientWidth * dpr` on
    // that asks the browser for a multi-gigapixel backing store. The canvas can never
    // legitimately be larger than the window it is pinned to.
    const w = Math.min(canvas.clientWidth, Math.max(innerWidth, 1));
    const h = Math.min(canvas.clientHeight, Math.max(innerHeight, 1));
    if (!w || !h) return; // not laid out yet; the observer will re-fire

    // Writing canvas.width reallocates the backing store AND clears the canvas, even
    // when the number is unchanged. `resize` fires in bursts while a window is dragged,
    // so without this the donut spends a drag throwing away a full-viewport buffer
    // sixty times a second to draw the same size again.
    const nextDpr = Math.min(devicePixelRatio || 1, 2);
    if (w === width && h === height && nextDpr === dpr) return;

    width = w;
    height = h;
    dpr = nextDpr;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cacheBackdrop();
    // A resize cleared the canvas, and a resting ring would otherwise stay blank.
    wake();
  }

  // ---- colour helpers ----
  function hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16),
    ];
  }

  function mix(from, to, f) {
    const a = hexToRgb(from);
    const b = hexToRgb(to);
    return (
      "#" +
      a
        .map((v, i) => Math.round(v + (b[i] - v) * f).toString(16).padStart(2, "0"))
        .join("")
    );
  }

  function modeVisual(m) {
    const tint = pair?.a
      ? { a: pair.a, b: pair.b }
      : { a: palette.fern, b: palette.twilight };

    switch (m) {
      case "waiting":
        return { a: tint.a, b: tint.b, glowA: 0.35 };
      case "speaking":
        return { a: tint.a, b: tint.b, glowA: 0.35 };
      case "inbound":
        return { a: palette.mint, b: palette.mintDeep, glowA: 0.45 };
      case "idle":
      default:
        return { a: palette.idle, b: palette.idle, glowA: 0.1 };
    }
  }

  const cur = { a: "#4a5060", b: "#4a5060", glowA: 0.1, waveAmp: 0 };
  let t0 = performance.now();
  let last = null;

  /**
   * Ask for exactly one frame.
   *
   * `frameHandle` doubles as "a frame is already pending", and that guard is the whole
   * point. rAF callbacks do not run in a hidden tab, they QUEUE — so the keepalive
   * below, which fires every 250ms regardless of whether anything is drawing, used to
   * append a fresh callback to that queue four times a second for as long as the tab
   * stayed hidden. Come back after five minutes and roughly twelve hundred of them run
   * on the same frame, each one tail-calling rAF on its way out, and the donut is now
   * running twelve hundred independent render loops. Forever: stop() could not undo it
   * either, because frameHandle only ever remembered the most recently scheduled one.
   *
   * With the guard every extra nudge lands on a handle that is already set and does
   * nothing at all. One loop in, one loop out.
   */
  /**
   * Something changed that a resting ring must show: a mode, a pair, a size, a picture.
   * In full motion the loop is running anyway, and this only makes sure of it.
   */
  function wake() {
    settleUntil = performance.now() + (motion === "calm" ? CALM_SETTLE_MS : 0);
    lastPaint = 0;
    schedule();
  }

  function schedule() {
    if (frameHandle !== null) return;
    // A hidden page gets nothing queued at all. rAF already declines to RUN while
    // hidden, but it happily accumulates, and the keepalive below is a caller that
    // does not know any better. Resumed by `onVisibility`.
    if (document.hidden) return;
    frameHandle = requestAnimationFrame(frame);
  }

  /**
   * The feathered bloom, which by default is not painted at all.
   *
   * The only thing here is the original, and it is here to be measured rather than to
   * be used. See the note on `bloom` above for why nothing calls it.
   */
  function paintBloom(glow, cx, cy, r, ring, wave) {
    if (bloom === "off" || !(glow > 0)) return;

    // It re-traces the path rather than re-stroking the one already in the context;
    // next to a full-canvas Gaussian that is not a cost worth the asymmetry.
    ctx.save();
    ctx.filter = `blur(${ring * 0.9}px)`;
    ctx.globalAlpha = glow;
    ctx.lineWidth = ring * 1.1;
    traceRing(ctx, 1, cx, cy, r, ring, wave, cur.waveAmp, voice);
    ctx.stroke();
    ctx.restore();
  }

  function frame(now) {
    // This callback has fired, so the handle it was scheduled under is spent. Clearing
    // it first is what lets schedule() tell "a frame is pending" from "the loop ran".
    frameHandle = null;
    if (!running) return;
    // Went hidden between the request and the callback. Nothing to draw, and nothing
    // scheduled either — onVisibility restarts the loop.
    if (document.hidden) return;

    // THE GATE. Delivered frames are cheap; painted ones are not, so this returns
    // before `last` is touched and dt keeps measuring paint-to-paint. See FPS_BY_MODE.
    const moving = motion === "full";
    const minInterval = 1000 / (moving ? fpsByMode[mode] || 60 : 20) - GATE_SLACK_MS;
    if (lastPaint && now - lastPaint < minInterval) {
      schedule();
      return;
    }
    lastPaint = now;

    const t = (now - t0) / 1000;
    const dt = Math.min((now - (last ?? now)) / 1000, 0.05);
    last = now;

    ctx.clearRect(0, 0, width, height);
    const cx = width / 2;
    const cy = height / 2;

    // A straight blit of an already-correct layer: the scale happened once, in
    // cacheBackdrop(). Drawn at CSS size because the context carries the dpr transform.
    if (backdropLayer) {
      ctx.drawImage(backdropLayer, 0, 0, width, height);
    }

    const R = Math.max(width, height) * 0.42;
    const ring = R * 0.1;

    const v = modeVisual(mode);
    // Frame-rate independent easing; still motion snaps rather than fades.
    const k = motion === "still" ? 1 : 1 - Math.pow(0.002, dt);
    cur.a = mix(cur.a, v.a, k);
    cur.b = mix(cur.b, v.b, k);
    cur.glowA += (v.glowA - cur.glowA) * k;
    cur.waveAmp += ((moving && mode === "speaking" ? 1 : 0) - cur.waveAmp) * k;

    // Guarded, because the source reaches into an AudioContext this module does not own
    // and cannot vouch for. An exception here escapes the frame, which means the line
    // that schedules the NEXT frame never runs and the ring freezes solid, permanently,
    // over one bad read. Falling back to silence is the only acceptable failure.
    let heard = 0;
    try {
      heard = levelSource?.() ?? 0;
    } catch {
      heard = 0;
    }
    // Only while something is speaking, so a replay on an idle window does not move the
    // ring — the same rule as before, and it still costs nothing to keep.
    voice = moving ? envelope(
      voice,
      cur.waveAmp > 0.01 ? clamp01(heard * VOICE_KNOBS.sensitivity) : 0,
      dt,
    ) : 0;

    let pulse = 1;
    let alpha = 1;
    if (!moving) {
      alpha = STILL_ALPHA[mode] ?? STILL_ALPHA.idle;
    } else if (mode === "idle") {
      pulse = 1 + 0.03 * Math.sin(t * 0.9);
      alpha = 0.35 + 0.08 * Math.sin(t * 0.9);
    } else if (mode === "waiting") {
      const ph = t % 1;
      const beat =
        ph < 0.12 ? 1 - ph / 0.12 : ph > 0.2 && ph < 0.34 ? 1 - (ph - 0.2) / 0.14 : 0;
      pulse = 1 + 0.1 * beat;
      alpha = 0.55 + 0.45 * beat;
    } else if (mode === "speaking") {
      alpha = 0.8;
    } else if (mode === "inbound") {
      const ph = t % 1.2;
      const beat = ph < 0.15 ? 1 - ph / 0.15 : 0;
      pulse = 1 + 0.16 * beat;
      alpha = 0.6 + 0.4 * beat;
    }

    // The voice swells the ring on top of whatever the mode is already doing — never
    // instead of it. Silence leaves this at exactly 1, so a quiet window looks precisely
    // as it did before any of this existed.
    const r = R * pulse * (1 + voice * VOICE_KNOBS.swell);
    const gradient = ctx.createRadialGradient(cx, cy, r - ring, cx, cy, r + ring);
    gradient.addColorStop(0, cur.a);
    gradient.addColorStop(1, cur.b);
    ctx.globalAlpha = alpha;

    let wave = null;
    if (cur.waveAmp > 0.01) {
      // Untouched by the voice. The generated wave is the creature's own motion; what
      // loudness does is deepen it, further down, rather than reshape it here.
      wave = standingWave(t);
    }
    traceRing(ctx, 1, cx, cy, r, ring, wave, cur.waveAmp, voice);
    ctx.lineWidth = ring * 1.3;
    ctx.strokeStyle = gradient;
    ctx.stroke();

    paintBloom(alpha * cur.glowA * 2, cx, cy, r, ring, wave);

    ctx.globalAlpha = 1;
    // Resting: the ring is drawn as it should be, and nothing is due until it changes.
    if (!moving && now >= settleUntil) return;
    schedule();
  }

  const onResize = () => resize();

  /**
   * Stop dead while the page is hidden, and pick up cleanly when it is not.
   *
   * This is the mobile half of the perf work. With the screen off, TTS keeps
   * playing — it is an `<audio>` element and survives backgrounding by design — but the
   * page around it was still being asked to breathe, and the OS was throttling exactly
   * the thread the audio needed. Weft competing with its own voice.
   *
   * It is NOT the desktop half, and that distinction cost some confusion: a weft window
   * sitting visible behind another window is not hidden, so nothing here fires and the
   * gate above is the only thing holding the cost down. Both are needed; neither
   * substitutes for the other.
   */
  const onVisibility = () => {
    if (!running) return;
    if (document.hidden) {
      if (frameHandle !== null) cancelAnimationFrame(frameHandle);
      frameHandle = null;
      return;
    }
    // Both clocks are stale by however long we were away. `last = null` makes the next
    // frame's dt zero rather than enormous; `lastPaint = 0` lets it paint immediately
    // instead of waiting out a gate interval measured from before the gap.
    last = null;
    lastPaint = 0;
    schedule();
  };
  // Window `resize` misses a box that changes without the window doing so — a panel
  // opening, an address bar retracting. Observing our own box covers both, and it
  // fires after layout with the true final size. Same pairing as breathers.js.
  let observer = null;

  return {
    start() {
      if (running) return;
      running = true;
      refreshPalette();
      cur.a = palette.idle;
      cur.b = palette.idle;
      resize();
      addEventListener("resize", onResize);
      document.addEventListener("visibilitychange", onVisibility);
      observer = new ResizeObserver(() => resize());
      observer.observe(canvas);
      t0 = performance.now();
      last = null;
      lastPaint = 0;
      schedule();

      // rAF stops in a background tab; when it resumes, `last` is stale by however
      // long the tab was hidden. This nudge restarts the loop and the dt clamp above
      // keeps the easing from jumping. It is safe to fire into a running loop only
      // because schedule() refuses to queue a second one — see the note there.
      keepalive = setInterval(() => {
        const resting = motion !== "full" && performance.now() >= settleUntil;
        if (running && !resting && (last === null || performance.now() - last > 250)) {
          schedule();
        }
      }, 250);
    },

    stop() {
      running = false;
      if (frameHandle !== null) cancelAnimationFrame(frameHandle);
      if (keepalive !== null) clearInterval(keepalive);
      frameHandle = null;
      keepalive = null;
      removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      if (observer) {
        observer.disconnect();
        observer = null;
      }
    },

    setMode(next) {
      if (next === mode) return;
      mode = next;
      wake();
    },

    /** full | calm | still (comfort.js). Anything else is full. */
    setMotion(next) {
      const value = next === "calm" || next === "still" ? next : "full";
      if (value === motion) return;
      motion = value;
      wake();
    },

    /** @param {{a:string,b:string}|null} next */
    setPair(next) {
      pair = next;
      wake();
      const root = document.documentElement;

      if (next?.a) {
        root.style.setProperty("--weft-a", next.a);
        root.style.setProperty("--weft-b", next.b);
      } else {
        root.style.removeProperty("--weft-a");
        root.style.removeProperty("--weft-b");
      }
    },

    /**
     * Paint a picture behind the ring, or none. Separate from setPair because the two
     * are separate choices: a pick can have a picture and the theme's colours, or
     * colours and no picture. Cover-fit and centred, so a portrait screen keeps the top
     * and bottom of the picture and a landscape one keeps the sides.
     *
     * @param {string|null} src  any URL an Image can load; the window passes a blob URL
     */
    setBackdrop(src) {
      loadBackdrop(src || null);
    },

    /**
     * Raise or lower the per-mode frame ceiling — the override the mobile frame-rate work asks for, and what
     * design/archive/6-idle.html drives to price a gate setting. Unknown modes are ignored; a
     * mode left out keeps its default.
     *
     * @param {Partial<Record<"idle"|"waiting"|"speaking"|"inbound", number>>} next
     */
    setFps(next) {
      for (const [key, value] of Object.entries(next ?? {})) {
        if (key in fpsByMode && Number.isFinite(value) && value > 0) {
          fpsByMode[key] = value;
        }
      }
      lastPaint = 0;
    },

    /** "off" (default) | "gaussian", for design/archive/4-perf.html. See the note on `bloom`. */
    setBloom(next) {
      if (next === "off" || next === "gaussian") bloom = next;
    },

    /** Re-sample CSS custom properties. Call when the theme changes. */
    refreshPalette,

    /** Hand the donut something to listen to, or null for a ring that only breathes. */
    setLevelSource(fn) {
      levelSource = typeof fn === "function" ? fn : null;
    },
  };
}
