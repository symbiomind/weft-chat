/**
 * A file on a message, drawn as a card: what it is, and a way to play it or keep it.
 *
 * A picture shows as itself (views/chat.js). Anything else in the gallery, a song a
 * weft made or a PDF it fetched, shows as one of these: an icon for its kind, its name,
 * its type and size, a play button for sound and video, and a download button.
 *
 * **Nothing large is fetched until it is asked for.** Drawing a card costs one small
 * request, `/about`, and the answer is kept for the page's life, so a thread with the
 * same song in it ten times asks once. The bytes come only when play or download is
 * pressed, through the same credential as every other request (session.js), as a
 * blob: an `<audio>` element cannot send the bearer token itself.
 *
 * **What is played or saved is typed by the server.** The blob's type is the one the
 * gallery found in the file's bytes (app/gallery.py), and that is never a type a
 * browser renders as a page.
 */

import { el } from "./dom.js";
import { icon } from "./icons.js";
import * as providersApi from "./providers.js";

/** `/about` answers, by hash. A promise, so a card drawn twice waits on one request. */
const abouts = new Map();

/** blob: URLs of files fetched to play or save, by hash. */
const blobs = new Map();

/** Seed what is known about a file, from an upload's answer, so its card needs no request. */
export function remember(about) {
  if (about?.hash) abouts.set(about.hash, Promise.resolve(about));
}

function aboutOf(hash) {
  if (!abouts.has(hash)) {
    abouts.set(hash, providersApi.attachmentAbout(hash).catch((err) => {
      abouts.delete(hash);
      throw err;
    }));
  }
  return abouts.get(hash);
}

function blobUrl(hash) {
  if (!blobs.has(hash)) {
    blobs.set(hash, providersApi.attachmentBlob(hash)
      .then((blob) => URL.createObjectURL(blob))
      .catch((err) => {
        blobs.delete(hash);
        throw err;
      }));
  }
  return blobs.get(hash);
}

/** Let go of every file fetched to play or save. For a view being torn down. */
export function releaseFiles() {
  for (const pending of blobs.values()) {
    pending.then((url) => URL.revokeObjectURL(url), () => {});
  }
  blobs.clear();
}

/** "3.2 MB", "840 KB": how big, to the nearest thing a person reads. */
export function sizeText(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/** What to call a type on the card: "MP3", "PDF", "text". */
export function typeText(mime) {
  const known = {
    "application/pdf": "PDF",
    "audio/mpeg": "MP3",
    "audio/aac": "AAC",
    "audio/mp4": "M4A",
    "audio/ogg": "Ogg",
    "audio/flac": "FLAC",
    "audio/wav": "WAV",
    "audio/webm": "WebM sound",
    "video/mp4": "MP4 video",
    "video/quicktime": "MOV video",
    "video/webm": "WebM video",
    "text/markdown": "Markdown",
    "text/csv": "CSV",
    "application/json": "JSON",
    "text/plain": "text",
  };
  return known[mime] || "file";
}

function iconFor(mime = "") {
  if (mime.startsWith("audio/")) return "music";
  if (mime.startsWith("video/")) return "film";
  if (mime.startsWith("text/") || mime === "application/pdf" || mime === "application/json") {
    return "file-text";
  }
  return "file";
}

/** Hand a fetched file to the device under its own name. */
async function save(hash, name) {
  const url = await blobUrl(hash);
  const link = el("a", { href: url, download: name || "file", hidden: "" });
  document.body.append(link);
  link.click();
  link.remove();
}

/**
 * The card for one file. `{hash, name}` is what the message says; the rest is asked
 * for. `onError(message)` hears about a play or download that failed.
 */
export function fileCard({ hash, name }, { onError } = {}) {
  const title = el("span", { class: "file-name", text: name || "file" });
  const detail = el("span", { class: "file-detail", text: "" });
  const glyph = el("span", { class: "file-icon" }, [icon("file")]);
  const actions = el("span", { class: "file-actions" });
  const card = el("div", { class: "file-card pending", role: "group", "aria-label": name || "file" }, [
    el("div", { class: "file-row" }, [
      glyph,
      el("span", { class: "file-words" }, [title, detail]),
      actions,
    ]),
  ]);

  const failed = (what) => (err) => {
    card.classList.remove("busy");
    onError?.(`Could not ${what} ${name || "that file"}: ${err.detail || err.message || "it failed"}.`);
  };

  aboutOf(hash).then((about) => {
    card.classList.remove("pending");
    const mime = about.type || "";
    const shown = about.name || name || "file";
    title.textContent = shown;
    glyph.replaceChildren(icon(iconFor(mime)));
    detail.textContent = [typeText(mime), sizeText(about.size)].filter(Boolean).join(" · ");

    if (mime.startsWith("audio/") || mime.startsWith("video/")) {
      const play = el("button", {
        type: "button", class: "file-action", "aria-label": `play ${shown}`, title: "play",
        onclick: () => {
          card.classList.add("busy");
          blobUrl(hash).then((url) => {
            card.classList.remove("busy");
            const video = mime.startsWith("video/");
            const player = el(video ? "video" : "audio", {
              class: "file-player", controls: "", src: url,
              ...(video ? { playsinline: "" } : {}),
            });
            play.remove();
            card.append(player);
            player.play().catch(() => { /* the controls are there to press */ });
          }, failed("play"));
        },
      }, [icon("play")]);
      actions.append(play);
    }
    actions.append(el("button", {
      type: "button", class: "file-action", "aria-label": `download ${shown}`, title: "download",
      onclick: () => save(hash, shown).catch(failed("download")),
    }, [icon("download")]));
  }, () => {
    // Deleted from the gallery, or never this user's: the reference outlives the file.
    card.classList.remove("pending");
    card.classList.add("gone");
    detail.textContent = "no longer in the gallery";
  });

  return card;
}

/** A column of cards for a message's files. */
export function fileCards(files, options) {
  return el("div", { class: "bubble-files" }, files.map((file) => fileCard(file, options)));
}
