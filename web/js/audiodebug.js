/* Weft — the audio debug log.
 *
 * A panel that writes down what the browser did with every clip: made, started,
 * refused, ended, and what state the AudioContext was in at each step. It exists for
 * phones, where there is no console to read and the person holding the phone is not
 * the person debugging it. They take a screenshot, or press Copy, and that is enough.
 *
 * Off unless asked for. `?audiodebug` in the address turns it on and REMEMBERS it on
 * this device, because an app added to the home screen has no address bar to put the
 * flag back in after a reload. `?audiodebug=0` turns it off again, as does the Off
 * button on the panel.
 *
 * Every call is cheap when it is off: `log()` returns before touching the DOM, so the
 * hooks can sit in speech.js permanently rather than being added and removed per bug.
 */

import { el } from "./dom.js";

const KEY = "weft.audiodebug";
/** Enough for a long reply replayed twice; older lines fall off the top. */
const MAX_LINES = 300;

/**
 * Whether the log should be on, from the query string and the remembered flag.
 *
 * Pure, so it is testable without a browser. Returns the decision and what to store:
 * `store` is true or false when the address said something, null when it did not.
 */
export function decide(search, remembered) {
  const params = new URLSearchParams(search);
  if (!params.has("audiodebug")) return { on: remembered, store: null };
  const value = params.get("audiodebug");
  const on = value !== "0" && value !== "off" && value !== "false";
  return { on, store: on };
}

let on = false;
const lines = [];
const started = performance.now();
let panel = null;
let list = null;

function remembered() {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function remember(value) {
  try {
    if (value) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    /* a private window: on for this page only */
  }
}

export function enabled() {
  return on;
}

/** Seconds since the page loaded, to the millisecond — enough to see a gap. */
function stamp() {
  return ((performance.now() - started) / 1000).toFixed(3).padStart(8, " ");
}

/**
 * One line in the log. `detail` may be a string or a flat object; objects are written
 * as key=value so a screenshot stays readable on a narrow screen.
 */
export function log(event, detail) {
  if (!on) return;
  let text = event;
  if (detail && typeof detail === "object") {
    const parts = Object.entries(detail)
      .filter(([, value]) => value !== undefined)
      .map(([key, value]) => `${key}=${value}`);
    if (parts.length) text += " " + parts.join(" ");
  } else if (detail !== undefined) {
    text += " " + detail;
  }

  const line = `${stamp()} ${text}`;
  lines.push(line);
  if (lines.length > MAX_LINES) lines.shift();
  if (list) {
    list.append(el("div", { text: line }));
    while (list.childElementCount > MAX_LINES) list.firstElementChild.remove();
    list.scrollTop = list.scrollHeight;
  }
}

/**
 * Whether the page is inside a user gesture right now, and whether it ever has been.
 *
 * The first is the whole question on iOS: a clip started inside a tap may play, one
 * started from a timer or an `ended` event may not. Not every browser has the API, so
 * "?" means unknown rather than no.
 */
export function gesture() {
  const ua = navigator.userActivation;
  if (!ua) return "?";
  return `${ua.isActive ? "active" : "none"}/${ua.hasBeenActive ? "sticky" : "never"}`;
}

async function copy() {
  const text = lines.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    log("debug: copied", { lines: lines.length });
  } catch {
    // No clipboard (an http page, or an old WebView): select it instead, so a long
    // press can copy it by hand.
    const range = document.createRange();
    range.selectNodeContents(list);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    log("debug: clipboard refused, text selected");
  }
}

function build() {
  list = el("div", { class: "audiodebug-lines" });
  for (const line of lines) list.append(el("div", { text: line }));

  const toggle = el("button", { type: "button", text: "Hide" });
  toggle.addEventListener("click", () => {
    const hidden = panel.classList.toggle("collapsed");
    toggle.textContent = hidden ? "Audio log" : "Hide";
  });

  panel = el("div", { class: "audiodebug" }, [
    el("div", { class: "audiodebug-bar" }, [
      toggle,
      el("button", { type: "button", text: "Copy", onclick: copy }),
      el("button", {
        type: "button",
        text: "Clear",
        onclick: () => {
          lines.length = 0;
          list.replaceChildren();
        },
      }),
      el("button", {
        type: "button",
        text: "Off",
        onclick: () => {
          remember(false);
          on = false;
          panel.remove();
          panel = null;
          list = null;
        },
      }),
    ]),
    list,
  ]);
  document.body.append(panel);
  list.scrollTop = list.scrollHeight;
}

/** Read the flag and, if it is on, put the panel up. Called once, at boot. */
export function start() {
  const { on: wanted, store } = decide(location.search, remembered());
  if (store !== null) remember(store);
  on = wanted;
  if (!on) return;

  log("debug: on", { ua: navigator.userAgent });
  log("debug: support", {
    AudioContext: Boolean(window.AudioContext),
    webkitAudioContext: Boolean(window.webkitAudioContext),
    userActivation: Boolean(navigator.userActivation),
    standalone: navigator.standalone ?? matchMedia("(display-mode: standalone)").matches,
    audioSession: navigator.audioSession?.type ?? "none",
  });
  // What the phone did to the page. iOS moves the AudioContext to `interrupted` when
  // the screen locks or another app takes the sound, and a gap in the log is easier
  // to read with the reason written beside it.
  document.addEventListener("visibilitychange", () => log("page: " + document.visibilityState));
  window.addEventListener("pagehide", () => log("page: hide"));
  window.addEventListener("pageshow", (event) => log("page: show", { cached: event.persisted }));
  build();
}
