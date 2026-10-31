/* Weft — the tip card, its ring, and the (?) marks.
 *
 * tips.js decides which tip is next; this puts it on screen. The chat view tells it
 * three things and nothing else: where the user is (`where()`, re-read on every
 * `visit()`), what just happened (`emit()`), and what the account looks like
 * (`context()`, for tips that only apply to a new account).
 *
 * THE CARD IS NOT MODAL. It sits low on the screen with no scrim, so the thing it points
 * at stays tappable: the welcome says "tap ☰", and tapping ☰ is what finishes it. A ring
 * marks the thing, redrawn every frame while it shows, because what it circles can be
 * sliding in with the drawer.
 *
 * WHEN A TIP MAY APPEAR. Only after a real arrival on a page, a moment after, so a
 * drawer or panel has finished moving. Never over a form (a panel with no page name),
 * never while `canShow()` says no (a reply streaming, the Pick menu open), and never
 * twice for the same arrival: repaints of the page you are already on are not visits.
 *
 * WHO SEES IT. Every signed-in account. Without one there is nothing to show, and the
 * tutor is inert: no card, no marks, no switch, no request.
 *
 * WHERE PROGRESS LIVES. With the account: it arrives with the list, and
 * every finish, the switch and a restart go back as they happen. A write that fails is
 * not retried or reported; the tip it finished may show again on the next visit, which
 * is the cheapest possible way to be wrong.
 */

import { el } from "./dom.js";
import { apiFetch } from "./session.js";
import { createTips } from "./tips.js";

/** What finishing by doing looks like, said under the tip that waits for it. */
const DOING = {
  "open:drawer": "Or just tap ☰. That counts.",
  "open:providers": "Or just open Providers.",
  "providers-changed": "Or add one. That counts too.",
  "wefts-changed": "Or add one. That counts too.",
  "shelf-open": "Or hold send. That counts.",
};

/** Put a change to the progress on the server. Fire and forget, see above. */
function save(change) {
  const send = (path, method, body) => apiFetch(path, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let sent;
  if (change.done) sent = send("/api/tips/done", "POST", { ids: change.done });
  else if ("on" in change) sent = send("/api/tips/on", "PUT", { on: change.on });
  else if (change.restart) sent = send("/api/tips/restart", "POST", { what: change.restart });
  sent?.catch(() => {});
}

/** Tips progress used to live in the browser. It is not carried over; tidy it away. */
function forgetLocalProgress() {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("weft.tips")) localStorage.removeItem(key);
    }
  } catch {
    // No storage, or blocked: nothing to tidy.
  }
}

/** How long after arriving before a tip may land: the drawer takes .28s to slide in. */
const SETTLE_MS = 380;

const INERT = {
  visit() {}, emit() {}, setReady() {}, drawerControls: () => [], teardown() {},
  progress: () => Promise.resolve(null), read() {}, readId() {}, forgetFinds: () => Promise.resolve(null),
  whenLoaded: Promise.resolve(false), has: () => false, find: () => false,
};

/**
 * `extras(tip)` returns controls to show under a tip's words, for the few tips that are
 * more than words (ELIZA's card: show her in the Pick menu, start her over). Called on
 * every show, so the controls are always drawn from the state as it is now.
 *
 * @param {{user: ?{id:string}, where: () => string,
 *   context: () => {wefts:number}, canShow: () => boolean,
 *   extras?: (tip: object) => Node[], openDoc?: (id: string) => void}} options
 *   `openDoc` follows a tip's `doc:` link to a guide page (views/guide.js).
 */
export function createTutor({ user, where, context, canShow, extras = () => [], openDoc = () => {} }) {
  if (!user) return INERT;
  forgetLocalProgress();

  const tips = createTips({ save });
  let loaded = false;
  /** @type {(value: boolean) => void} */
  let markLoaded;
  const whenLoaded = new Promise((resolve) => { markLoaded = resolve; });
  let ready = false;
  let page = null;
  let timer = 0;
  /** @type {{tip: object, byHand: boolean} | null} */
  let showing = null;

  // ---- the card ----
  // Polite and live, so a screen reader hears a tip arrive, as an eye sees it slide in.
  const card = el("div", {
    id: "tip", role: "dialog", "aria-labelledby": "tip-title", "aria-live": "polite", hidden: "",
  });
  const ring = el("div", { id: "tip-ring", "aria-hidden": "true", hidden: "" });
  document.body.append(ring, card);

  card.addEventListener("click", (event) => {
    const doc = event.target.closest("a[data-doc]");
    if (doc) {
      // The next step deeper: the tip makes way for its page.
      event.preventDefault();
      hide();
      openDoc(doc.dataset.doc);
      return;
    }
    const link = event.target.closest("a[data-tip]");
    if (link) {
      event.preventDefault();
      const tip = tips.get(link.dataset.tip);
      if (!tip) return;
      // A hidden tip at the end of a link is found by following it.
      if (tip.group === "secret") tips.finish(tip.id);
      show(tip, true);
      return;
    }
    const act = event.target.closest("[data-act]")?.dataset.act;
    if (act === "got") {
      const { tip, byHand } = showing;
      tips.finish(tip.id);
      hide();
      paintMarks();
      // Finishing one can unlock the next on this page: offer it straight away.
      if (!byHand) offer(260);
    } else if (act === "off") {
      tips.setOn(false);
      hide();
    }
  });

  function onKeydown(event) {
    // Escape puts the tip away before it closes the panel behind it.
    if (event.key === "Escape" && showing) {
      event.stopPropagation();
      hide();
    }
  }
  document.addEventListener("keydown", onKeydown, true);

  function show(tip, byHand = false) {
    showing = { tip, byHand };
    const ctx = context();
    const core = tips.core(ctx);
    const at = core.indexOf(tip);

    const eyebrow = el("div", { class: "tip-eyebrow" }, [
      el("span", {
        text: tip.group === "secret" ? "Hidden tip"
          : at >= 0 ? `Tip ${at + 1} of ${core.length}` : "Tip",
      }),
    ]);
    if (at >= 0) {
      eyebrow.append(el("span", { class: "tip-dots", "aria-hidden": "true" }, core.map((t) =>
        el("i", { class: t === tip || tips.isDone(t.id) ? "on" : "" }))));
    }
    const body = el("div", { class: "tip-body" });
    // Rendered on the server from the shipped files (app/tips.py), escaped there.
    body.innerHTML = tip.html;

    const actions = el("div", { class: "tip-actions" });
    // Not on a find: the switch does not stop those, so offering it there would lie.
    if (!byHand && tip.group !== "secret") {
      actions.append(el("button", { type: "button", class: "tip-off", "data-act": "off", text: "Turn off tips" }));
    }
    actions.append(el("button", { type: "button", class: "tip-got", "data-act": "got", text: byHand ? "Close" : "Got it" }));

    card.replaceChildren(
      eyebrow,
      el("h3", { id: "tip-title", text: tip.title }),
      body,
      ...extras(tip),
      ...(!byHand && DOING[tip.done_on] ? [el("p", { class: "tip-or", text: DOING[tip.done_on] })] : []),
      actions,
    );
    // Above the input bar on the chat; at the foot of the screen everywhere else.
    const bar = document.getElementById("bar");
    const lift = page === "chat" && bar ? bar.offsetHeight + 12 : 16;
    card.style.setProperty("--tip-lift", `${lift}px`);
    card.hidden = false;
    pointAt(byHand ? null : tip.point_at);
  }

  function hide() {
    showing = null;
    card.hidden = true;
    pointAt(null);
  }

  // ---- the ring ----
  let ringTarget = null;
  let frame = 0;

  function pointAt(selector) {
    ringTarget = selector;
    cancelAnimationFrame(frame);
    if (!selector) {
      ring.hidden = true;
      return;
    }
    const draw = () => {
      // The first one that is actually on screen: a hidden panel's copy does not count.
      const target = [...document.querySelectorAll(ringTarget)]
        .find((node) => node.getClientRects().length);
      if (!target) {
        ring.hidden = true;
      } else {
        const box = target.getBoundingClientRect();
        const pad = 5;
        Object.assign(ring.style, {
          left: `${box.left - pad}px`, top: `${box.top - pad}px`,
          width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px`,
        });
        ring.hidden = false;
      }
      frame = requestAnimationFrame(draw);
    };
    draw();
  }

  // ---- arriving ----

  /** Offer this page's next tip, once things have settled. */
  function offer(delay = SETTLE_MS) {
    clearTimeout(timer);
    // Not gated on the switch: with tips off, `next` still offers a hidden one.
    if (!loaded || !ready) return;
    const here = page;
    timer = setTimeout(() => {
      if (where() !== here || showing || !canShow() || document.visibilityState !== "visible") return;
      const tip = tips.next(here, context());
      if (tip) show(tip);
    }, delay);
  }

  function visit() {
    const now = where();
    if (now === page) return;
    page = now;
    hide();
    if (loaded && ready) settle(tips.event(`open:${page}`, context()));
    offer();
  }

  /** What an event did: put away a tip it finished, show one it asked for. */
  function settle({ finished, show: shown }) {
    if (showing && finished.includes(showing.tip)) hide();
    // `event` only returns a tip to show that may show: help with tips on, a find always.
    if (shown) show(shown);
    if (finished.length) paintMarks();
  }

  function emit(name) {
    // Not before the account is known: until the Pick menu has loaded it looks like a
    // new one, and an event then would finish setup tips that never applied.
    if (loaded && ready) settle(tips.event(name, context()));
  }

  // ---- (?) marks ----
  // On any [data-section] whose name a tip claims, whatever screen built it and however
  // late: the Providers list is drawn after its read comes back.
  function paintMarks() {
    if (!loaded) return;
    const ctx = context();
    for (const head of document.querySelectorAll("[data-section]")) {
      const tip = tips.forSection(head.dataset.section);
      let mark = head.querySelector(".qmark");
      if (!tip) {
        mark?.remove();
        continue;
      }
      if (!mark) {
        mark = el("button", { type: "button", class: "qmark", text: "?" });
        mark.addEventListener("click", () => show(tips.get(mark.dataset.tip), true));
        // Inside the heading, so it sits beside the words in a row or a column alike.
        (head.querySelector("h2") ?? head).append(mark);
      }
      mark.dataset.tip = tip.id;
      mark.setAttribute("aria-label", `help: ${tip.title}`);
      mark.title = tip.title;
      // Lit when it is the next thing to learn here.
      mark.classList.toggle("fresh", tip.group === "core" && tips.ready(tip.id, ctx));
    }
  }

  let marksQueued = false;
  const observer = new MutationObserver((records) => {
    // A mark going in is a mutation too; reacting to our own would never stop.
    const ours = records.every((r) => [...r.addedNodes, ...r.removedNodes]
      .every((node) => node.classList?.contains("qmark")));
    if (ours || marksQueued) return;
    marksQueued = true;
    requestAnimationFrame(() => {
      marksQueued = false;
      paintMarks();
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });

  apiFetch("/api/tips")
    .then((answer) => {
      tips.setList(answer.tips ?? []);
      tips.setProgress(answer);
      loaded = true;
      markLoaded(true);
      paintMarks();
      if (page && ready) settle(tips.event(`open:${page}`, context()));
      offer();
    })
    .catch(() => {
      // No tips is a working window. Nothing to tell anyone.
      markLoaded(false);
    });

  return {
    visit,
    emit,

    /** The account is known (the Pick menu has loaded): `when:` can be judged now. */
    setReady() {
      if (ready) return;
      ready = true;
      // The arrival that happened while it was loading counts now.
      if (loaded && page) settle(tips.event(`open:${page}`, context()));
      offer();
    },

    /** The switch and the restart, for the drawer. */
    drawerControls() {
      const box = el("input", { type: "checkbox", id: "tips-on" });
      box.checked = tips.isOn();
      box.addEventListener("change", () => {
        tips.setOn(box.checked);
        if (!box.checked) hide();
      });
      const restart = el("button", {
        type: "button", class: "small", text: "Restart tips",
        onclick: () => {
          tips.restart("help");
          box.checked = true;
          hide();
          paintMarks();
          offer();
        },
      });
      return [
        el("label", { class: "toggle-row" }, [
          box,
          el("span", { text: "Show tips" }),
          el("span", { class: "switch", "aria-hidden": "true" }),
        ]),
        el("div", { class: "tips-line" }, [
          el("p", {
            class: "hint",
            text: "Kept with your account, on every device. The (?) marks stay either way.",
          }),
          restart,
        ]),
      ];
    },

    /**
     * What the About page shows: the core run with what is done, and every hidden tip
     * with when it was found. Null when the tips could not be loaded.
     */
    async progress() {
      if (!(await whenLoaded)) return null;
      const ctx = context();
      return {
        core: tips.core(ctx).map((tip) => ({ tip, done: tips.isDone(tip.id) })),
        found: tips.found(),
      };
    },

    /** Open a tip to read, as a (?) does. */
    read(tip) {
      show(tip, true);
    },

    /** Open a tip to read by its id, as a `tip:` link in a guide page does. A hidden tip
     *  at the end of a link is found by following it, as from another tip. */
    readId(id) {
      const tip = loaded ? tips.get(id) : null;
      if (!tip) return;
      if (tip.group === "secret") tips.finish(tip.id);
      show(tip, true);
    },

    /** Resolves once the tips have loaded (true), or failed to (false). */
    whenLoaded,

    /** Whether this account has done or found the tip `id`. False until loaded. */
    has(id) {
      return loaded && tips.isDone(id);
    },

    /**
     * Find a hidden tip by doing what finds it, and show it, found or not before. For a
     * find that a tap makes directly, rather than one waiting on an event. False when
     * the tips are not loaded, or there is no such tip.
     */
    find(id) {
      const tip = loaded ? tips.get(id) : null;
      if (!tip) return false;
      tips.finish(id);
      show(tip, true);
      return true;
    },

    /** Forget the hidden tips found. The help's progress is left alone. */
    async forgetFinds() {
      tips.restart("found");
      paintMarks();
      return this.progress();
    },

    teardown() {
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("keydown", onKeydown, true);
      for (const mark of document.querySelectorAll(".qmark")) mark.remove();
      card.remove();
      ring.remove();
    },
  };
}
