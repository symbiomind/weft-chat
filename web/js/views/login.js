/* Weft — the login view.
 *
 * Deliberately the smallest possible thing that is still honest about failure. Every
 * message it shows comes from the server, because the server is careful about what it
 * says: unknown username, wrong password and deactivated account all return the same
 * 401 with the same body and take the same time. That is what stops this form
 * enumerating accounts, and the client must not undo it by guessing which it was.
 *
 * Everything is built with createElement and textContent — no innerHTML anywhere near
 * a value that came off the wire.
 */

import { ApiError, login } from "../session.js";
import { createBreathers } from "../canvas/breathers.js";
import * as comfort from "../comfort.js";
import { el } from "../dom.js";

export function renderLogin(root) {
  root.replaceChildren();

  // The quiet tenants: a full-viewport canvas behind the card, one grey donut
  // breathing at a time. main.js's teardown contract means login.js owns it —
  // the view hands it back to whoever mounted it.
  const breathCanvas = document.createElement("canvas");
  breathCanvas.className = "breathers";
  breathCanvas.setAttribute("aria-hidden", "true");
  const breathers = createBreathers(breathCanvas);
  // The device decides here: nobody is signed in to have chosen anything yet.
  breathers.setMotion(comfort.current().motion);
  const unwatchComfort = comfort.subscribe((state) => breathers.setMotion(state.motion));
  breathers.start();

  const error = el("p", { class: "form-error", role: "alert", hidden: "" });
  const username = el("input", {
    id: "login-username",
    name: "username",
    type: "text",
    autocomplete: "username",
    required: "",
    autocapitalize: "none",
    spellcheck: "false",
  });
  const password = el("input", {
    id: "login-password",
    name: "password",
    type: "password",
    autocomplete: "current-password",
    required: "",
  });
  const submit = el("button", { type: "submit", text: "sign in" });

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function clearError() {
    error.textContent = "";
    error.hidden = true;
  }

  const form = el(
    "form",
    {
      class: "card",
      novalidate: "",
      onsubmit: async (event) => {
        event.preventDefault();
        clearError();

        const user = username.value.trim();
        if (!user || !password.value) {
          showError("Enter a username and password.");
          return;
        }

        submit.disabled = true;
        submit.textContent = "signing in…";

        try {
          await login(user, password.value);
          // No navigation here. main.js is subscribed to the session and swaps the
          // view when the state changes — one place decides what is on screen.
        } catch (err) {
          if (err instanceof ApiError && err.status === 429) {
            const mins = err.retryAfter ? Math.ceil(err.retryAfter / 60) : null;
            showError(
              mins
                ? `Too many attempts. Try again in about ${mins} minute${mins === 1 ? "" : "s"}.`
                : "Too many attempts. Try again later.",
            );
          } else if (err instanceof ApiError && err.status === 503) {
            // Not a login failure — the server is missing a secret. Saying "wrong
            // password" here would send someone hunting for the wrong problem.
            showError("The server is not fully configured. Check its logs.");
          } else if (err instanceof ApiError && err.detail) {
            showError(err.detail);
          } else {
            showError("Could not reach the server.");
          }
          password.value = "";
          password.focus();
        } finally {
          submit.disabled = false;
          submit.textContent = "sign in";
        }
      },
    },
    [
      el("div", { class: "wordmark-lg" }, [
        document.createTextNode("w"),
        el("em", { text: "e" }),
        document.createTextNode("ft"),
      ]),
      el("p", { class: "slogan", text: "the window, not the platform" }),
      el("label", { for: "login-username", text: "username" }),
      username,
      el("label", { for: "login-password", text: "password" }),
      password,
      error,
      submit,
    ],
  );

  root.append(el("div", { class: "centre" }, [form]));
  root.prepend(breathCanvas);
  username.focus();

  // Teardown: hand back the resource owner, per main.js's contract — the
  // breathing stops the moment the view is swapped for the chat.
  return () => {
    unwatchComfort();
    breathers.stop();
  };
}
