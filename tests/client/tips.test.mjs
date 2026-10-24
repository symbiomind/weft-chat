/* Tests for web/js/tips.js — which tip comes next.
 *
 * The run under test is a small copy of the shipped one: a welcome that waits for the
 * menu, setup tips that only apply to an account with no wefts, a tip after them that
 * applies to everyone, an advanced tip, and a hidden one shown by an event.
 *
 * `an account that is set up skips the setup tips` is the one that matters: without it,
 * everyone who already has a weft would be told they don't.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createTips } from "../../web/js/tips.js";

const tip = (id, page, extra = {}) => ({
  id, page, group: "core", order: 9, after: [], section: null, point_at: null,
  done_on: null, show_on: null, when: null, since: "1", title: id, html: "", ...extra,
});

const RUN = [
  tip("chat/welcome", "chat", { order: 1, when: "no-wefts", done_on: "open:drawer" }),
  tip("drawer/start", "drawer", { after: ["chat/welcome"], when: "no-wefts" }),
  tip("chat/pick", "chat", { order: 2, after: ["drawer/start"] }),
  tip("chat/shelf", "chat", { order: 3, after: ["chat/pick"], done_on: "shelf-open" }),
  tip("providers/sessions", "providers", { group: "advanced", section: "sessions" }),
  tip("drawer/egg", "drawer", { group: "secret", after: ["chat/shelf"], show_on: "wordmark-x3" }),
];

const NEW = { wefts: 0 };
const SET_UP = { wefts: 2 };

/** What the server would be told, in order. */
const sent = () => {
  const changes = [];
  return { changes, save: (change) => changes.push(change) };
};

const make = (progress = {}, save = undefined) => createTips({ tips: RUN, progress, save });

describe("tips", () => {
  it("are on until turned off", () => {
    const out = sent();
    const tips = make({}, out.save);
    assert.equal(tips.isOn(), true);
    tips.setOn(false);
    assert.equal(tips.isOn(), false);
    assert.deepEqual(out.changes, [{ on: false }]);
    assert.equal(make({ on: false }).isOn(), false);
  });

  it("start from the progress the server sent", () => {
    const tips = make({ on: true, done: [{ id: "chat/welcome", since: "1", at: "2026-10-16T12:00:00Z" }] });
    assert.equal(tips.isDone("chat/welcome"), true);
    assert.equal(tips.doneAt("chat/welcome"), Date.parse("2026-10-16T12:00:00Z"));
    assert.equal(tips.next("drawer", NEW).id, "drawer/start");
  });

  it("tell the server what was finished", () => {
    const out = sent();
    const tips = make({}, out.save);
    tips.finish("chat/welcome");
    tips.finish("chat/welcome");
    tips.event("open:drawer", NEW);
    assert.deepEqual(out.changes, [{ done: ["chat/welcome"] }]);
  });

  it("start a new account at the welcome", () => {
    assert.equal(make().next("chat", NEW).id, "chat/welcome");
  });

  it("hold a tip back until what it waits on is done", () => {
    const tips = make();
    assert.equal(tips.next("drawer", NEW), null);
    tips.finish("chat/welcome");
    assert.equal(tips.next("drawer", NEW).id, "drawer/start");
  });

  it("an account that is set up skips the setup tips", () => {
    const tips = make();
    // The welcome does not apply, and neither does what follows it, so the first
    // tip for everyone is next.
    assert.equal(tips.next("chat", SET_UP).id, "chat/pick");
    assert.equal(tips.next("drawer", SET_UP), null);
  });

  it("finish a tip by doing what it asks", () => {
    const tips = make();
    const { finished } = tips.event("open:drawer", NEW);
    assert.deepEqual(finished.map((t) => t.id), ["chat/welcome"]);
    assert.equal(tips.isDone("chat/welcome"), true);
  });

  it("do not finish a tip by an event before it is unlocked", () => {
    const tips = make();
    tips.event("shelf-open", NEW);
    assert.equal(tips.isDone("chat/shelf"), false);
  });

  it("never offer an advanced tip by itself", () => {
    assert.equal(make().next("providers", NEW), null);
    assert.equal(make().forSection("sessions").id, "providers/sessions");
  });

  it("show a hidden tip on its event, once unlocked", () => {
    const tips = make();
    assert.equal(tips.event("wordmark-x3", SET_UP).show, null);
    tips.finish("chat/pick");
    tips.finish("chat/shelf");
    assert.equal(tips.event("wordmark-x3", SET_UP).show.id, "drawer/egg");
    assert.equal(tips.next("drawer", SET_UP), null, "an event tip is not offered on a visit");
  });

  it("still show a hidden tip with tips off, and only that", () => {
    const tips = make({ on: false });
    tips.finish("chat/pick");
    tips.finish("chat/shelf");
    assert.equal(tips.next("chat", SET_UP), null, "help stays quiet");
    assert.equal(tips.event("wordmark-x3", SET_UP).show.id, "drawer/egg");
  });

  it("give a hidden tip no (?)", () => {
    const tips = createTips({ tips: [tip("x", "drawer", { group: "secret", section: "s" })] });
    assert.equal(tips.forSection("s"), null);
  });

  it("show a tip again when its since moves", () => {
    const done = [{ id: "chat/welcome", since: "1", at: "2026-10-16T12:00:00Z" }];
    const moved = RUN.map((t) => (t.id === "chat/welcome" ? { ...t, since: "2" } : t));
    const tips = createTips({ tips: moved, progress: { done } });
    assert.equal(tips.next("chat", NEW).id, "chat/welcome");
  });

  it("restart the help from the beginning, switched on, keeping what was found", () => {
    const out = sent();
    const tips = make({ on: false }, out.save);
    tips.finish("chat/welcome");
    tips.finish("drawer/egg");
    tips.restart("help");
    assert.equal(tips.isOn(), true);
    assert.equal(tips.next("chat", NEW).id, "chat/welcome");
    assert.equal(tips.isDone("drawer/egg"), true);
    assert.deepEqual(out.changes.at(-1), { restart: "help" });
  });

  it("forget what was found, and only that", () => {
    const tips = make();
    tips.finish("chat/welcome");
    tips.finish("drawer/egg");
    assert.deepEqual(tips.found().map((f) => [f.tip.id, f.at !== null]), [["drawer/egg", true]]);
    tips.restart("found");
    assert.equal(tips.isDone("drawer/egg"), false);
    assert.equal(tips.isDone("chat/welcome"), true);
  });

  it("count the core run in the order it unlocks", () => {
    const ids = (ctx) => make().core(ctx).map((t) => t.id);
    assert.deepEqual(ids(NEW), ["chat/welcome", "drawer/start", "chat/pick", "chat/shelf"]);
    assert.deepEqual(ids(SET_UP), ["chat/pick", "chat/shelf"]);
  });

  it("survive a save that throws", () => {
    const tips = make({}, () => { throw new Error("offline"); });
    tips.finish("chat/welcome");
    assert.equal(tips.isDone("chat/welcome"), true);
  });
});

describe("the tip after every core tip", () => {
  const RUN_END = [
    tip("chat/pick", "chat"),
    tip("providers/speech", "providers"),
    tip("chat/welcome", "chat", { when: "no-wefts" }),
    tip("drawer/all-done", "drawer", { group: "secret", when: "core-done" }),
  ];
  const make = () => createTips({ tips: RUN_END });

  it("waits for core tips on other screens too", () => {
    const tips = make();
    tips.finish("chat/pick");
    assert.equal(tips.next("drawer", SET_UP), null, "the Providers tip is still to come");
    tips.finish("providers/speech");
    assert.equal(tips.next("drawer", SET_UP).id, "drawer/all-done");
  });

  it("does not wait for setup tips that never applied", () => {
    const tips = make();
    tips.finish("chat/pick");
    tips.finish("providers/speech");
    assert.equal(tips.ready("drawer/all-done", SET_UP), true);
    assert.equal(tips.ready("drawer/all-done", NEW), false, "a new account still has its welcome");
  });
});
