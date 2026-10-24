/* Weft — the send shelf.
 *
 * A row of extra ways to send, kept above the send button: attach, and push to talk.
 * Hold send (or swipe up from it) and the shelf opens. It then STAYS open — across
 * sends, across reloads — until the user closes it the same way: hold send again, or
 * swipe down on send or anywhere on the shelf. Tapping outside does nothing, and
 * neither does sending. That is the point of it: open once, and push to talk is one
 * tap away for the rest of the conversation instead of a hold every time.
 *
 * While it is closed, a small `+` on the send button bobs now and then, because a
 * gesture nobody is told about is a gesture nobody finds.
 *
 * TAP SEND STILL SENDS, open or closed. The only taps that do not are the release at
 * the end of a hold or a swipe — the gesture already did something with that touch,
 * and turning it into a send as well would post whatever is in the box by accident.
 *
 * The shelf sits beside the send button rather than inside it. A button cannot
 * contain other buttons, and while a reply streams the send button greys out; the
 * shelf must keep working then, because push to talk during a reply is the whole
 * reason it exists.
 *
 * The petals are placeholders for now: the caller decides what each one does.
 */

import { el } from "./dom.js";
import { icon } from "./icons.js";

/** Remembered per device: an open shelf comes back open after a reload. */
const OPEN_KEY = "weft.shelfOpen";

/** Long enough that a quick tap is a send, short enough that a hold feels immediate. */
const HOLD_MS = 260;

/** Vertical travel that counts as a swipe rather than a wobbly tap. */
const SWIPE_PX = 28;

/** How long after a gesture's release a click is treated as that gesture's echo. */
const ECHO_MS = 350;

/* Left to right. Push to talk is last, next to send, where a right thumb already
   rests: once the shelf is open it is the petal reached for most. One paperclip covers
   pictures and files alike, as it does everywhere else. */
export const PETALS = [
  { act: "attach", icon: "paperclip", label: "attach" },
  { act: "ptt", icon: "mic", label: "push to talk" },
];

/**
 * @param sendButton the form's submit button. It is moved into the returned wrapper.
 * @param onPetal    called with a petal's `act` when it is tapped.
 * @param onToggle   called with the new state after the shelf opens or closes.
 * @returns {{element: HTMLElement, petal(act: string): HTMLElement,
 *            show(act: string, visible: boolean): void,
 *            flash(act: string, words: string, ms?: number): void, teardown(): void}}
 */
export function createSendShelf(sendButton, { onPetal, onToggle } = {}) {
  sendButton.id = "send";
  sendButton.append(el("span", { class: "hint", "aria-hidden": "true", text: "+" }));

  const petals = PETALS.map(({ act, icon: name, label }) =>
    el("button", {
      type: "button",
      class: "petal",
      "data-act": act,
      title: label,
      "aria-label": label,
    }, [
      icon(name),
      el("em", { text: label }),
    ]));
  const shelf = el("div", {
    id: "send-shelf",
    role: "toolbar",
    "aria-label": "more ways to send",
  }, petals);
  const element = el("div", { id: "send-wrap" }, [sendButton, shelf]);

  let open = false;
  let toggledAt = 0;

  function setOpen(next, { quiet = false } = {}) {
    open = next;
    toggledAt = performance.now();
    element.classList.toggle("open", open);
    // Invisible petals must not be reachable by Tab either.
    shelf.inert = !open;
    sendButton.setAttribute("aria-expanded", String(open));
    // On the root so the log can make room for the shelf above the bar.
    document.documentElement.toggleAttribute("data-shelf-open", open);
    try {
      if (open) localStorage.setItem(OPEN_KEY, "1");
      else localStorage.removeItem(OPEN_KEY);
    } catch {
      /* no persistence; the shelf simply forgets across reloads */
    }
    // Not for the restore below: that runs while the caller is still building the
    // view, and anything onToggle reaches for may not exist yet.
    if (!quiet) onToggle?.(open);
  }

  let remembered = false;
  try {
    remembered = localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    /* as above */
  }
  setOpen(remembered, { quiet: true });

  // ---- the gesture ----
  //
  // `press` is the pointer currently down on send or on the shelf. `consumed` means a
  // hold or swipe already acted on it, so its release must not also click.
  let press = null;
  let holdTimer = null;
  let swallowUntil = 0;

  function begin(event, onSend) {
    if (event.button !== 0) return;
    press = { id: event.pointerId, y: event.clientY, onSend, consumed: false };
    // Capture, so a finger that slides off the button still reports its swipe here.
    // On the petal itself rather than the shelf: the click that follows a captured
    // press is aimed at the capturing element, and a click aimed at the shelf does not
    // say which petal was pressed.
    const holder = event.target.closest?.(".petal") ?? event.currentTarget;
    holder.setPointerCapture?.(event.pointerId);
    if (!onSend) return;
    sendButton.classList.add("holding");
    holdTimer = setTimeout(() => {
      if (!press) return;
      press.consumed = true;
      sendButton.classList.remove("holding");
      setOpen(!open);
    }, HOLD_MS);
  }

  function move(event) {
    if (!press || press.consumed || event.pointerId !== press.id) return;
    const dy = event.clientY - press.y;
    // Up only means something from send; down closes from send or the shelf.
    const next = dy > SWIPE_PX ? false : dy < -SWIPE_PX && press.onSend ? true : null;
    if (next === null) return;
    press.consumed = true;
    clearTimeout(holdTimer);
    sendButton.classList.remove("holding");
    setOpen(next);
  }

  function end(event) {
    if (!press || event.pointerId !== press.id) return;
    // A cancelled pointer produces no click, so there is no echo to swallow — and a
    // stale swallow would eat the user's next real tap.
    if (press.consumed && event.type === "pointerup") swallowUntil = performance.now() + ECHO_MS;
    press = null;
    clearTimeout(holdTimer);
    sendButton.classList.remove("holding");
  }

  function isEcho() {
    return performance.now() < swallowUntil;
  }

  const onSendDown = (event) => begin(event, true);
  const onShelfDown = (event) => begin(event, false);

  function onSendClick(event) {
    // preventDefault on a submit button's click is what stops the form submitting.
    if (isEcho()) event.preventDefault();
  }

  function onShelfClick(event) {
    const petal = event.target.closest(".petal");
    if (!petal || isEcho()) return;
    onPetal?.(petal.dataset.act);
  }

  function onContextMenu(event) {
    // A long press on a phone raises the context menu too, a moment after the hold has
    // already toggled the shelf; toggling again would undo it. A right-click never
    // starts a press (begin ignores it), so it is the only kind that gets here clean.
    event.preventDefault();
    if (!press && performance.now() - toggledAt > 1000) setOpen(!open);
  }

  for (const [node, down, click] of [
    [sendButton, onSendDown, onSendClick],
    [shelf, onShelfDown, onShelfClick],
  ]) {
    node.addEventListener("pointerdown", down);
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", end);
    node.addEventListener("pointercancel", end);
    node.addEventListener("click", click);
  }
  sendButton.addEventListener("contextmenu", onContextMenu);

  function petalFor(act) {
    return shelf.querySelector(`.petal[data-act="${act}"]`);
  }

  /** Show or hide one petal: push to talk is only there once it can hear anyone. */
  function show(act, visible) {
    const petal = petalFor(act);
    if (petal) petal.hidden = !visible;
  }

  /**
   * Swap a petal's label and show it even where there is no hover. For `ms`, or until
   * the next flash when `ms` is 0 (push to talk's "listening…" lasts the whole hold).
   */
  const flashTimers = new Map();
  function flash(act, words, ms = 1400) {
    const petal = petalFor(act);
    if (!petal) return;
    const label = petal.querySelector("em");
    const original = PETALS.find((p) => p.act === act).label;
    label.textContent = words;
    petal.classList.add("flash");
    clearTimeout(flashTimers.get(act));
    if (!ms) return;
    flashTimers.set(act, setTimeout(() => {
      label.textContent = original;
      petal.classList.remove("flash");
    }, ms));
  }

  function teardown() {
    clearTimeout(holdTimer);
    for (const timer of flashTimers.values()) clearTimeout(timer);
    // The open state stays in storage; only the live attribute goes with the view.
    document.documentElement.removeAttribute("data-shelf-open");
  }

  return { element, petal: petalFor, show, flash, teardown };
}
