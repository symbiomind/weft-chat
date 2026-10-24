/* Weft — boot and routing.
 *
 * The entry point, and the ONE place that decides what is on screen.
 *
 * There is no URL router, deliberately. The thing being routed on is the session
 * state, not a location: you are anonymous, or you are holding a server-issued
 * password you must replace, or you are in. A URL cannot override any of those — the
 * server decides — so putting them in the address bar would invite a user to type a
 * path and get a view the server will refuse to serve data to.
 *
 * Views never navigate. They act on the session, the session emits, and this reacts.
 * That is what keeps "what is on screen" answerable from one file.
 */

import * as audiodebug from "./audiodebug.js";
import * as comfort from "./comfort.js";
import { getState, resume, subscribe } from "./session.js";
import * as theme from "./theme.js";
import { renderChangePassword } from "./views/change-password.js";
import { renderChat } from "./views/chat.js";
import { renderLogin } from "./views/login.js";

const root = document.getElementById("app");

/** What is currently mounted, so we only re-render on an actual transition. */
let mounted = null;
/** Views that own a resource (the canvas loop) hand back a teardown. */
let teardown = null;

function mount(status, state) {
  if (mounted === status) return;

  if (teardown) {
    teardown();
    teardown = null;
  }

  document.body.dataset.view = status;

  switch (status) {
    case "must-change-password":
      renderChangePassword(root, state.user);
      break;
    case "active":
      teardown = renderChat(root);
      break;
    case "anonymous":
    default: {
      // renderLogin starts the breather canvas; the teardown stops its loop.
      const stopBreathers = renderLogin(root);
      teardown = typeof stopBreathers === "function" ? stopBreathers : null;
      break;
    }
  }

  mounted = status;
}

function showBootError(message) {
  root.replaceChildren();
  const card = document.createElement("div");
  card.className = "card";
  const heading = document.createElement("h1");
  heading.textContent = "Weft could not start";
  const detail = document.createElement("p");
  detail.textContent = message;
  card.append(heading, detail);
  const centre = document.createElement("div");
  centre.className = "centre";
  centre.append(card);
  root.append(centre);
}

async function boot() {
  theme.init();
  // Before anything draws: the device's motion and transparency limits apply from the
  // first frame, the sign-in page included.
  comfort.init();
  // Before anything can speak, so the log has the whole story.
  audiodebug.start();

  subscribe((state) => mount(state.status, state));

  // Re-establish from the httpOnly refresh cookie. A reload loses the in-memory access
  // token but not the cookie, so this is what makes F5 not log you out. Failure is the
  // ordinary anonymous case, not an error.
  try {
    await resume();
  } catch {
    /* resume() already fell back to anonymous */
  }

  // A backgrounded tab has its timers throttled, so the scheduled refresh may not have
  // fired. Coming back to a tab that was open overnight should not greet you with a
  // failed request — and refresh() coalesces, so this is free when it is unnecessary.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (getState().status === "anonymous") return;
    import("./session.js").then((s) => s.refresh().catch(() => {}));
  });

  mount(getState().status, getState());
}

boot().catch((err) => {
  console.error("boot failed", err);
  showBootError("Something went wrong starting the app. Check the browser console.");
});
