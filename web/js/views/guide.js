/* Weft — the Guide: help pages, a short version of each, read on the phone.
 *
 * The list is every page under app/docs/guide/, in the order the files' weights give
 * it, with the folder's own intro on top if it has one. A page shows its short version
 * (the file's `content:`), then a link out to the whole page, which lives on GitHub or
 * a docs site (server.docs.url). The app never shows the full body.
 *
 * Tips are not listed here. Each lives on the screen it explains, behind its (?), and a
 * tip can link on to a page here (`doc:` links), which links on to the whole page. That
 * is the way in: tip, page, whole page, each a step deeper.
 *
 * The html comes rendered from the server (app/docs.py), escaped there, as a tip's does.
 */

import { el } from "../dom.js";
import { PURPOSE, panelHead } from "../panels.js";
import { apiFetch } from "../session.js";

/** One fetch for the whole guide, shared by the list and by `doc:` links from tips. */
let loading = null;

export function loadGuide() {
  loading ??= apiFetch("/api/guide").catch((err) => {
    loading = null;
    throw err;
  });
  return loading;
}

/**
 * Links inside a page: `doc:` opens another page on top, `tip:` opens the tip to read.
 * Outbound links are ordinary anchors and need nothing.
 */
function followLinks(node, { openDoc, openTip }) {
  node.addEventListener("click", (event) => {
    const doc = event.target.closest("a[data-doc]");
    const tip = event.target.closest("a[data-tip]");
    if (!doc && !tip) return;
    event.preventDefault();
    if (doc) openDoc(doc.dataset.doc);
    else openTip(tip.dataset.tip);
  });
}

/**
 * One page: the short version, and the whole page beyond the app.
 *
 * @param {object} panels
 * @param {{id: string, title: string, summary: ?string, html: string, url: ?string}} page
 * @param {{openDoc: (id: string) => void, openTip: (id: string) => void}} links
 */
export function renderGuidePage(panels, page, links) {
  const body = el("div", { class: "tip-body guide-text" });
  body.innerHTML = page.html;
  followLinks(body, links);

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: page.title, sub: page.summary ?? PURPOSE.guide }),
    el("div", { class: "panel-body guide-page" }, [
      body,
      ...(page.url ? [el("a", {
        class: "guide-whole", href: page.url, target: "_blank", rel: "noopener noreferrer",
        text: "Read the whole page →",
      })] : []),
    ]),
  ]);
}

/**
 * The list of pages.
 *
 * @param {object} panels
 * @param {{openDoc: (id: string) => void, openTip: (id: string) => void}} links
 */
export function renderGuide(panels, links) {
  const intro = el("div", { class: "tip-body guide-text", hidden: "" });
  followLinks(intro, links);
  const list = el("div", { class: "rows" }, [el("p", { class: "empty", text: "Looking…" })]);

  loadGuide().then((guide) => {
    if (guide.intro) {
      intro.innerHTML = guide.intro;
      intro.hidden = false;
    }
    list.replaceChildren(...(guide.pages.length
      ? guide.pages.map((page) => el("button", {
        type: "button", class: "row guide-row", onclick: () => links.openDoc(page.id),
      }, [
        el("div", { class: "row-main" }, [
          el("div", { class: "row-name", text: page.title }),
          ...(page.summary ? [el("div", { class: "row-sub", text: page.summary })] : []),
        ]),
        el("span", { class: "guide-chev", "aria-hidden": "true", text: "›" }),
      ]))
      : [el("p", { class: "empty", text: "No pages yet." })]));
  }).catch(() => {
    list.replaceChildren(el("p", { class: "empty", text: "Could not load the guide." }));
  });

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "Guide", sub: PURPOSE.guide }),
    el("div", { class: "panel-body guide" }, [intro, list]),
  ]);
}
