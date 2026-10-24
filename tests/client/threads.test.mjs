/* Tests for web/js/threads.js — the per-pick context store.
 *
 * These exist because of a specific incident (2026-09-14). The chat view shared one
 * message array across every pick, so switching from a bridge identity to a plain
 * OpenRouter model posted the bridge conversation to OpenRouter, which read it as its
 * own history and answered as the previous session.
 *
 * `two picks never share an array` is the test that matters. Everything else here is
 * supporting cast.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createThreads, mergeStoreRead, threadKey } from "../../web/js/threads.js";

const IRIS = { id: "w-iris", name: "Iris" };
const JUNO = { id: "w-juno", name: "Juno" };
const STRANGER = { id: "w-stranger", name: "Kimi" };

describe("threadKey", () => {
  it("distinguishes two wefts", () => {
    assert.notEqual(threadKey(IRIS), threadKey(JUNO));
  });

  it("distinguishes two wefts on the same engine", () => {
    // The same model behind two wefts is two individuals, two threads.
    const a = { id: "one", provider_id: "p", model_id: "gpt-4o-mini" };
    const b = { id: "two", provider_id: "p", model_id: "gpt-4o-mini" };
    assert.notEqual(threadKey(a), threadKey(b));
  });

  it("does not move when the weft's engine does", () => {
    const before = { id: "w-iris", provider_id: "bridge", model_id: "bridge/iris" };
    const after = { id: "w-iris", provider_id: "other", model_id: "something-else" };
    assert.equal(threadKey(before), threadKey(after));
  });

  it("is stable for the same weft", () => {
    assert.equal(threadKey(IRIS), threadKey({ ...IRIS }));
  });

  it("is null when nothing is picked", () => {
    assert.equal(threadKey(null), null);
    assert.equal(threadKey(undefined), null);
    assert.equal(threadKey({}), null);
    assert.equal(threadKey({ provider_id: "x" }), null);
  });
});

describe("createThreads", () => {
  it("two picks never share an array", () => {
    // THE ONE. If this fails, one being's conversation is being sent to another.
    const threads = createThreads();

    threads.forPick(IRIS).push({ role: "user", content: "hello iris" });
    threads.forPick(JUNO).push({ role: "user", content: "hello juno" });

    assert.deepEqual(threads.forPick(STRANGER), []);
    assert.equal(threads.forPick(IRIS).length, 1);
    assert.equal(threads.forPick(JUNO).length, 1);
    assert.equal(threads.forPick(IRIS)[0].content, "hello iris");
  });

  it("returns the same array for the same pick, so a turn accumulates", () => {
    const threads = createThreads();
    threads.forPick(IRIS).push({ role: "user", content: "one" });
    threads.forPick({ ...IRIS }).push({ role: "assistant", content: "two" });

    assert.equal(threads.forPick(IRIS).length, 2);
  });

  it("hands an unpickable argument a throwaway, not a shared array", () => {
    const threads = createThreads();

    threads.forPick(null).push({ role: "user", content: "into the void" });

    assert.deepEqual(threads.forPick(null), []);
    assert.equal(threads.size, 0);
  });

  it("drops one thread without touching the others", () => {
    const threads = createThreads();
    threads.forPick(IRIS).push({ role: "user", content: "hi" });
    threads.forPick(JUNO).push({ role: "user", content: "hi" });

    threads.drop(IRIS);

    assert.deepEqual(threads.forPick(IRIS), []);
    assert.equal(threads.forPick(JUNO).length, 1);
  });

  it("clears everything on sign-out", () => {
    const threads = createThreads();
    threads.forPick(IRIS).push({ role: "user", content: "hi" });
    threads.forPick(STRANGER).push({ role: "user", content: "hi" });

    threads.clear();

    assert.equal(threads.size, 0);
    assert.deepEqual(threads.forPick(IRIS), []);
  });
});

/* Scrollback (2026-09-16) — the opt-in souvenir.
 *
 * The rule under all of these: Weft still holds no session. What a stored
 * thread is, is one device's copy of what it was shown. So the tests that matter are the
 * ones about what must NOT happen — nothing stored unless asked, and never one user's
 * conversation appearing under another's login on the same browser.
 */

/** A Web Storage-alike. Real localStorage is unavailable under node, and injecting one
 *  is also what lets the failure modes below be provoked on purpose. */
function fakeStorage(overrides = {}) {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    get size() {
      return map.size;
    },
    keys: () => [...map.keys()],
    ...overrides,
  };
}

const IRIS_KEPT = { ...IRIS, history: "local" };
const JUNO_KEPT = { ...JUNO, history: "local" };

describe("createThreads — scrollback", () => {
  it("stores nothing for a pick that did not ask", () => {
    // The default has to be the one that keeps nothing. IRIS carries no `history`.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: "u1" });

    threads.forPick(IRIS).push({ role: "user", content: "hello" });
    threads.save(IRIS);

    assert.equal(storage.size, 0);
  });

  it("stores nothing when there is no user to attribute it to", () => {
    // Storage belongs to a browser profile. Without a user id there is no namespace, and
    // an un-namespaced conversation is the exact thing this feature must never create.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: null });

    threads.forPick(IRIS_KEPT).push({ role: "user", content: "hello" });
    threads.save(IRIS_KEPT);

    assert.equal(storage.size, 0);
  });

  it("brings a kept thread back after a reload", () => {
    // THE ONE for this feature. A reload is a brand new module over the same storage.
    const storage = fakeStorage();

    const before = createThreads({ storage, userId: "u1" });
    before.forPick(IRIS_KEPT).push({ role: "user", content: "what were we saying" });
    before.forPick(IRIS_KEPT).push({ role: "assistant", content: "the donut sways" });
    before.save(IRIS_KEPT);

    const after = createThreads({ storage, userId: "u1" });

    assert.deepEqual(after.forPick(IRIS_KEPT), [
      { role: "user", content: "what were we saying" },
      { role: "assistant", content: "the donut sways" },
    ]);
  });

  it("leaves a memory-only thread empty after a reload", () => {
    const storage = fakeStorage();

    const before = createThreads({ storage, userId: "u1" });
    before.forPick(IRIS).push({ role: "user", content: "gone on refresh" });
    before.save(IRIS);

    const after = createThreads({ storage, userId: "u1" });

    assert.deepEqual(after.forPick(IRIS), []);
  });

  it("never shows one user's scrollback to another on the same browser", () => {
    // Two Weft logins, one browser profile. This is what the user id in the key is for,
    // and it is the reason signing out is allowed to leave the stored copy alone.
    const storage = fakeStorage();

    const mine = createThreads({ storage, userId: "dana" });
    mine.forPick(IRIS_KEPT).push({ role: "user", content: "private" });
    mine.save(IRIS_KEPT);

    const theirs = createThreads({ storage, userId: "someone-else" });

    assert.deepEqual(theirs.forPick(IRIS_KEPT), []);
  });

  it("keeps two kept picks apart in storage as well as in memory", () => {
    // The 2026-09-14 leak, one layer down: a shared storage key would reintroduce it on
    // the next reload even with the in-memory Map behaving perfectly.
    const storage = fakeStorage();

    const before = createThreads({ storage, userId: "u1" });
    before.forPick(IRIS_KEPT).push({ role: "user", content: "for iris" });
    before.forPick(JUNO_KEPT).push({ role: "user", content: "for juno" });
    before.save(IRIS_KEPT);
    before.save(JUNO_KEPT);

    const after = createThreads({ storage, userId: "u1" });

    assert.equal(after.forPick(IRIS_KEPT)[0].content, "for iris");
    assert.equal(after.forPick(JUNO_KEPT)[0].content, "for juno");
  });

  it("drops a blob written by a different version of Weft", () => {
    // Guessing at an unknown shape renders somebody's conversation wrong, or half of it,
    // months later, with nothing on screen to explain why. Dropping costs one souvenir.
    const storage = fakeStorage();
    storage.setItem(
      `weft.scrollback.u1.${threadKey(IRIS_KEPT)}`,
      JSON.stringify({ v: 99, messages: [{ role: "user", content: "from the future" }] }),
    );

    const threads = createThreads({ storage, userId: "u1" });

    assert.deepEqual(threads.forPick(IRIS_KEPT), []);
  });

  it("discards anything in a stored blob that is not a message", () => {
    // This came off disk, so it is input rather than state — and with `send: everything`
    // it goes on to be forwarded upstream as context.
    const storage = fakeStorage();
    storage.setItem(
      `weft.scrollback.u1.${threadKey(IRIS_KEPT)}`,
      JSON.stringify({
        v: 1,
        messages: [
          { role: "user", content: "real" },
          null,
          "just a string",
          { role: "user" },
          { content: "no role" },
          { role: 7, content: 8 },
        ],
      }),
    );

    const threads = createThreads({ storage, userId: "u1" });

    assert.deepEqual(threads.forPick(IRIS_KEPT), [{ role: "user", content: "real" }]);
  });

  it("survives storage that refuses to answer", () => {
    // A private window, blocked site data, or a full quota. Losing a souvenir is a
    // shrug; taking the chat window down with it is not.
    const boom = () => {
      throw new Error("site data blocked");
    };
    const threads = createThreads({
      storage: fakeStorage({ getItem: boom, setItem: boom, removeItem: boom }),
      userId: "u1",
    });

    assert.deepEqual(threads.forPick(IRIS_KEPT), []);
    threads.forPick(IRIS_KEPT).push({ role: "user", content: "still works" });
    threads.save(IRIS_KEPT);
    threads.drop(IRIS_KEPT);

    assert.deepEqual(threads.forPick(IRIS_KEPT), []);
  });

  it("signing out clears memory but leaves the souvenir on the device", () => {
    // Most of the reason anyone turns this on: log back in tomorrow and it is there.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(IRIS_KEPT).push({ role: "user", content: "see you tomorrow" });
    threads.save(IRIS_KEPT);

    threads.clear();

    assert.equal(threads.size, 0);
    const tomorrow = createThreads({ storage, userId: "u1" });
    assert.equal(tomorrow.forPick(IRIS_KEPT)[0].content, "see you tomorrow");
  });

  it("forget() erases the device copy without disturbing the conversation", () => {
    // Turning scrollback off mid-conversation: the window carries on, the copy goes.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(IRIS_KEPT).push({ role: "user", content: "mid-sentence" });
    threads.save(IRIS_KEPT);

    threads.forget(IRIS_KEPT);

    assert.equal(threads.forPick(IRIS_KEPT).length, 1);
    assert.equal(storage.size, 0);
  });

  it("drop() takes the stored copy with it", () => {
    // A pick that has left the menu should not come back on the next reload.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(IRIS_KEPT).push({ role: "user", content: "hi" });
    threads.save(IRIS_KEPT);

    threads.drop(IRIS_KEPT);

    assert.equal(storage.size, 0);
    assert.deepEqual(createThreads({ storage, userId: "u1" }).forPick(IRIS_KEPT), []);
  });

  it("only save() writes — accumulating a turn does not touch storage", () => {
    // Storage writes are synchronous on the main thread. If merely pushing a message
    // wrote, the canvas drawing the reply would stutter once per streamed token.
    const storage = fakeStorage();
    const threads = createThreads({ storage, userId: "u1" });

    threads.forPick(IRIS_KEPT).push({ role: "user", content: "one" });
    threads.forPick(IRIS_KEPT).push({ role: "assistant", content: "two" });

    assert.equal(storage.size, 0);
    threads.save(IRIS_KEPT);
    assert.equal(storage.size, 1);
  });
});

/* The window, and the quota guard (2026-09-23).
 *
 * Until this, a thread grew forever in both storage modes — not by decision, but because
 * nothing in the module could ever remove anything from one. These are the tests for the
 * two things that changed: the window has a size, and a full device no longer eats the
 * newest turn in silence.
 */

/** n complete turns, numbered so a trim can be proved to have kept the RIGHT end. */
function turns(n, from = 0) {
  const messages = [];
  for (let i = from; i < from + n; i += 1) {
    messages.push({ role: "user", content: `ask ${i}` });
    messages.push({ role: "assistant", content: `reply ${i}` });
  }
  return messages;
}

const SMALL_WINDOW = { ...IRIS, window_turns: 3 };
const SMALL_WINDOW_KEPT = { ...IRIS, history: "local", window_turns: 3 };

describe("createThreads — the window", () => {
  it("trims a thread to its window, keeping the newest turns", () => {
    const threads = createThreads();
    const messages = threads.forPick(SMALL_WINDOW);
    messages.push(...turns(10));

    threads.save(SMALL_WINDOW);

    assert.equal(messages.length, 6);
    assert.equal(messages[0].content, "ask 7");
    assert.equal(messages.at(-1).content, "reply 9");
  });

  it("trims the in-memory mode too, not just the stored one", () => {
    // The window is a contract about what a pick HOLDS. An unbounded in-memory thread is
    // the same bug as an unbounded stored one, only harder to notice.
    const threads = createThreads();
    assert.equal(SMALL_WINDOW.history, undefined);

    const messages = threads.forPick(SMALL_WINDOW);
    messages.push(...turns(50));
    threads.save(SMALL_WINDOW);

    assert.equal(messages.length, 6);
  });

  it("trims the array in place, so a turn in flight is not orphaned", () => {
    // The chat view holds this exact array across an await. Replacing it would leave the
    // streaming reply pushing into something nobody is reading.
    const threads = createThreads();
    const held = threads.forPick(SMALL_WINDOW);
    held.push(...turns(10));

    threads.save(SMALL_WINDOW);

    assert.equal(threads.forPick(SMALL_WINDOW), held);
  });

  it("never leaves a reply at the head of a thread", () => {
    // An exact message count lands mid-turn half the time, and a thread that opens on an
    // answer both reads wrong and gets SENT that way.
    const threads = createThreads();
    const messages = threads.forPick(SMALL_WINDOW);
    messages.push({ role: "assistant", content: "orphan" }, ...turns(10));

    threads.save(SMALL_WINDOW);

    assert.equal(messages[0].role, "user");
  });

  it("leaves a thread shorter than its window entirely alone", () => {
    const threads = createThreads();
    const messages = threads.forPick(SMALL_WINDOW);
    messages.push(...turns(2));

    threads.save(SMALL_WINDOW);

    assert.equal(messages.length, 4);
    assert.equal(messages[0].content, "ask 0");
  });

  it("applies a lowered window to what was already stored", () => {
    // Turn the dial down and it takes effect on the next hydration, rather than waiting
    // for the thread to happen to grow past the new number.
    const storage = fakeStorage();
    const big = { ...IRIS, history: "local", window_turns: 100 };
    const first = createThreads({ storage, userId: "u1" });
    first.forPick(big).push(...turns(20));
    first.save(big);

    const second = createThreads({ storage, userId: "u1" });
    const messages = second.forPick({ ...big, window_turns: 2 });

    assert.equal(messages.length, 4);
    assert.equal(messages.at(-1).content, "reply 19");
  });

  it("falls back to a bounded default rather than unbounded", () => {
    // A pick from a menu fetched before the dials existed carries no number. Whatever
    // that means, it must not mean "grow forever".
    const threads = createThreads();
    const messages = threads.forPick(IRIS);
    messages.push(...turns(500));

    threads.save(IRIS);

    assert.equal(messages.length, 400);
  });
});

describe("createThreads — the quota guard", () => {
  /** Storage that refuses anything over `limit` bytes, the way a full device does. */
  function fullStorage(limit) {
    const map = new Map();
    return {
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => {
        if (String(v).length > limit) {
          const err = new Error("quota");
          err.name = "QuotaExceededError";
          throw err;
        }
        map.set(k, String(v));
      },
      removeItem: (k) => map.delete(k),
      keys: () => [...map.keys()],
      get size() {
        return map.size;
      },
    };
  }

  it("keeps the newest turns when the device is full, instead of dropping them", () => {
    // THE ONE. The old behaviour was to swallow the error, which lost the turn that had
    // just happened — silently, and only discoverably on the reload that was the entire
    // point of keeping a scrollback.
    const storage = fullStorage(400);
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(SMALL_WINDOW_KEPT).push(...turns(3));

    threads.save(SMALL_WINDOW_KEPT);

    const stored = JSON.parse(storage.getItem(storage.keys()[0]));
    assert.ok(stored.messages.length > 0);
    assert.equal(stored.messages.at(-1).content, "reply 2");
  });

  it("does not shrink the conversation because storage would not take it", () => {
    // Storage is the souvenir; the array is the conversation still being had. Failing to
    // save one is not permission to start deleting the other.
    const storage = fullStorage(400);
    const threads = createThreads({ storage, userId: "u1" });
    const messages = threads.forPick(SMALL_WINDOW_KEPT);
    messages.push(...turns(3));

    threads.save(SMALL_WINDOW_KEPT);

    assert.equal(messages.length, 6);
  });

  it("gives up quietly on a device that refuses everything", () => {
    // A quota error on a single turn is not a quota problem, and retrying forever would
    // hang the main thread on the way out of a turn.
    const storage = fullStorage(0);
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(IRIS_KEPT).push(...turns(2));

    assert.doesNotThrow(() => threads.save(IRIS_KEPT));
    assert.equal(storage.size, 0);
  });

  it("does not retry a refusal that has nothing to do with space", () => {
    // A private window refuses the second attempt exactly as it refused the first.
    let calls = 0;
    const storage = fakeStorage({
      setItem: () => {
        calls += 1;
        throw new Error("site data blocked");
      },
    });
    const threads = createThreads({ storage, userId: "u1" });
    threads.forPick(IRIS_KEPT).push(...turns(5));

    threads.save(IRIS_KEPT);

    assert.equal(calls, 1);
  });
});

describe("mergeStoreRead", () => {
  const msg = (role, content, turn) => ({ role, content, attachments: [], spoken: false, turn });
  const exchange = (turn) => [msg("user", `q${turn}`, turn), msg("assistant", `a${turn}`, turn)];
  const contents = (entries) => entries.map((e) => e.message.content);

  it("takes a full read as it stands", () => {
    const held = mergeStoreRead(null, { full: true, messages: exchange(1) }, 10);
    assert.deepEqual(contents(held), ["q1", "a1"]);
  });

  it("adds a catch-up to what is held", () => {
    const held = mergeStoreRead(null, { full: true, messages: exchange(1) }, 10);
    const next = mergeStoreRead(held, { full: false, messages: exchange(2) }, 10);
    assert.deepEqual(contents(next), ["q1", "a1", "q2", "a2"]);
  });

  it("replaces what is held when the service had to read whole", () => {
    const held = mergeStoreRead(null, { full: true, messages: exchange(1) }, 10);
    const next = mergeStoreRead(held, { full: true, messages: exchange(7) }, 10);
    assert.deepEqual(contents(next), ["q7", "a7"]);
  });

  it("trims a catch-up to the window, as a full read would have", () => {
    const held = mergeStoreRead(null, { full: true, messages: [...exchange(1), ...exchange(2)] }, 2);
    const next = mergeStoreRead(held, { full: false, messages: exchange(3) }, 2);
    assert.deepEqual(contents(next), ["q2", "a2", "q3", "a3"]);
  });

  it("keeps the store's turn off the message, which is what goes upstream", () => {
    const [entry] = mergeStoreRead(null, { full: true, messages: [msg("user", "hi", 4)] }, 10);
    assert.equal(entry.turn, 4);
    assert.equal("turn" in entry.message, false);
  });

  it("an empty catch-up changes nothing", () => {
    const held = mergeStoreRead(null, { full: true, messages: exchange(1) }, 10);
    assert.deepEqual(mergeStoreRead(held, { full: false, messages: [] }, 10), held);
  });
});
