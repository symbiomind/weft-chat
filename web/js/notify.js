/* Weft — the system notification: the bell, rung outside the window.
 *
 * When a weft's agent leaves something through its door and this window is not being
 * looked at, the device says so the way any app does: a notification with the OS's own
 * sound, which needs no tap first, unlike audio played by the page.
 *
 * **Opt in, per device.** Nothing asks for permission until the user turns the switch
 * on, and the browser only shows its prompt from inside that tap anyway. The switch is
 * a property of where you are sitting, like the voice: on for the desktop, off for the
 * phone at night. The browser's permission is per site, so one switch covers every weft.
 *
 * **Only while a window is open.** The bell's own poll is what notices; this is not Web
 * Push, so a closed tab or a sleeping phone hears nothing. A hidden tab's timers are
 * slowed by the browser, so a notification can be a minute late.
 *
 * **Who, never what.** The notification names the weft and how many are waiting. The
 * text stays inside Weft, because a lock screen is read by whoever picks the phone up.
 *
 * Shown through the service worker (sw.js), because Android's Chrome refuses
 * `new Notification()` from a page; the worker also brings the window back on a tap.
 */

import { el } from "./dom.js";

const ON_KEY = "weft.notify";

/** Whether this browser can do it at all: a secure page, notifications, a worker. */
export function supported() {
  return globalThis.isSecureContext === true && "Notification" in globalThis &&
    "serviceWorker" in navigator;
}

function flag() {
  try {
    return localStorage.getItem(ON_KEY) === "1";
  } catch {
    return false;
  }
}

function setFlag(on) {
  try {
    if (on) localStorage.setItem(ON_KEY, "1");
    else localStorage.removeItem(ON_KEY);
  } catch {
    /* this visit still does as asked */
  }
}

/** "unsupported", "blocked" (the browser was told no), "on" or "off". */
export function state() {
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  return Notification.permission === "granted" && flag() ? "on" : "off";
}

export function isOn() {
  return state() === "on";
}

function register() {
  return navigator.serviceWorker.register("/sw.js").catch(() => null);
}

/** Bring the worker up on load when the switch is on, so the first ring is not late. */
export function start() {
  if (isOn()) register();
}

/** From inside a tap: ask the browser, and remember the answer on this device. */
export async function turnOn() {
  if (!supported()) return state();
  const answer = await Notification.requestPermission();
  setFlag(answer === "granted");
  if (answer === "granted") await register();
  return state();
}

export function turnOff() {
  setFlag(false);
  return state();
}

/**
 * Ring for one weft. `tag` folds repeats into one, so several open tabs, or several
 * polls before anyone looks, still make a single notification per weft.
 */
export async function ring(weftId, name, count) {
  if (!isOn()) return;
  const registration = await register();
  if (!registration) return;
  try {
    await registration.showNotification(name, {
      body: `${count} notification${count === 1 ? "" : "s"} waiting`,
      tag: `weft-${weftId}`,
      renotify: true,
      icon: "/icons/icon-192.png",
      data: { open: "notifications" },
    });
  } catch {
    /* permission withdrawn in the browser's settings since: the bell still shows it */
  }
}

/**
 * Listen for a tap on a notification. The worker focuses a window and tells it; the
 * window opens its notifications page. Returns the unsubscribe.
 */
export function onOpen(handler) {
  if (!("serviceWorker" in navigator)) return () => {};
  const listener = (event) => {
    if (event.data?.open === "notifications") handler();
  };
  navigator.serviceWorker.addEventListener("message", listener);
  // addEventListener, unlike onmessage, holds the messages back until asked.
  navigator.serviceWorker.startMessages();
  return () => navigator.serviceWorker.removeEventListener("message", listener);
}

const HINTS = {
  on: "On for this device, for every weft. Only while Weft is open in a tab, even a " +
    "hidden one. It says which weft and how many, never what they say.",
  off: "Have this device tell you when a weft's agent leaves you something while you " +
    "are looking elsewhere. One switch for every weft.",
  blocked: "This browser has been told not to allow Weft's notifications. Allow them in " +
    "the site's settings (the icon beside the address), then come back here.",
  unsupported: "This browser cannot do it here. It needs a secure (https) address, and " +
    "on an iPhone, Weft added to the home screen.",
};

/** The switch, for wherever it is wanted: one setting, shown in more than one place. */
export function notifySwitch() {
  const box = el("input", { type: "checkbox", name: "notify" });
  const hint = el("p", { class: "hint" });

  function paint(now = state()) {
    box.checked = now === "on";
    box.disabled = now === "blocked" || now === "unsupported";
    hint.textContent = HINTS[now];
  }

  box.addEventListener("change", async () => {
    paint(box.checked ? await turnOn() : turnOff());
  });
  paint();

  return el("div", { class: "notify" }, [
    el("label", { class: "toggle-row" }, [
      box,
      el("span", { text: "Notify me on this device" }),
      el("span", { class: "switch", "aria-hidden": "true" }),
    ]),
    hint,
  ]);
}
