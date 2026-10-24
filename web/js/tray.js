/* Weft — the attachment tray: pictures waiting to go with the next message.
 *
 * The 📎 petal opens the device's own picker (on a phone, that already offers the
 * camera, the photo library and files; `capture` is deliberately NOT set, because it
 * would force the camera). Each chosen picture uploads straight away and sits in a row
 * of thumbnails above the input, with an ✕ to take it back out, until send takes the
 * lot.
 *
 * Uploading on choice rather than on send means the chat request carries only hashes,
 * and a slow upload happens while the user is still typing instead of after they have
 * pressed send.
 *
 * A thumbnail shows the picture from the device itself (a blob: URL of the chosen
 * file), so it appears the moment it is picked. Once uploaded, that same URL is handed
 * to the caller so the bubble can show it without fetching back what was just sent.
 */

import { el } from "./dom.js";

/** Mirrors app/attachments.py MAX_PER_MESSAGE. The server refuses more. */
export const MAX_PICTURES = 6;

/**
 * @param upload   `(file) => Promise<{hash}>`, the PUT /api/attachments call.
 * @param onError  `(name, message)` when a picture cannot be attached.
 * @param onUploaded `(hash, url)` once a picture is on the server; `url` is the local
 *                 blob: URL. Returning true takes it over (the callee now revokes it);
 *                 otherwise the tray revokes it when the picture leaves the tray.
 * @param onChange `()` whenever the tray gains or loses a picture.
 */
export function createTray({ upload, onError, onUploaded, onChange } = {}) {
  const input = el("input", {
    type: "file",
    accept: "image/*",
    multiple: "",
    hidden: "",
  });
  const element = el("div", { id: "tray", role: "list", "aria-label": "pictures to send" });

  /** @type {{name: string, url: string, hash: string|null, failed: boolean,
   *          handed: boolean, node: HTMLElement}[]} */
  let items = [];

  function changed() {
    element.hidden = items.length === 0;
    // On the root so the log can make room for the row above the bar.
    document.documentElement.toggleAttribute("data-tray", items.length > 0);
    onChange?.();
  }

  function remove(item) {
    items = items.filter((i) => i !== item);
    item.node.remove();
    // Once handed over, the URL belongs to whoever took it.
    if (!item.handed) URL.revokeObjectURL(item.url);
    changed();
  }

  async function add(file) {
    const name = file.name || "picture";
    const url = URL.createObjectURL(file);
    const node = el("div", { class: "tray-item pending", role: "listitem", title: name }, [
      el("img", { src: url, alt: name }),
    ]);
    const item = { name, url, hash: null, failed: false, handed: false, node };
    node.append(el("button", {
      type: "button",
      class: "tray-remove",
      "aria-label": `remove ${name}`,
      text: "✕",
      onclick: () => remove(item),
    }));
    items.push(item);
    element.append(node);
    changed();

    try {
      const { hash } = await upload(file);
      // Removed while it was uploading: nothing to show it in any more.
      if (!items.includes(item)) return;
      item.hash = hash;
      item.handed = onUploaded?.(hash, url) === true;
      node.classList.remove("pending");
    } catch (err) {
      if (!items.includes(item)) return;
      item.failed = true;
      node.classList.remove("pending");
      node.classList.add("failed");
      onError?.(name, err.detail || err.message || "the upload failed");
    }
    changed();
  }

  element.hidden = true;

  input.addEventListener("change", () => {
    const room = MAX_PICTURES - items.length;
    const files = [...input.files];
    // Cleared so choosing the same picture again still fires `change`.
    input.value = "";
    if (files.length > room) {
      onError?.(
        `${files.length - Math.max(room, 0)} picture(s)`,
        `a message can carry ${MAX_PICTURES} pictures at most`,
      );
    }
    for (const file of files.slice(0, Math.max(room, 0))) add(file);
  });

  return {
    element,
    input,

    /** Open the device's picker. Must be called from inside a tap. */
    choose() {
      input.click();
    },

    /** True while a picture is still on its way to the server. */
    get uploading() {
      return items.some((i) => !i.hash && !i.failed);
    },

    /** True when there is at least one picture ready to send. */
    get ready() {
      return items.some((i) => i.hash);
    },

    /**
     * Empty the tray and return what was in it, as the chat request refers to it.
     * Pictures that failed to upload are dropped; their error was already shown.
     */
    take() {
      const pictures = items
        .filter((i) => i.hash)
        .map((i) => ({ hash: i.hash, name: i.name }));
      for (const item of [...items]) remove(item);
      return pictures;
    },

    teardown() {
      for (const item of [...items]) remove(item);
      document.documentElement.removeAttribute("data-tray");
    },
  };
}
