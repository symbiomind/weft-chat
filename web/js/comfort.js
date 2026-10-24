/* Weft — comfort: how much moves, how much is see-through, and which side send is on.
 *
 * Three settings, per device, kept in localStorage beside the theme:
 *
 *   motion  full | calm | still
 *     full   as designed.
 *     calm   nothing large or looping moves. The donut holds still and changes colour
 *            by fading; no animation loops; a streamed reply is painted twice a second.
 *     still  nothing moves. No transitions; the donut is drawn once per change; a reply
 *            is painted a paragraph at a time.
 *
 *   glass  glass | less | solid
 *     glass  as designed: frosted surfaces over the floor.
 *     less   mostly opaque, a light blur. What macOS and iOS do for Reduce Transparency.
 *     solid  opaque surfaces and no backdrop blur at all.
 *
 *   hand   right | left — which side the send button (and its shelf) sits on.
 *
 * THE DEVICE IS A CEILING. What the operating system asks for wins over what this page
 * would like: `prefers-reduced-motion` caps motion at calm, `prefers-reduced-transparency`
 * and `prefers-contrast: more` cap glass at less, and `forced-colors` (Windows High
 * Contrast) means calm and solid. A user may always choose quieter than the device asks;
 * never louder. The Comfort screen shows the louder options switched off, and says why.
 *
 * Why the harm is where it is, so the inventory below stays honest: flashing (more than
 * three a second, large and contrasty) is what triggers photosensitive seizures, and
 * Weft does not flash. Large and full-screen motion is what triggers vestibular
 * disorders, and that is Weft's real exposure: the donut first, then ELIZA's sweep,
 * then the drawer and panel slides.
 *
 * The result is two attributes on <html>, `data-motion` and `data-glass`, plus
 * `data-hand`. CSS keys off them, and the canvases ask `current()` and `subscribe()`.
 * The login page obeys the device alone: it reads the same attributes, and nobody has
 * chosen anything there yet on a new device.
 */

const KEY = "weft.comfort";

/** Each list runs from the most to the least. A ceiling is a position in it. */
export const MOTION = ["full", "calm", "still"];
export const GLASS = ["glass", "less", "solid"];
export const HANDS = ["right", "left"];

const DEFAULTS = { motion: "full", glass: "glass", hand: "right" };

/** What the device says, as booleans. */
const QUERIES = {
  reducedMotion: "(prefers-reduced-motion: reduce)",
  reducedTransparency: "(prefers-reduced-transparency: reduce)",
  moreContrast: "(prefers-contrast: more)",
  forcedColors: "(forced-colors: active)",
};

/**
 * The most each setting may be, given what the device asks for.
 *
 * @param {{reducedMotion?: boolean, reducedTransparency?: boolean,
 *          moreContrast?: boolean, forcedColors?: boolean}} os
 */
export function ceilings(os = {}) {
  return {
    motion: os.reducedMotion || os.forcedColors ? "calm" : "full",
    glass: os.forcedColors ? "solid"
      : os.reducedTransparency || os.moreContrast ? "less" : "glass",
  };
}

/** The quieter of a choice and a ceiling, in `order`. Unknown values read as the most. */
export function quieter(choice, ceiling, order) {
  return order[Math.max(order.indexOf(choice), order.indexOf(ceiling), 0)];
}

/** Whether `value` is louder than the device allows, and so cannot be chosen. */
export function aboveCeiling(value, ceiling, order) {
  return order.indexOf(value) < order.indexOf(ceiling);
}

/** The stored choices, each defaulted on its own when missing or nonsense. */
export function readChoice(storage = globalThis.localStorage) {
  let raw = {};
  try {
    raw = JSON.parse(storage?.getItem(KEY) ?? "{}") ?? {};
  } catch {
    raw = {};
  }
  return {
    motion: MOTION.includes(raw.motion) ? raw.motion : DEFAULTS.motion,
    glass: GLASS.includes(raw.glass) ? raw.glass : DEFAULTS.glass,
    hand: HANDS.includes(raw.hand) ? raw.hand : DEFAULTS.hand,
  };
}

function writeChoice(choice, storage = globalThis.localStorage) {
  try {
    storage?.setItem(KEY, JSON.stringify(choice));
  } catch {
    /* private window or blocked storage: it still applies for this visit */
  }
}

/** What the device asks for right now. */
function readOs() {
  const os = {};
  for (const [name, query] of Object.entries(QUERIES)) {
    try {
      os[name] = matchMedia(query).matches;
    } catch {
      os[name] = false;
    }
  }
  return os;
}

/** @type {Set<(state: ReturnType<typeof current>) => void>} */
const listeners = new Set();
let choice = { ...DEFAULTS };

/**
 * Everything the screens need: what was chosen, what the device allows, what applies,
 * and what the device is asking for (so the screen can say why something is off).
 */
export function current() {
  const os = readOs();
  const ceiling = ceilings(os);
  return {
    choice: { ...choice },
    ceiling,
    os,
    motion: quieter(choice.motion, ceiling.motion, MOTION),
    glass: quieter(choice.glass, ceiling.glass, GLASS),
    hand: choice.hand,
  };
}

function apply() {
  const state = current();
  const root = document.documentElement;
  root.dataset.motion = state.motion;
  root.dataset.glass = state.glass;
  root.dataset.hand = state.hand;
  for (const listener of listeners) {
    try {
      listener(state);
    } catch (err) {
      console.error("comfort listener failed", err);
    }
  }
  return state;
}

/** Change one or more choices, save them, and apply what results. */
export function choose(next) {
  choice = readChoice({
    getItem: () => JSON.stringify({ ...choice, ...next }),
  });
  writeChoice(choice);
  return apply();
}

/** Told whenever what applies may have changed: a choice, or the device. */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

let started = false;

/** Read the stored choice, apply it, and follow the device from now on. Once. */
export function init() {
  if (started) return current();
  started = true;
  choice = readChoice();
  for (const query of Object.values(QUERIES)) {
    try {
      matchMedia(query).addEventListener("change", apply);
    } catch {
      /* an old browser without the query, or without listeners on it */
    }
  }
  return apply();
}
