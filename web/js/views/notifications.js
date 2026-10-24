/* Weft — one weft's notifications.
 *
 * What a weft's agent left through the door while the user was away, newest first.
 * Each is a letter, not a turn: it is read here, never in the chat, and dismissing it
 * is the only thing to do with it for now. Dismissed is gone, on every device.
 *
 * A body is plain text, read the way a reply is: a picture reference in it shows as the
 * picture (references.js), and nothing else in it is interpreted.
 *
 * The page wears the weft's own pair, so whose letters these are is never in doubt.
 */

import { el } from "../dom.js";
import { dress, pairOf } from "../colours.js";
import { panelHead } from "../panels.js";
import * as providersApi from "../providers.js";
import { picturesIn } from "../references.js";

/** "Sat 18 Oct, 1:12 pm": a report is a moment, so the day and the time. */
function when(iso) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

/**
 * @param {object} panels
 * @param {{weft: object, onChanged: () => void,
 *   openPicture: (src: string, name: string) => void}} options
 *   `onChanged` after anything is dismissed, so the bells can catch up.
 */
export function renderNotifications(panels, { weft, onChanged, openPicture }) {
  const list = el("div", { class: "rows notes" }, [
    el("p", { class: "empty", text: "Looking…" }),
  ]);
  const dismissAll = el("button", {
    type: "button", class: "small", text: "Dismiss all", hidden: "",
    onclick: async () => {
      dismissAll.disabled = true;
      try {
        await providersApi.dismissAllNotifications(weft.id);
      } catch {
        /* the list below says what is still there */
      }
      dismissAll.disabled = false;
      onChanged();
      load();
    },
  });

  /** Blob URLs of the pictures on show, revoked when the page closes. */
  const urls = [];

  function pictures(shown) {
    const row = el("div", { class: "bubble-pics" });
    for (const { hash, name } of shown) {
      const img = el("img", { alt: name || "picture", title: name || "picture" });
      const frame = el("button", {
        type: "button", class: "bubble-pic", "aria-label": `open ${name || "picture"}`,
        onclick: () => img.src && openPicture(img.src, name),
      }, [img]);
      providersApi.attachmentBlob(hash)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          urls.push(url);
          img.src = url;
        })
        .catch(() => frame.classList.add("gone"));
      row.append(frame);
    }
    return row;
  }

  function noteRow(note) {
    const shown = picturesIn(note.body);
    const dismiss = el("button", {
      type: "button", class: "small", text: "Dismiss",
      onclick: async () => {
        dismiss.disabled = true;
        try {
          await providersApi.dismissNotification(note.id);
        } catch {
          /* already gone from another device: dismissed either way */
        }
        row.remove();
        onChanged();
        if (!list.querySelector(".note")) paint([]);
      },
    });
    const main = el("div", { class: "row-main" }, [
      el("div", { class: "row-name", text: note.title }),
      el("div", { class: "row-sub", text: when(note.created_at) }),
    ]);
    if (shown.text) main.append(el("p", { class: "note-body", text: shown.text }));
    if (shown.pictures.length) main.append(pictures(shown.pictures));
    const row = el("div", { class: "row note" }, [main, dismiss]);
    return row;
  }

  function paint(notes) {
    dismissAll.hidden = notes.length < 2;
    list.replaceChildren(...(notes.length
      ? notes.map(noteRow)
      : [el("p", { class: "empty", text: `Nothing waiting from ${weft.name}.` })]));
  }

  async function load() {
    try {
      paint(await providersApi.listNotifications(weft.id));
    } catch {
      list.replaceChildren(el("p", { class: "empty", text: "Could not load the notifications." }));
    }
  }

  load();

  const node = el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: weft.name, sub: "notifications · what they left while you were away" }),
    el("div", { class: "panel-body" }, [
      el("div", { class: "notes-head" }, [dismissAll]),
      list,
    ]),
  ]);
  dress(node, pairOf(weft));

  return {
    node,
    teardown: () => {
      for (const url of urls) URL.revokeObjectURL(url);
      onChanged();
    },
  };
}
