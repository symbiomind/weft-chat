/* Weft — the system notification: the bell, rung outside the window.
 *
 * When a weft's agent leaves something through its door, the device says so the way
 * any app does: a notification with the OS's own sound, which needs no tap first,
 * unlike audio played by the page. Even with Weft in front, because deep in one weft's
 * chat another's bell is easy to miss; not while the notifications page is open.
 *
 * **Opt in, per device.** Nothing asks for permission until the user turns the switch
 * on, and the browser only shows its prompt from inside that tap anyway. The switch is
 * a property of where you are sitting, like the voice: on for the desktop, off for the
 * phone at night. The browser's permission is per site, so one switch covers every weft.
 *
 * **Open, the bell's poll rings it.** A hidden tab's timers are slowed by the browser,
 * so that can be a minute late.
 *
 * **Closed, Web Push rings it, where the server pushes.** Turning the switch on also
 * subscribes this device (app/push.py), and the subscription is sent again on every
 * visit, so a renewed one or a new sign-in stays current. A device that is subscribed
 * leaves the ringing to the push, open or closed, so it never rings twice. A server
 * that does not push (it is off by default) leaves the poll to do it all.
 *
 * **Who, never what.** The notification names the weft and how many are waiting. The
 * text stays inside Weft, because a lock screen is read by whoever picks the phone up.
 *
 * Shown through the service worker (sw.js), because Android's Chrome refuses
 * `new Notification()` from a page; the worker also brings the window back on a tap.
 */

import { el } from "./dom.js";
import * as api from "./providers.js";

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

/** Whether this device's push subscription is with the server, so the push rings. */
let pushed = false;

/** The last subscribing, for the switch to wait on before it says which it is. */
let subscribing = Promise.resolve();

/** Whether the server will ring this device while Weft is closed. */
export function isPushed() {
  return pushed;
}

function sameKey(subscription, publicKey) {
  const held = subscription.options?.applicationServerKey;
  if (!held) return false;
  const text = btoa(String.fromCharCode(...new Uint8Array(held)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return text === publicKey;
}

function keyBytes(publicKey) {
  const text = atob(publicKey.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(text, (c) => c.charCodeAt(0));
}

/**
 * Subscribe this device, when the server pushes and the browser can. A subscription
 * made with an old server key is dead (the key was changed), so it is replaced.
 */
async function subscribePush(registration) {
  pushed = false;
  if (!registration || !("PushManager" in globalThis)) return;
  const { available, public_key: publicKey } = await api.pushInfo();
  if (!available) return;
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !sameKey(subscription, publicKey)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({
    userVisibleOnly: true, applicationServerKey: keyBytes(publicKey),
  });
  await api.pushSubscribe(subscription.toJSON());
  pushed = true;
}

function subscribeQuietly(registration) {
  // Quiet: without push the poll still rings while a tab is open.
  subscribing = subscribePush(registration).catch(() => {
    pushed = false;
  });
  return subscribing;
}

/** On load, when the switch is on: bring the worker up so the first ring is not late,
 *  and send the subscription again. */
export async function start() {
  if (isOn()) await subscribeQuietly(await register());
}

/** From inside a tap: ask the browser, and remember the answer on this device. */
export async function turnOn() {
  if (!supported()) return state();
  const answer = await Notification.requestPermission();
  setFlag(answer === "granted");
  if (answer === "granted") await subscribeQuietly(await register());
  return state();
}

export async function turnOff() {
  setFlag(false);
  pushed = false;
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = await registration?.pushManager?.getSubscription();
    if (subscription) {
      await api.pushUnsubscribe(subscription.endpoint).catch(() => {});
      await subscription.unsubscribe();
    }
  } catch {
    /* the switch is off here whatever the server heard; a dead endpoint is dropped */
  }
  return state();
}

/**
 * Ring for one weft. `tag` folds repeats into one, so several open tabs, or several
 * polls before anyone looks, still make a single notification per weft.
 */
export async function ring(weftId, name, count) {
  if (!isOn() || pushed) return;
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
  pushed: "On for this device, for every weft, even with Weft closed. It says which " +
    "weft and how many, never what they say.",
  on: "On for this device, for every weft. Only while Weft is open in a tab, even a " +
    "hidden one: this server does not ring a closed one. It says which weft and how " +
    "many, never what they say.",
  off: "Have this device tell you when a weft's agent leaves you something, even " +
    "while you are chatting with another. One switch for every weft.",
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
    hint.textContent = HINTS[now === "on" && pushed ? "pushed" : now];
  }

  box.addEventListener("change", async () => {
    box.disabled = true;
    paint(box.checked ? await turnOn() : await turnOff());
  });
  paint();
  // Opened while the load's subscribing is still under way: say which it came to.
  subscribing.then(() => paint());

  return el("div", { class: "notify" }, [
    el("label", { class: "toggle-row" }, [
      box,
      el("span", { text: "Notify me on this device" }),
      el("span", { class: "switch", "aria-hidden": "true" }),
    ]),
    hint,
  ]);
}
