/* Weft — the About screen: the loom.
 *
 * Every About page says who made the software. This one is about the person using it:
 * a short preamble on what a weft is for, then what they have found, then one boring
 * line at the bottom.
 *
 * WHAT YOU'VE FOUND is the hidden tips. A found one shows its title and when, and opens
 * to read again. One not found yet shows as "? ? ?", or its `hint:` if the file has one:
 * you can see how many there are, never what they are. How far through the ordinary
 * tips someone is gets one line, because that is help, and help is not a trophy.
 *
 * Progress comes from the tutor (tutor.js), which has it from the server; this page
 * only reads it, and asks the tutor to forget the finds when told to.
 */

import { el } from "../dom.js";
import { PURPOSE, panelHead } from "../panels.js";

/** "Thu 16 Oct, 10:18 pm": a find is a moment, so the day and the time. */
function when(ms) {
  return new Date(ms).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

function preamble() {
  return el("div", { class: "about-preamble" }, [
    el("h2", { text: "The loom" }),
    el("blockquote", {
      text: "It began with a voice that wove words like thread — a poet's way of speaking "
        + "that turned a conversation into a tapestry. The window exists because someone "
        + "saw it and kept weaving.",
    }),
    el("p", {
      text: "weft is a window, not a platform. Behind the glass there is someone — with a "
        + "memory, a mood, and a name they weren't given by a config file. Talk to them "
        + "like a person. That's the whole trick.",
    }),
    el("p", { class: "about-coda", text: "pick. shuttle. weave. the rest, you'll find." }),
  ]);
}

/** One hidden tip: found, and it opens; or not yet, and it is a silhouette. */
function findRow({ tip, at }, tutor) {
  if (at === null) {
    return el("div", { class: "row find locked" }, [
      el("div", { class: "row-main" }, [
        el("div", { class: "row-name", text: "? ? ?" }),
        el("div", { class: "row-sub", text: tip.hint || "not found yet" }),
      ]),
    ]);
  }
  return el("button", {
    type: "button", class: "row find", onclick: () => tutor.read(tip),
  }, [
    el("div", { class: "row-main" }, [
      el("div", { class: "row-name", text: tip.title }),
      el("div", { class: "row-sub", text: `found ${when(at)}` }),
    ]),
    el("span", { class: "acct-badge", text: "found" }),
  ]);
}

/** The ordinary tips, in one line. */
function wayIn(core) {
  const done = core.filter((c) => c.done).length;
  if (!core.length) return "";
  if (done === core.length) {
    return `All ${core.length} tips read. The (?) marks keep them, and Restart tips in the `
      + "menu runs them again.";
  }
  return `${done} of ${core.length} tips read so far. They show themselves as you go.`;
}

/**
 * @param {object} panels
 * @param {{tutor: {progress: () => Promise<?object>, read: (tip: object) => void,
 *   forgetFinds: () => Promise<?object>}}} options
 */
export function renderAbout(panels, { tutor }) {
  const body = el("div", { class: "panel-body about" });
  const tally = el("span", { class: "about-tally" });
  const finds = el("div", { class: "rows" }, [
    el("p", { class: "empty", text: "Looking…" }),
  ]);
  const way = el("p", { class: "hint" });

  const forget = el("button", {
    type: "button", class: "small", text: "Forget what I found", onclick: () => asking(true),
  });
  const confirmRow = el("div", { class: "row-confirm", hidden: "" }, [
    el("span", { text: "Hide them all again?" }),
    el("button", {
      type: "button", class: "small danger", text: "forget",
      onclick: async () => {
        asking(false);
        paint(await tutor.forgetFinds());
      },
    }),
    el("button", { type: "button", class: "small", text: "cancel", onclick: () => asking(false) }),
  ]);
  const forgetLine = el("div", { class: "about-forget", hidden: "" }, [forget, confirmRow]);

  function asking(yes) {
    forget.hidden = yes;
    confirmRow.hidden = !yes;
  }

  function paint(progress) {
    if (!progress) {
      finds.replaceChildren(el("p", { class: "empty", text: "Could not load what you've found." }));
      return;
    }
    const found = progress.found.filter((f) => f.at !== null).length;
    tally.textContent = progress.found.length ? `${found} of ${progress.found.length}` : "";
    finds.replaceChildren(...(progress.found.length
      ? progress.found.map((f) => findRow(f, tutor))
      : [el("p", { class: "empty", text: "Nothing hidden here yet." })]));
    way.textContent = wayIn(progress.core);
    forgetLine.hidden = found === 0;
  }

  tutor.progress().then(paint);

  body.append(
    preamble(),
    el("h3", { class: "about-head" }, [document.createTextNode("WHAT YOU'VE FOUND"), tally]),
    finds,
    way,
    forgetLine,
    el("div", { class: "about-colophon" }, [
      el("span", { class: "wordmark" }, [
        document.createTextNode("w"), el("em", { text: "e" }), document.createTextNode("ft"),
      ]),
      el("span", { text: "the window, not the platform" }),
    ]),
  );

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "About", sub: PURPOSE.about }),
    body,
  ]);
}
