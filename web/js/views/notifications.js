/* Weft — notifications: one inbox for every weft.
 *
 * What the wefts' agents left through their doors while the user was away, newest
 * first, all in one place, the way mail is read. Each is a letter, not a turn: it is
 * read here, never in the chat, and dismissing it is the only thing to do with it for
 * now. Dismissed is gone, on every device.
 *
 * A body is plain text, read the way a reply is: a picture reference in it shows as the
 * picture (references.js), and nothing else in it is interpreted.
 *
 * The page itself is neutral. Each letter wears its own weft's pair, with the pea and
 * the name, so whose it is is never in doubt; the name is the return address, a tap
 * away from that weft's chat.
 */

import { el } from "../dom.js";
import { dress, pairOf } from "../colours.js";
import { PURPOSE, panelHead } from "../panels.js";
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
 * @param {{wefts: () => object[], onChanged: () => void, onOpenWeft: (weft: object) => void,
 *   openPicture: (src: string, name: string) => void}} options
 *   `wefts` is the user's wefts, asked as each letter is drawn, for its name and pair.
 *   `onChanged` after anything is dismissed, so the bell can catch up.
 */
export function renderNotifications(panels, { wefts, onChanged, onOpenWeft, openPicture }) {
  const list = el("div", { class: "rows notes" }, [
    el("p", { class: "empty", text: "Looking…" }),
  ]);
  const dismissAll = el("button", {
    type: "button", class: "small", text: "Dismiss all", hidden: "",
    onclick: async () => {
      dismissAll.disabled = true;
      try {
        await providersApi.dismissAllNotifications();
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

  /** Who it is from: the pea, the name (a door to that weft's chat), and when. */
  function from(weft, note) {
    const name = weft
      ? el("button", {
        type: "button", class: "note-weft", text: weft.name,
        title: `talk to ${weft.name}`,
        onclick: () => onOpenWeft(weft),
      })
      : el("span", { class: "note-weft", text: "a weft" });
    return el("div", { class: "note-from" }, [
      el("span", { class: "led" }),
      name,
      el("span", { class: "row-sub", text: when(note.created_at) }),
    ]);
  }

  function noteRow(note, weft) {
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
        const left = list.querySelectorAll(".note").length;
        if (!left) paint([]);
        else dismissAll.hidden = left < 2;
      },
    });
    const main = el("div", { class: "row-main" }, [
      from(weft, note),
      el("div", { class: "row-name", text: note.title }),
    ]);
    if (shown.text) main.append(el("p", { class: "note-body", text: shown.text }));
    if (shown.pictures.length) main.append(pictures(shown.pictures));
    const row = el("div", { class: "row note" }, [main, dismiss]);
    dress(row, pairOf(weft));
    return row;
  }

  /** Nothing waiting. Where nothing could arrive yet either, say where letters come from. */
  function empty() {
    if (wefts().some((w) => w.has_door)) {
      return [el("p", { class: "empty", text: "Nothing waiting." })];
    }
    return [
      el("p", { class: "empty", text: "Nothing waiting, and nothing can arrive yet." }),
      el("p", {
        class: "hint",
        text: "Notifications come from an agent, through its weft's door. Open one under "
          + "Agent door on a weft's page (the ⚙ beside it on the bar), connect an agent "
          + "to it, and what that agent reports lands here.",
      }),
    ];
  }

  function paint(notes) {
    dismissAll.hidden = notes.length < 2;
    const byId = new Map(wefts().map((w) => [w.id, w]));
    list.replaceChildren(...(notes.length
      ? notes.map((note) => noteRow(note, byId.get(note.weft_id)))
      : empty()));
  }

  async function load() {
    try {
      paint(await providersApi.listNotifications());
    } catch {
      list.replaceChildren(el("p", { class: "empty", text: "Could not load the notifications." }));
    }
  }

  load();

  const node = el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "Notifications", sub: PURPOSE.notifications }),
    el("div", { class: "panel-body" }, [
      el("div", { class: "notes-head" }, [dismissAll]),
      list,
    ]),
  ]);

  return {
    node,
    teardown: () => {
      for (const url of urls) URL.revokeObjectURL(url);
      onChanged();
    },
  };
}
