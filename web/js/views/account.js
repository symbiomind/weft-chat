/* Weft — the Account screen.
 *
 * Who you are, and the ways out: change the password, the devices signed in, sign out
 * everywhere. Sign out sits in the drawer too, on purpose; leaving should never be
 * more than one tap from wherever you are.
 *
 * The devices list is one box per signed-in device, this one first and
 * marked. This device's Sign out is the plain logout, no question asked, as it always
 * was. Another device's asks on its own row, because it is somebody's open window.
 *
 * Sign out everywhere confirms on its own row, like a provider's delete does: it ends
 * this session as well, and every device the account is signed in on, so it should not
 * fire on a stray tap.
 */

import { el } from "../dom.js";
import { PURPOSE, panelHead } from "../panels.js";
import {
  ApiError, endSession, getState, listSessions, logout, logoutEverywhere,
} from "../session.js";
import { renderPasswordPanel } from "./change-password.js";

/** The account card: an initial, the name, its role when that role is more than user. */
export function accountCard(user, { sub, action } = {}) {
  const name = user?.display_name || user?.username || "—";
  const nameLine = el("div", { class: "acct-name", text: name });
  if (user?.role && user.role !== "user") {
    nameLine.append(el("span", { class: "acct-badge", text: user.role }));
  }
  const id = el("div", { class: "acct-id" }, [nameLine]);
  if (sub) id.append(sub);
  const children = [
    el("div", { class: "acct-avatar", "aria-hidden": "true", text: name.charAt(0).toUpperCase() }),
    id,
  ];
  if (action) children.push(action);
  return el("div", { class: "acct" }, children);
}

/** "4 min ago", from the server's relative seconds: never this device's clock. */
function ago(seconds) {
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** One box: what it is, where it is, when it was last here, and its way out. */
function deviceRow(device, { onEnded, error }) {
  const what = [device.browser, device.os].filter(Boolean).join(" · ") || "Unknown browser";
  const name = el("div", { class: "row-name", text: what });
  if (device.current) name.append(el("span", { class: "acct-badge", text: "this device" }));

  const when = device.current || device.active
    ? "active now"
    : `last seen ${ago(device.seen_seconds_ago)}`;
  const since = new Date(device.created_at).toLocaleDateString(undefined, {
    day: "numeric", month: "short",
  });
  const title = el("div", { class: "row-main" }, [
    name,
    el("div", { class: "row-sub", text: device.ip || "address unknown" }),
    el("div", { class: "row-sub", text: `${when} · signed in ${since}` }),
  ]);
  // The labels are a guess from a string the browser chose to send; the string itself
  // is there for anyone who wants to check the guess.
  if (device.user_agent) title.title = device.user_agent;

  const signOut = el("button", {
    type: "button",
    class: "small danger",
    text: "Sign out",
    onclick: () => (device.current ? logout() : asking()),
  });
  const actions = el("div", { class: "row-actions" }, [signOut]);
  const node = el("div", { class: device.current ? "row device current" : "row device" }, [
    title,
    actions,
  ]);

  function asking() {
    const confirmRow = el("div", { class: "row-confirm" }, [
      el("span", { text: "Sign it out?" }),
      el("button", {
        type: "button",
        class: "small danger",
        text: "sign out",
        onclick: async (event) => {
          event.currentTarget.disabled = true;
          error.hidden = true;
          try {
            await endSession(device.id);
            onEnded();
          } catch (err) {
            // A 404 is a device that ended on its own meanwhile: what was asked for.
            if (err instanceof ApiError && err.status === 404) {
              onEnded();
              return;
            }
            error.textContent = err instanceof ApiError && err.detail
              ? err.detail
              : "Could not reach the service. That device is still signed in.";
            error.hidden = false;
            confirmRow.replaceWith(actions);
          }
        },
      }),
      el("button", {
        type: "button",
        class: "small",
        text: "cancel",
        onclick: () => confirmRow.replaceWith(actions),
      }),
    ]);
    actions.replaceWith(confirmRow);
  }

  return node;
}

export function renderAccount(panels) {
  const user = getState().user;
  const body = el("div", { class: "panel-body" });

  // A display name hides the username, which is what signs in; say it underneath.
  const sub = user?.display_name
    ? el("div", { class: "acct-sub", text: `signs in as ${user.username}` })
    : null;

  const error = el("p", { class: "form-error", role: "alert", hidden: "" });

  const devices = el("div", { class: "rows" }, [
    el("p", { class: "empty", text: "Finding your devices…" }),
  ]);
  async function showDevices() {
    try {
      const list = await listSessions();
      devices.replaceChildren(...list.map((device) => deviceRow(device, {
        onEnded: showDevices,
        error,
      })));
    } catch {
      devices.replaceChildren(el("p", {
        class: "empty",
        text: "Could not load your devices. Sign out everywhere still works.",
      }));
    }
  }
  showDevices();

  const everywhere = el("button", {
    type: "button",
    class: "chunk",
    text: "Sign out everywhere",
    onclick: () => asking(true),
  });
  const confirmEverywhere = el("button", {
    type: "button",
    class: "small danger",
    text: "sign out everywhere",
    onclick: async () => {
      confirmEverywhere.disabled = true;
      error.hidden = true;
      try {
        // On success the session is cleared and main.js swaps to the login view.
        await logoutEverywhere();
      } catch (err) {
        error.textContent = err instanceof ApiError && err.detail
          ? err.detail
          : "Could not reach the service. Nothing was signed out.";
        error.hidden = false;
        confirmEverywhere.disabled = false;
      }
    },
  });
  const confirmRow = el("div", { class: "row-confirm", hidden: "" }, [
    el("span", { text: "Sign out of every device, this one too?" }),
    confirmEverywhere,
    el("button", {
      type: "button",
      class: "small",
      text: "cancel",
      onclick: () => asking(false),
    }),
  ]);

  /** The button, or the question it asks: never both. */
  function asking(yes) {
    everywhere.hidden = yes;
    confirmRow.hidden = !yes;
  }

  body.append(
    accountCard(user, { sub }),
    el("h3", { text: "SECURITY" }),
    el("button", {
      type: "button",
      class: "chunk",
      text: "Change password",
      onclick: () => panels.push((p) => renderPasswordPanel(p)),
    }),
    el("h3", { text: "SIGNED IN ON" }),
    devices,
    everywhere,
    confirmRow,
    error,
    el("p", {
      class: "hint",
      text: "Sign out everywhere ends every session this account has, on every device, "
        + "including this one. For a phone you lost, or a computer that was not yours.",
    }),
  );

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "Account", sub: PURPOSE.account }),
    body,
  ]);
}
