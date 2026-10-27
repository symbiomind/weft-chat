/* Weft — full-screen panels.
 *
 * WHY THIS IS NOT IN main.js. main.js says it is the one place that decides what is on
 * screen, and that is still true of the thing it decides: *session status*. You are
 * anonymous, or holding a password you must replace, or you are in. The server owns
 * that, which is why it is not in the URL.
 *
 * A panel is a different axis. Providers and Models are surfaces *within* being logged
 * in — they cannot outlive the view that opened them, they do not survive a reload,
 * and no session transition can land you in one. Routing them through main.js would
 * mean teaching it about states the server has no opinion on, which is the first step
 * towards the URL router the original design deliberately did not want.
 *
 * So: main.js routes the session, panels stack inside the active view, and the view's
 * teardown calls popAll(). A panel can never be orphaned because the only thing that
 * can leave one open is the thing that gets torn down.
 *
 * The stack keeps closed-over layers in the DOM rather than re-rendering them. Going
 * Providers -> Models -> back should return you to the same scroll position on a list
 * you were halfway down, which is the whole difference between a stack and a history.
 */

import { el } from "./dom.js";
import { icon } from "./icons.js";

/** @type {HTMLElement|null} */
let host = null;
/** @type {{node: HTMLElement, page: ?string, teardown?: () => void, onReveal?: () => void}[]} */
const stack = [];
/** Where focus was before the first panel opened, to give it back when the last closes. */
let returnFocus = null;
let titleIds = 0;
/** Told whenever the top layer changes, including to nothing. */
const watchers = new Set();

function ensureHost() {
  if (host) return host;
  host = el("div", { id: "panel-host" });
  document.body.append(host);
  document.addEventListener("keydown", onKeydown);
  return host;
}

function onKeydown(event) {
  // Escape closes the top layer only. Closing the whole stack would throw away an
  // edit form AND the list behind it on one keystroke.
  if (event.key === "Escape" && stack.length) {
    event.stopPropagation();
    pop();
  }
}

function showTop() {
  stack.forEach((layer, index) => {
    layer.node.hidden = index !== stack.length - 1;
  });
  for (const watcher of watchers) watcher();
  const top = stack[stack.length - 1];
  if (top) {
    top.onReveal?.();
    // Move focus into the panel. Without this, a keyboard user is still on the button
    // that opened it and tabs through the chat window behind the overlay.
    const focusable = top.node.querySelector("input, button, [tabindex]");
    focusable?.focus?.();
  }
}

/**
 * Push a panel. `render` receives the panel API and returns either a node or
 * `{ node, teardown, onReveal }`.
 *
 * `onReveal` fires when this layer becomes the top one again — which is how the
 * Wefts list learns that the weft page it opened renamed or rebound a weft.
 *
 * `page` names the screen for the tips (tutor.js): "providers", "weft" and so on. A
 * layer without one, such as an Add form, is somewhere no tip shows.
 */
export function push(render, { page = null } = {}) {
  const api = { push, pop, popAll };
  const result = render(api);
  const layer = { ...(result?.node ? result : { node: result }), page };

  if (!stack.length) returnFocus = document.activeElement;
  layer.node.classList.add("panel");
  // A dialog, named by its own title, so a screen reader says where it has landed and
  // leaves the chat behind it alone until it closes.
  layer.node.setAttribute("role", "dialog");
  layer.node.setAttribute("aria-modal", "true");
  const title = layer.node.querySelector(".panel-title");
  if (title) {
    title.id ||= `panel-title-${(titleIds += 1)}`;
    layer.node.setAttribute("aria-labelledby", title.id);
  }
  ensureHost().append(layer.node);
  stack.push(layer);
  document.body.dataset.panel = "open";
  showTop();
  return layer;
}

/** Close the top panel. */
export function pop() {
  const layer = stack.pop();
  if (!layer) return;

  try {
    layer.teardown?.();
  } catch (err) {
    // A view that throws on the way out must not leave its node welded to the screen.
    console.error("panel teardown failed", err);
  }
  layer.node.remove();

  if (stack.length === 0) {
    delete document.body.dataset.panel;
    host?.remove();
    document.removeEventListener("keydown", onKeydown);
    host = null;
    for (const watcher of watchers) watcher();
    // Give focus back, to what opened the first panel if it is still there to take it
    // (the drawer that opened it is shut and inert by now), or to the menu button.
    const back = returnFocus?.isConnected && !returnFocus.closest("[inert]")
      ? returnFocus : document.getElementById("burger");
    returnFocus = null;
    back?.focus?.();
    return;
  }
  showTop();
}

/** Close everything. Called by the owning view's teardown. */
export function popAll() {
  while (stack.length) pop();
}

/** The top layer's page name, null for one without a name, undefined for no panels. */
export function topPage() {
  return stack.length ? stack[stack.length - 1].page : undefined;
}

/** Call `watcher` whenever the top layer changes. Returns the way to stop. */
export function watch(watcher) {
  watchers.add(watcher);
  return () => watchers.delete(watcher);
}

export function isOpen() {
  return stack.length > 0;
}

/**
 * A panel's head: back on the left, the name and a line under it, and ✕ on the right.
 *
 * Two lines on every screen, as a weft's page has always had: the name says where you
 * are, the line says what it is for, in the same words the drawer uses to send you
 * there. Back goes up one layer; ✕ closes the whole stack and lands in the chat, as
 * the drawer's own ✕ does. `title` and `sub` take a string, or a node a page repaints.
 */
export function panelHead(panels, { title, sub }) {
  const line = (tag, cls, value) => value instanceof Node
    ? (value.classList.add(cls), value)
    : el(tag, { class: cls, text: value ?? "" });
  return el("div", { class: "panel-head" }, [
    el("button", {
      type: "button", class: "panel-back", "aria-label": "back", title: "back",
      onclick: () => panels.pop(),
    }, [icon("back")]),
    el("div", { class: "panel-titles" }, [
      line("h1", "panel-title", title),
      line("div", "panel-subtitle", sub),
    ]),
    el("button", {
      type: "button", class: "panel-close", "aria-label": "close settings",
      title: "back to the chat",
      onclick: () => panels.popAll(),
    }, [icon("close")]),
  ]);
}

/** What each screen is for: the drawer's line under its name, and the screen's own. */
export const PURPOSE = {
  notifications: "what your wefts left while you were away",
  account: "password · devices · sign out",
  wefts: "thread a new one, dress the ones you have",
  providers: "engines · speech · sessions · talk",
  comfort: "motion · transparency · send button",
  about: "the loom · what you've found",
};
