/* Weft — the attachment tray: pictures and files waiting to go with the next message.
 *
 * The 📎 petal opens the device's own picker (on a phone, that already offers the
 * camera, the photo library and files; `capture` is deliberately NOT set, because it
 * would force the camera). Each chosen picture or file uploads straight away and sits
 * in a row above the input, with an ✕ to take it back out, until send takes the lot. A
 * picture shows as its thumbnail; a file (a PDF, a song) as its icon and name.
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
import { icon } from "./icons.js";

/** Mirrors app/attachments.py MAX_PER_MESSAGE. The server refuses more. */
export const MAX_PICTURES = 6;

/**
 * What the picker offers. Mirrors what app/gallery.py keeps; the server decides from
 * the bytes either way, so this only spares choosing something that will be refused.
 */
export const ACCEPT = [
  "image/*", "application/pdf", "audio/*", "video/*",
  "text/plain", "text/markdown", "text/csv", "application/json",
  ".md", ".markdown", ".csv", ".txt", ".json",
].join(",");

/** Whether a chosen file is sent as a picture: the server is told so, and holds it to it. */
const isPicture = (file) => (file.type || "").startsWith("image/");

/**
 * @param upload   `(file) => Promise<{hash, kind}>`, the PUT /api/attachments call.
 * @param onError  `(name, message)` when a picture cannot be attached.
 * @param onUploaded `(hash, url)` once a picture is on the server; `url` is the local
 *                 blob: URL. Returning true takes it over (the callee now revokes it);
 *                 otherwise the tray revokes it when the picture leaves the tray.
 * @param onChange `()` whenever the tray gains or loses a picture.
 */
export function createTray({ upload, onError, onUploaded, onChange } = {}) {
  const input = el("input", {
    type: "file",
    accept: ACCEPT,
    multiple: "",
    hidden: "",
  });
  const element = el("div", { id: "tray", role: "list", "aria-label": "attachments to send" });

  /** @type {{name: string, url: string|null, hash: string|null, kind: string,
   *          failed: boolean, handed: boolean, node: HTMLElement}[]} */
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
    if (item.url && !item.handed) URL.revokeObjectURL(item.url);
    changed();
  }

  async function add(file) {
    const picture = isPicture(file);
    const name = file.name || (picture ? "picture" : "file");
    const url = picture ? URL.createObjectURL(file) : null;
    const face = picture
      ? el("img", { src: url, alt: name })
      : el("span", { class: "tray-file" }, [icon("file"), el("span", { text: name })]);
    const node = el("div", { class: "tray-item pending", role: "listitem", title: name }, [face]);
    const item = { name, url, hash: null, kind: picture ? "picture" : "file",
                   failed: false, handed: false, node };
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
      const { hash, kind } = await upload(file);
      // Removed while it was uploading: nothing to show it in any more.
      if (!items.includes(item)) return;
      item.hash = hash;
      item.kind = kind === "file" ? "file" : "picture";
      item.handed = item.kind === "picture" && url ? onUploaded?.(hash, url) === true : false;
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
        `${files.length - Math.max(room, 0)} more`,
        `a message can carry ${MAX_PICTURES} pictures and files at most`,
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

    /** True while a picture or file is still on its way to the server. */
    get uploading() {
      return items.some((i) => !i.hash && !i.failed);
    },

    /** True when there is at least one picture or file ready to send. */
    get ready() {
      return items.some((i) => i.hash);
    },

    /**
     * Empty the tray and return what was in it, as the chat request refers to it.
     * Anything that failed to upload is dropped; its error was already shown.
     */
    take() {
      const pictures = items
        .filter((i) => i.hash)
        .map((i) => ({ hash: i.hash, name: i.name, ...(i.kind === "file" ? { kind: "file" } : {}) }));
      for (const item of [...items]) remove(item);
      return pictures;
    },

    teardown() {
      for (const item of [...items]) remove(item);
      document.documentElement.removeAttribute("data-tray");
    },
  };
}
