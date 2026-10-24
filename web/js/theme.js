/* Weft — the theme system: auto / light / dark.
 *
 * Three states, and "auto" is the absence of an attribute rather than a value: with no
 * data-theme on <html>, the prefers-color-scheme media query in theme.css decides. An
 * explicit choice stamps data-theme and wins in both directions.
 *
 * The choice is the one thing this client puts in localStorage. It is a per-viewer
 * convenience with no security weight — unlike the access token, which must never go
 * near storage. Every access is wrapped, because storage throws rather than returning
 * null in a private window or when site data is blocked, and a theme preference is not
 * worth a blank page.
 */

const KEY = "weft.theme";
const MODES = ["auto", "light", "dark"];

/** @type {Set<(mode: string) => void>} */
const listeners = new Set();

function read() {
  try {
    const stored = localStorage.getItem(KEY);
    return MODES.includes(stored) ? stored : "auto";
  } catch {
    return "auto";
  }
}

function write(mode) {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* private window, blocked site data — the theme still applies for this session */
  }
}

export function current() {
  return read();
}

export function apply(mode) {
  const chosen = MODES.includes(mode) ? mode : "auto";
  const root = document.documentElement;

  if (chosen === "auto") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", chosen);
  }

  write(chosen);
  for (const listener of listeners) {
    try {
      listener(chosen);
    } catch (err) {
      console.error("theme listener failed", err);
    }
  }
  return chosen;
}

/** Notified on an explicit change AND when the OS flips while in auto. */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function init() {
  apply(read());

  // While in auto, the OS changing at dusk must repaint anything that sampled a CSS
  // variable into a canvas — the stylesheet updates itself, a canvas does not.
  try {
    const query = matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener("change", () => {
      if (read() === "auto") {
        for (const listener of listeners) {
          try {
            listener("auto");
          } catch (err) {
            console.error("theme listener failed", err);
          }
        }
      }
    });
  } catch {
    /* very old browser: an explicit toggle still works */
  }
}
