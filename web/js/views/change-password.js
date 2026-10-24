/* Weft — the forced password change.
 *
 * Reached when the server sets must_change_password, which it does for every account
 * created by bootstrap or reset by an administrator. Until this is done the server
 * answers 409 on everything except this and logout, so there is nowhere else to go —
 * the view has no cancel button because cancelling is not a state that exists.
 *
 * The current password is required even though the caller is already authenticated.
 * That is what stops a stolen access token locking the real owner out of their account.
 *
 * The same three fields also open as a panel from the drawer (renderPasswordPanel), for
 * changing a password you chose yourself. That one has a back button, because there it
 * is a choice rather than a gate.
 */

import { ApiError, changePassword, logout } from "../session.js";
import { el } from "../dom.js";
import { panelHead } from "../panels.js";

/** The three fields and the matching check, shared by the gate and the panel. */
function passwordFields() {
  const error = el("p", { class: "form-error", role: "alert", hidden: "" });
  const currentPassword = el("input", {
    id: "cp-current",
    type: "password",
    autocomplete: "current-password",
    required: "",
  });
  const newPassword = el("input", {
    id: "cp-new",
    type: "password",
    autocomplete: "new-password",
    required: "",
    minlength: "12",
  });
  const confirmPassword = el("input", {
    id: "cp-confirm",
    type: "password",
    autocomplete: "new-password",
    required: "",
  });
  const submit = el("button", { type: "submit", text: "set password" });

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  /** Resolves true once the server has accepted the new password. */
  async function attempt() {
    error.hidden = true;

    // Checked here because the server has no way to know the user typed it twice.
    // Every other rule below is enforced server-side and merely echoed.
    if (newPassword.value !== confirmPassword.value) {
      showError("The two new passwords do not match.");
      confirmPassword.focus();
      return false;
    }

    submit.disabled = true;
    submit.textContent = "setting…";

    try {
      await changePassword(currentPassword.value, newPassword.value);
      return true;
    } catch (err) {
      showError(
        err instanceof ApiError && err.detail
          ? err.detail
          : "Could not change the password.",
      );
      return false;
    } finally {
      submit.disabled = false;
      submit.textContent = "set password";
    }
  }

  const rows = [
    el("label", { for: "cp-current", text: "current password" }),
    currentPassword,
    el("label", { for: "cp-new", text: "new password" }),
    newPassword,
    el("p", { class: "hint", text: "At least 12 characters. Length beats punctuation." }),
    el("label", { for: "cp-confirm", text: "confirm new password" }),
    confirmPassword,
    error,
    submit,
  ];

  return { rows, attempt, currentPassword };
}

export function renderChangePassword(root, user) {
  root.replaceChildren();

  const { rows, attempt, currentPassword } = passwordFields();

  const form = el(
    "form",
    {
      class: "card",
      novalidate: "",
      onsubmit: (event) => {
        event.preventDefault();
        attempt();
      },
    },
    [
      el("div", { class: "wordmark-lg" }, [
        document.createTextNode("w"),
        el("em", { text: "e" }),
        document.createTextNode("ft"),
      ]),
      el("h1", { text: "Choose a password" }),
      el("p", {
        class: "slogan",
        text: `Signed in as ${user?.username ?? "this account"}. This password was issued by the server, so it has to be replaced before anything else works.`,
      }),
      ...rows,
      el("button", {
        type: "button",
        class: "linkish",
        text: "sign out instead",
        onclick: () => logout(),
      }),
    ],
  );

  root.append(el("div", { class: "centre" }, [form]));
  currentPassword.focus();
}

/**
 * Change password, from the drawer. On success the server has already ended every other
 * session and handed this device a fresh pair, so the panel only has to say so.
 */
export function renderPasswordPanel(panels) {
  const { rows, attempt } = passwordFields();
  const body = el("div", { class: "panel-body" });

  const form = el("form", {
    class: "panel-form",
    novalidate: "",
    onsubmit: async (event) => {
      event.preventDefault();
      if (!(await attempt())) return;
      body.replaceChildren(
        el("div", { class: "panel-form" }, [
          el("p", { text: "Password changed. Any other device signed in to this account has been signed out." }),
          el("button", { type: "button", text: "done", onclick: () => panels.pop() }),
        ]),
      );
    },
  }, rows);
  body.append(form);

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, {
      title: "Change password",
      sub: "signs out every other device",
    }),
    body,
  ]);
}
