/* Weft — the Wefts screen: every weft this user has, and a way to add one.
 *
 * ADDING TAKES A NAME AND NOTHING ELSE. Type "Jarvis", and Jarvis is on
 * the Pick menu at once, and its page opens so it can be given an engine, colours, a
 * voice. A name already taken (in any case) is refused by the server, and the field says
 * so rather than making a second one.
 *
 * A ROW SAYS WHAT ANSWERS IT, so a weft left with no engine (just added, or its provider
 * was deleted) is visible as that without opening it. Tapping a row opens its page.
 */

import { dress, pairOf } from "../colours.js";
import { el, rowButton } from "../dom.js";
import { capabilitiesOf, icon } from "../icons.js";
import { PURPOSE, panelHead } from "../panels.js";
import * as api from "../providers.js";
import { renderWeft } from "./weft.js";

/**
 * `onChanged()` fires whenever a weft is added, changed or deleted, so the chat window
 * can refresh its Pick menu. `onClearHistory(weft)` and `onCompose(weft, text)` are passed
 * through to each weft's page.
 */
export function renderWefts(panels, { onChanged, onClearHistory, onCompose }) {
  const error = el("p", { class: "form-error", role: "alert", hidden: "" });
  const list = el("div", { class: "rows" });

  const nameField = el("input", {
    type: "text", id: "new-weft-name", autocomplete: "off", maxlength: "64",
    placeholder: "a name — Jarvis, say",
  });
  const addButton = el("button", { type: "submit", text: "add" });
  const addForm = el("form", { class: "add-weft" }, [nameField, addButton]);

  /** Open one weft's page. */
  function open(weft) {
    panels.push((p) => renderWeft(p, {
      weft,
      onChanged: () => onChanged?.(),
      onDeleted: () => onChanged?.(),
      onClearHistory,
      onCompose,
    }), { page: "weft" });
  }

  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = nameField.value.trim();
    if (!name) {
      nameField.focus();
      return;
    }
    error.hidden = true;
    addButton.disabled = true;
    try {
      const weft = await api.createWeft(name);
      nameField.value = "";
      onChanged?.();
      refresh();
      open(weft);
    } catch (err) {
      error.textContent = err.detail || err.message || "Could not add that weft.";
      error.hidden = false;
      nameField.focus();
    } finally {
      addButton.disabled = false;
    }
  });

  function engineLine(weft) {
    if (weft.provider_id && weft.model_id) return `${weft.model_id} on ${weft.provider_name}`;
    return "no engine yet";
  }

  function row(weft) {
    const node = rowButton({ class: "row weft-row", onclick: () => open(weft) }, [
      el("span", { class: "led" }),
      el("div", { class: "row-main" }, [
        // What it can do, as the Pick shows it. Push to talk is left off: it is the
        // user's, on every weft or none, so it says nothing about this one.
        el("div", { class: "row-name" }, [
          document.createTextNode(weft.name),
          el("span", { class: "pick-caps" },
            capabilitiesOf(weft).map(({ icon: name, label }) => icon(name, { label }))),
        ]),
        el("div", { class: "row-sub", text: engineLine(weft) }),
      ]),
      el("span", { class: "row-go", "aria-hidden": "true", text: "›" }),
    ]);
    node.classList.toggle("unbound", !(weft.provider_id && weft.model_id));
    dress(node, pairOf(weft));
    return node;
  }

  async function refresh() {
    try {
      const wefts = await api.listWefts();
      list.replaceChildren(
        ...(wefts.length
          ? wefts.map(row)
          : [el("p", {
            class: "empty",
            text: "No wefts yet. Give one a name above — then choose what answers it, " +
              "how it looks, and how it sounds.",
          })]),
      );
    } catch (err) {
      error.textContent = err.detail || err.message || "Could not load your wefts.";
      error.hidden = false;
    }
  }

  const node = el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "Wefts", sub: PURPOSE.wefts }),
    el("div", { class: "panel-body" }, [addForm, error, list]),
  ]);

  refresh();

  // Re-read on the way back from a weft's page: a rename or a new engine changes a row.
  return { node, onReveal: refresh };
}

export { renderWeft };
