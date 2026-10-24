/* Weft — which tip comes next.
 *
 * The tips themselves are files on the server (app/tips.py). This module only decides,
 * and it is pure: no DOM and no network, progress handed in and changes handed out, so
 * tests/client/tips.test.mjs can drive it under node. The card that shows a tip is
 * tutor.js.
 *
 * THE ONE RULE. A tip is ready when:
 *
 *   - it applies to this account (`when:`; an account that already has wefts skips the
 *     setup tips),
 *   - every tip in its `after:` list is done, or does not apply,
 *   - and it is not done itself.
 *
 * A ready tip shows by itself when the user arrives on its page, unless it is
 * `advanced` (those wait behind their (?)) or waits for an event instead (`show_on:`).
 * Unlocks, a run of tips across screens, and hidden tips all fall out of that.
 *
 * DONE is kept with the account, against id + since: a tip whose `since`
 * moves shows again, which is how a changed feature gets announced to people who
 * already finished its tip. Finishing is Got it, or doing what the tip asks
 * (`done_on:`). This module keeps its own copy and hands every change to `save`, which
 * tutor.js sends to the server; a change that fails to arrive lasts for this visit.
 *
 * ON BY DEFAULT. Every tip carries Turn off tips, and the drawer has the switch, so
 * someone who does not want them is one tap from never seeing another. Off is help
 * off: hidden tips still show on their trigger, because they are a reward, not help.
 */

/** @typedef {{id:string, page:string, group:string, order:number, after:string[],
 *   section:?string, point_at:?string, done_on:?string, show_on:?string, when:?string,
 *   since:string, hint:?string, title:string, html:string}} Tip */
/** @typedef {{wefts:number}} Context */

/** @typedef {{id:string, since:string, at:string}} Mark */
/** @typedef {{done?: string[], on?: boolean, restart?: "help"|"found"}} Change */

/**
 * @param {{tips?: Tip[], progress?: {on?: boolean, done?: Mark[]},
 *   save?: (change: Change) => void}} options
 */
export function createTips({ tips = [], progress = {}, save = () => {} } = {}) {
  let list = tips;
  let byId = new Map(list.map((t) => [t.id, t]));
  /** @type {{on: boolean, done: Object<string, number>}} */
  let state = read(progress);

  /** The server's progress, as this module keeps it: stamp -> when, in ms. */
  function read({ on = true, done = [] } = {}) {
    const marks = {};
    for (const mark of done) marks[`${mark.id}@${mark.since}`] = Date.parse(mark.at) || Date.now();
    return { on: on !== false, done: marks };
  }

  function tell(change) {
    try {
      save(change);
    } catch {
      // A save that throws is a save that failed: this visit keeps what it knows.
    }
  }

  const stamp = (tip) => `${tip.id}@${tip.since ?? ""}`;
  const isDone = (tip) => !!tip && Object.hasOwn(state.done, stamp(tip));

  /** @param {Tip} tip @param {Context} ctx */
  function applies(tip, ctx) {
    if (tip.when === "no-wefts") return ctx.wefts === 0;
    // Every core tip done, or not for this account. A core tip never carries this
    // condition itself, so asking cannot go round in a circle.
    if (tip.when === "core-done") {
      return list.every((t) => t.group !== "core" || t.when === "core-done" || satisfied(t.id, ctx));
    }
    return true;
  }

  /** Done, or not for this account: either way nothing should wait on it. */
  function satisfied(id, ctx) {
    const tip = byId.get(id);
    return !tip || isDone(tip) || !applies(tip, ctx);
  }

  function ready(tip, ctx) {
    return applies(tip, ctx) && !isDone(tip) && tip.after.every((id) => satisfied(id, ctx));
  }

  return {
    /** Replace the list, as the server sent it. */
    setList(tips) {
      list = tips;
      byId = new Map(list.map((t) => [t.id, t]));
    },
    /** Replace the progress, as the server sent it. */
    setProgress(progress) {
      state = read(progress);
    },
    get(id) {
      return byId.get(id) ?? null;
    },
    isOn: () => state.on,
    setOn(on) {
      state.on = !!on;
      tell({ on: state.on });
    },
    /**
     * Start again. `help` forgets every tip but the hidden ones and turns tips on;
     * `found` forgets only the hidden ones.
     */
    restart(what = "help") {
      const hidden = new Set(list.filter((t) => t.group === "secret").map((t) => t.id));
      for (const key of Object.keys(state.done)) {
        const id = key.slice(0, key.lastIndexOf("@"));
        if (hidden.has(id) === (what === "found")) delete state.done[key];
      }
      if (what === "help") state.on = true;
      tell({ restart: what });
    },
    isDone: (id) => isDone(byId.get(id)),
    ready: (id, ctx) => {
      const tip = byId.get(id);
      return !!tip && ready(tip, ctx);
    },
    finish(id) {
      const tip = byId.get(id);
      if (!tip || isDone(tip)) return;
      state.done[stamp(tip)] = Date.now();
      tell({ done: [tip.id] });
    },
    /** When a tip was finished, in ms, or null. */
    doneAt: (id) => {
      const tip = byId.get(id);
      return tip && isDone(tip) ? state.done[stamp(tip)] : null;
    },

    /**
     * The tip this page offers by itself, or null. With tips off, only a hidden one:
     * off is help off, and a find is not help.
     */
    next(page, ctx) {
      return list
        .filter((t) => t.page === page && t.group !== "advanced" && !t.show_on && ready(t, ctx))
        .filter((t) => state.on || t.group === "secret")
        .sort((a, b) => a.order - b.order)[0] ?? null;
    },

    /**
     * Something happened. Finishes every ready tip waiting for it, and returns those,
     * plus the tip this event shows (`show_on:`), if one is ready.
     */
    event(name, ctx) {
      const finished = list.filter((t) => t.done_on === name && ready(t, ctx));
      for (const tip of finished) state.done[stamp(tip)] = Date.now();
      if (finished.length) tell({ done: finished.map((t) => t.id) });
      const show = list.find((t) => t.show_on === name && ready(t, ctx)
        && (state.on || t.group === "secret")) ?? null;
      return { finished, show };
    },

    /** The hidden tips, in file order, each with when it was found (null for not yet). */
    found() {
      return list.filter((t) => t.group === "secret").map((tip) => ({
        tip, at: isDone(tip) ? state.done[stamp(tip)] : null,
      }));
    },

    /** The tip whose (?) sits on this section, or null. Hidden tips never get one. */
    forSection(section) {
      return list.find((t) => t.section === section && t.group !== "secret") ?? null;
    },

    /**
     * The core run as this account sees it (what applies, plus what was done), in the
     * order it unlocks: by how deep in the `after:` chain, then by `order`.
     */
    core(ctx) {
      const depth = new Map();
      const depthOf = (tip, seen = new Set()) => {
        if (depth.has(tip.id)) return depth.get(tip.id);
        if (seen.has(tip.id)) return 0; // a loop; the server's check reports it
        seen.add(tip.id);
        const before = tip.after.map((id) => byId.get(id)).filter(Boolean);
        const d = before.length ? 1 + Math.max(...before.map((t) => depthOf(t, seen))) : 0;
        depth.set(tip.id, d);
        return d;
      };
      return list
        .filter((t) => t.group === "core" && (applies(t, ctx) || isDone(t)))
        .sort((a, b) => depthOf(a) - depthOf(b) || a.order - b.order);
    },
  };
}
