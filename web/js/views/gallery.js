/* Weft — the gallery: every picture and file put into a chat.
 *
 * The place between the chat and wherever a file is really kept. What the user sent
 * with 📎, what a weft's agent handed over through its door, and anything added here
 * with Add, newest first, as a grid of tiles. A tile opens the thing itself: the
 * picture, a player, the start of a text file. From there it can be downloaded, sent to
 * a weft, or deleted.
 *
 * **Sending is the only way a weft is shown something.** Added here, a file is shown
 * to no weft. "Send to a weft" opens that weft's chat with the file in the tray, and
 * the send is what shows it (app/gallery.py). Each item says which wefts have it.
 *
 * **Nothing here expires.** Deleting is the user's, and it is for good: a chat that
 * referred to the file shows it as gone.
 */

import { el } from "../dom.js";
import { dress, pairOf } from "../colours.js";
import { icon } from "../icons.js";
import { PURPOSE, panelHead } from "../panels.js";
import * as providersApi from "../providers.js";
import { fileUrl, iconFor, remember, saveFile, sizeText, typeText } from "../files.js";
import { ACCEPT } from "../tray.js";

/** The filter chips, and which items each one keeps. */
const FILTERS = [
  { id: "all", label: "All", keeps: () => true },
  { id: "pictures", label: "Pictures", keeps: (i) => i.kind === "picture" },
  { id: "sound", label: "Sound", keeps: (i) => i.type.startsWith("audio/") },
  { id: "video", label: "Video", keeps: (i) => i.type.startsWith("video/") },
  { id: "documents", label: "Documents",
    keeps: (i) => i.kind === "file" && !/^(audio|video)\//.test(i.type) },
];

/** How much of a text file the item page shows. */
const TEXT_PREVIEW_CHARS = 4000;

/** "12 Nov 2026": when something arrived. */
function day(iso) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric",
  });
}

/**
 * @param {object} panels
 * @param {{wefts: () => object[], canTake: (weft: object) => boolean,
 *   pictureUrl: (hash: string) => Promise<string|null>,
 *   openPicture: (src: string, name: string) => void,
 *   onSend: (weft: object, item: object, url: string|null) => void}} options
 *   `wefts` is the user's wefts, for names and colours; `canTake` says which of them a
 *   file can be sent to. `pictureUrl` is the chat's own cache of pictures, so a
 *   thumbnail here is the same blob the bubbles use. `onSend` opens a weft's chat with
 *   the item in the tray.
 */
export function renderGallery(panels, { wefts, canTake, pictureUrl, openPicture, onSend }) {
  let items = [];
  let filter = "all";

  const status = el("p", { class: "gallery-status", text: "Looking…" });
  const grid = el("div", { class: "gallery-grid" });
  const chips = el("div", { class: "gallery-chips", role: "radiogroup", "aria-label": "show" });
  const error = el("p", { class: "form-error", hidden: "" });

  const picker = el("input", { type: "file", accept: ACCEPT, multiple: "", hidden: "" });
  const add = el("button", {
    type: "button", class: "small gallery-add", onclick: () => picker.click(),
  }, [icon("paperclip"), el("span", { text: "Add" })]);

  picker.addEventListener("change", async () => {
    const files = [...picker.files];
    picker.value = "";
    if (!files.length) return;
    error.hidden = true;
    add.disabled = true;
    status.textContent = `Adding ${files.length === 1 ? files[0].name : `${files.length} files`}…`;
    const failed = [];
    for (const file of files) {
      try {
        const about = await providersApi.uploadAttachment(file);
        if (about.kind === "file") remember(about);
      } catch (err) {
        failed.push(`${file.name}: ${err.detail || err.message || "it failed"}`);
      }
    }
    add.disabled = false;
    if (failed.length) {
      error.textContent = `Could not add ${failed.join("; ")}`;
      error.hidden = false;
    }
    load();
  });

  function paintChips() {
    chips.replaceChildren(...FILTERS
      .filter((f) => f.id === "all" || items.some(f.keeps))
      .map((f) => el("button", {
        type: "button", role: "radio", class: "gallery-chip",
        "aria-checked": String(f.id === filter),
        text: f.label,
        onclick: () => {
          filter = f.id;
          paint();
        },
      })));
    chips.hidden = items.length < 2;
  }

  function paint() {
    if (!FILTERS.some((f) => f.id === filter && (f.id === "all" || items.some(f.keeps)))) {
      filter = "all";
    }
    paintChips();
    const keeps = FILTERS.find((f) => f.id === filter).keeps;
    const shown = items.filter(keeps);
    const total = items.reduce((sum, i) => sum + (i.size || 0), 0);
    status.textContent = items.length
      ? `${items.length} thing${items.length === 1 ? "" : "s"} · ${sizeText(total)}`
      : "";
    grid.replaceChildren(...shown.map(tile));
    if (!items.length) grid.replaceChildren(...empty());
  }

  function empty() {
    return [
      el("div", { class: "gallery-empty" }, [
        el("p", { class: "empty", text: "Nothing here yet." }),
        el("p", {
          class: "hint",
          text: "Pictures and files you send with 📎, and ones your wefts' agents hand you "
            + "through their doors, land here. Add puts one here without sending it.",
        }),
      ]),
    ];
  }

  /** The weft that brought an item, while it still exists. */
  const weftOf = (id) => (id ? wefts().find((w) => w.id === id) : null);

  function tile(item) {
    const brought = weftOf(item.weft_id);
    const face = item.kind === "picture"
      ? el("img", { alt: "", loading: "lazy" })
      : el("span", { class: "gallery-glyph" }, [icon(iconFor(item.type))]);
    if (item.kind === "picture") {
      pictureUrl(item.hash).then((url) => {
        if (url) face.src = url;
        else button.classList.add("gone");
      });
    }
    const button = el("button", {
      type: "button", class: `gallery-tile ${item.kind}`,
      title: item.name, "aria-label": `${item.name}, ${typeText(item.type)}`,
      onclick: () => panels.push((p) => renderItem(p, item), { page: "gallery-item" }),
    }, [
      face,
      el("span", { class: "gallery-caption" }, [
        el("span", { class: "gallery-name", text: item.kind === "picture" ? "" : item.name }),
        el("span", { class: "gallery-type", text: item.kind === "picture" ? "" : typeText(item.type) }),
      ]),
      ...(brought ? [el("span", { class: "gallery-pea", title: `from ${brought.name}` })] : []),
    ]);
    if (brought) dress(button, pairOf(brought));
    return button;
  }

  /** One item, opened: the thing itself, what it is, and what can be done with it. */
  function renderItem(itemPanels, item) {
    const brought = weftOf(item.weft_id);
    const message = el("p", { class: "form-error", hidden: "" });
    const fail = (what) => (err) => {
      message.textContent = `Could not ${what}: ${err.detail || err.message || "it failed"}.`;
      message.hidden = false;
    };

    const preview = el("div", { class: `gallery-preview ${item.kind}` });
    if (item.kind === "picture") {
      const img = el("img", { alt: item.name });
      pictureUrl(item.hash).then((url) => {
        if (url) img.src = url;
      });
      preview.append(el("button", {
        type: "button", class: "gallery-zoom", "aria-label": `open ${item.name} full size`,
        onclick: () => img.src && openPicture(img.src, item.name),
      }, [img]));
    } else if (/^(audio|video)\//.test(item.type)) {
      const video = item.type.startsWith("video/");
      preview.append(el("span", { class: "gallery-glyph big" }, [icon(iconFor(item.type))]));
      fileUrl(item.hash).then((url) => {
        const player = el(video ? "video" : "audio", {
          class: "file-player", controls: "", src: url, ...(video ? { playsinline: "" } : {}),
        });
        preview.replaceChildren(...(video ? [] : [preview.firstChild]), player);
      }, fail("load it"));
    } else if (item.type.startsWith("text/") || item.type === "application/json") {
      const text = el("pre", { class: "gallery-text", text: "…" });
      preview.append(text);
      providersApi.attachmentBlob(item.hash)
        .then((blob) => blob.slice(0, TEXT_PREVIEW_CHARS * 4).text())
        .then((words) => {
          text.textContent = words.length > TEXT_PREVIEW_CHARS
            ? `${words.slice(0, TEXT_PREVIEW_CHARS)}\n…` : words;
        }, fail("read it"));
    } else {
      preview.append(el("span", { class: "gallery-glyph big" }, [icon(iconFor(item.type))]));
    }

    const from = item.source === "weft"
      ? `${brought ? brought.name : "A weft"} handed it over`
      : item.source === "user" ? "You added it" : "From before the gallery";
    const shownTo = item.shown_to.map(weftOf).filter(Boolean);
    const shown = shownTo.length
      ? el("div", { class: "gallery-wefts" }, shownTo.map((w) => {
        const chip = el("span", { class: "gallery-weft" }, [
          el("span", { class: "led" }), el("span", { text: w.name }),
        ]);
        dress(chip, pairOf(w));
        return chip;
      }))
      : el("p", { class: "hint", text: "No weft has it yet. Send it to one to hand it over." });

    const send = el("button", {
      type: "button", class: "small", text: "Send to a weft…",
      onclick: () => {
        const takers = wefts().filter(canTake);
        actions.replaceWith(el("div", { class: "gallery-send" }, [
          el("p", { class: "hint", text: takers.length
            ? "It goes in that weft's tray, to send with your next message."
            : "No weft can take it yet: give one an engine first." }),
          el("div", { class: "gallery-wefts" }, takers.map((w) => {
            const chip = el("button", {
              type: "button", class: "gallery-weft",
              onclick: async () => {
                const url = item.kind === "picture" ? await pictureUrl(item.hash) : null;
                onSend(w, item, url);
              },
            }, [el("span", { class: "led" }), el("span", { text: w.name })]);
            dress(chip, pairOf(w));
            return chip;
          })),
          el("button", {
            type: "button", class: "small", text: "cancel",
            onclick: (event) => event.currentTarget.parentElement.replaceWith(actions),
          }),
        ]));
      },
    });

    const remove = el("button", {
      type: "button", class: "small danger", text: "Delete",
      onclick: () => {
        const confirmRow = el("div", { class: "row-confirm" }, [
          el("span", { text: `Delete “${item.name}” for good? Chats that show it will say it's gone.` }),
          el("button", {
            class: "small danger", text: "delete",
            onclick: async () => {
              try {
                await providersApi.deleteGalleryItem(item.hash);
              } catch (err) {
                if (err.status !== 404) {
                  fail("delete it")(err);
                  return;
                }
              }
              items = items.filter((i) => i.hash !== item.hash);
              paint();
              itemPanels.pop();
            },
          }),
          el("button", {
            class: "small", text: "cancel", onclick: () => confirmRow.replaceWith(actions),
          }),
        ]);
        actions.replaceWith(confirmRow);
      },
    });

    const actions = el("div", { class: "row-actions gallery-actions" }, [
      el("button", {
        type: "button", class: "small", text: "Download",
        onclick: () => saveFile(item.hash, item.kind === "picture"
          ? `${item.name.replace(/\.[A-Za-z0-9]{2,5}$/, "")}.webp` : item.name)
          .catch(fail("download it")),
      }),
      send,
      remove,
    ]);

    return el("div", { class: "panel-inner" }, [
      panelHead(itemPanels, { title: item.name, sub: [typeText(item.type), sizeText(item.size)].join(" · ") }),
      el("div", { class: "panel-body gallery-item" }, [
        preview,
        el("p", { class: "gallery-from", text: `${from} · ${day(item.created_at)}` }),
        el("h3", { text: "Wefts that have it" }),
        shown,
        actions,
        message,
      ]),
    ]);
  }

  async function load() {
    try {
      items = (await providersApi.listGallery()).items;
      paint();
    } catch (err) {
      status.textContent = "";
      grid.replaceChildren(el("p", { class: "empty", text: "Could not load the gallery." }));
    }
  }

  load();

  return {
    node: el("div", { class: "panel-inner" }, [
      panelHead(panels, { title: "Gallery", sub: PURPOSE.gallery }),
      el("div", { class: "panel-body gallery" }, [
        el("div", { class: "gallery-head" }, [status, add]),
        chips,
        error,
        grid,
        el("p", {
          class: "hint gallery-foot",
          text: "Nothing here expires. A weft only has what was sent in its own chat.",
        }),
        picker,
      ]),
    ]),
    onReveal: () => load(),
  };
}
