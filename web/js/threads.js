/* Weft — per-thread context.
 *
 * A pick is an individual. Iris is not Juno, and neither of them is a stranger on
 * OpenRouter. This module is what makes that true in the wire traffic instead of only
 * in the user's head.
 *
 * WHY IT EXISTS — the bug it was written to fix, 2026-09-14:
 *
 * The chat view kept ONE array of messages for the whole window and never cleared it
 * when the pick changed. Because the OpenAI protocol is stateless, that array is sent
 * with every turn as context. So after talking to Iris and Juno on the bridge and
 * then switching to a plain model on OpenRouter, the next request carried the bridge
 * conversation to OpenRouter — which read it as its own history and answered in
 * character as the previous session.
 *
 * That is not a display quirk. It is **one provider's conversation being posted to a
 * different provider**, silently, as a side effect of using the picker exactly the way
 * it was designed to be used. A window that leaks what you said to one being into a
 * request to another is not a window.
 *
 * The fix is structural rather than a "clear it on switch" call somewhere in the view:
 * if context can only be reached through a pick, there is no array left for two picks
 * to share. Keying is on the weft, because two wefts are two different threads even
 * when the same engine answers both.
 *
 * SCROLLBACK, added 2026-09-16. A thread can now be asked to survive a reload.
 *
 * This is not Weft growing a memory. The service still stores
 * nothing, and the session still lives upstream in the gateway. What a stored thread
 * is, precisely, is **a souvenir on one device** — it is why you can refresh the page
 * and still see what you were just told, and why your phone will never show you what
 * you said on your desktop. That drift is the design, not a gap in it: ask the being
 * themselves what you were talking about and they will tell you, because they are the
 * ones who actually remember.
 *
 * It is off unless a pick asks for it (`pick.history === "local"`), because the default
 * has to be the one that keeps nothing.
 *
 * The key carries the user id. Storage belongs to a browser profile, not to a login, so
 * without it a second Weft user on the same browser would open the window and find
 * somebody else's conversation waiting in it. That is the whole reason signing out does
 * NOT wipe what is stored: the namespace is what keeps users apart, so logging back in
 * can safely find your own scrollback where you left it.
 *
 * Storage is INJECTED rather than reached for. This module has no DOM and no globals,
 * which is what lets its logic be tested under node — see tests/client/threads.test.mjs.
 */

/** Bump when the stored shape changes. A blob from an older Weft is dropped, not guessed at. */
const SCROLLBACK_VERSION = 1;

/* THE WINDOW HAS A SIZE NOW (2026-09-23).
 *
 * Before this, a thread grew forever. Not by decision — there was simply no code that
 * could ever remove anything from one, in either storage mode, which is the sort of bug
 * that hides because it only shows up in the sessions you care about most.
 *
 * `window_turns` is what the window HOLDS. Past it, the oldest turns are gone, and gone
 * means gone — this is the one cap that makes the browser's appetite finite, and it is
 * what gives the quota guard below something bounded to write. It applies in BOTH
 * storage modes: in-memory and localStorage are two backends for one contract, not two
 * different promises.
 *
 * It arrives on the pick, from the server, like every other per-pick setting. The
 * fallback exists for the same reason `persists()` reads defensively: a pick can reach
 * this module from a menu that was fetched before the dials existed, and a missing
 * number must never mean "unbounded".
 */
const DEFAULT_WINDOW_TURNS = 200;

/** A turn is a message and its reply. Everything here counts in messages; the dial
 *  counts in turns, and this is the only place that conversion lives. */
const MESSAGES_PER_TURN = 2;

/* HOW MUCH ONE THREAD MAY OCCUPY ON THE DEVICE.
 *
 * localStorage is roughly 5MB PER ORIGIN, shared by every pick — so no per-thread number
 * can be safe on its own, and this budget is a good-manners measure rather than a
 * guarantee. It is the cheap half of the quota guard: trimming to a byte budget before
 * writing means the common case never has to throw at all. The expensive half — catching
 * QuotaExceededError and retrying smaller — is still required, because the thread that
 * finally fills the origin may be a well-behaved one that simply went last.
 */
const MAX_BLOB_BYTES = 512 * 1024;

/**
 * The identity of one thread, or null for "nothing picked" — which is not a thread and
 * must never be given an array to accumulate into.
 *
 * The weft's own id. It used to be built from the provider and the
 * model, which meant a weft moved to a new engine lost its thread on this device; a
 * weft's id never changes, whatever answers it.
 */
export function threadKey(pick) {
  return pick?.id ? `w:${pick.id}` : null;
}

/** Whether this pick asked to be remembered on this device. */
function persists(pick) {
  return Boolean(pick) && pick.history === "local";
}

/** How many turns this pick's window holds. Never unbounded, never zero. */
function windowOf(pick) {
  const turns = Number(pick?.window_turns);
  return Number.isFinite(turns) && turns >= 1 ? Math.floor(turns) : DEFAULT_WINDOW_TURNS;
}

/**
 * Cut a thread down to the window, IN PLACE, and never leave a reply at the head.
 *
 * In place because the array identity is the contract: the chat view holds the same
 * array this module handed it, and a turn in flight is pushing into it. Returning a new
 * one would silently orphan the live conversation halfway through a reply.
 *
 * The orphan rule is the subtle half. Cutting to an exact message count lands on an
 * assistant message half the time, and a thread that opens mid-reply reads as though the
 * model spoke first — which it then also *sends* upstream that way, to providers that
 * have opinions about a conversation beginning with an answer. Dropping one more message
 * costs half a turn and keeps every thread starting where a person did.
 */
function trimToWindow(messages, turns) {
  let excess = messages.length - turns * MESSAGES_PER_TURN;
  if (excess > 0 && messages[excess]?.role !== "user") excess += 1;
  if (excess > 0) messages.splice(0, excess);
  return messages;
}

/**
 * A store-kept thread after one read of its store, as `[{turn, message}]`.
 *
 * `held` is what the last read left (null for none); `read` is /api/session/thread's
 * answer. A full read replaces what is held. A catch-up (`full: false`) carries only
 * what came after the window's last read, so it is added to what is held and the
 * result trimmed to the newest `turns` exchanges, which is what a full read of the same
 * store would have returned. Messages without a turn are kept: there is nothing to
 * trim them by.
 *
 * The turn travels beside the message rather than on it, because the message is what
 * gets sent upstream and a chat provider has no business with the store's numbering.
 */
export function mergeStoreRead(held, read, turns) {
  const incoming = read.messages.map(({ turn, ...message }) => ({ turn: turn ?? null, message }));
  if (read.full !== false || !held) return incoming;

  const merged = [...held, ...incoming];
  const numbered = merged.filter((entry) => entry.turn !== null).map((entry) => entry.turn);
  if (numbered.length === 0) return merged;
  const floor = Math.max(...numbered) - windowOf({ window_turns: turns }) + 1;
  return merged.filter((entry) => entry.turn === null || entry.turn >= floor);
}

/** Whether a failed write failed because the device is full, rather than because this
 *  is a private window or site data is blocked. Only the first is worth retrying, and
 *  the browsers disagree about how to say it. */
function isQuotaError(err) {
  if (!err || typeof err !== "object") return false;
  return (
    err.name === "QuotaExceededError"
    || err.name === "NS_ERROR_DOM_QUOTA_REACHED"
    || err.code === 22
    || err.code === 1014
  );
}

/**
 * @param {object} [options]
 * @param {Storage|null} [options.storage] A Web Storage-alike, or null for memory only.
 * @param {string|null} [options.userId] Whose scrollback this is. Required to store any.
 */
export function createThreads({ storage = null, userId = null } = {}) {
  /** @type {Map<string, {role: string, content: string}[]>} */
  const byKey = new Map();

  /** Storage without a user to attribute it to would be storage nobody can keep apart. */
  const canStore = Boolean(storage && userId);

  function storageKey(pick) {
    const key = threadKey(pick);
    return key === null ? null : `weft.scrollback.${userId}.${key}`;
  }

  /**
   * Every storage call goes through here.
   *
   * Reading and writing both throw for reasons that have nothing to do with us — a
   * private window, blocked site data, a full quota. Losing a souvenir is a shrug;
   * taking the chat window down with it is not.
   */
  function attempt(work, fallback = null) {
    try {
      return work();
    } catch {
      return fallback;
    }
  }

  function read(pick) {
    const name = storageKey(pick);
    if (!name) return null;

    const raw = attempt(() => storage.getItem(name));
    if (!raw) return null;

    const blob = attempt(() => JSON.parse(raw));
    // An unrecognised version is a shape this code does not know how to read. Dropping
    // it costs one scrollback; guessing at it would render somebody's conversation
    // wrong, or half of it, with no clue as to why.
    if (!blob || blob.v !== SCROLLBACK_VERSION || !Array.isArray(blob.messages)) return null;

    // Anything that is not a message-shaped object is discarded rather than trusted.
    // This came off disk, so it is input, not state — and it goes on to be forwarded
    // upstream as context on the next turn, within whatever `send_turns` allows.
    return blob.messages.filter(
      (m) => m && typeof m.role === "string" && typeof m.content === "string",
    );
  }

  function serialize(messages) {
    return JSON.stringify({ v: SCROLLBACK_VERSION, messages });
  }

  /**
   * Commit a thread to the device, in as much of it as will fit.
   *
   * THE FAILURE THIS EXISTS TO PREVENT. localStorage throws
   * QuotaExceededError *mid-save*, and the old code routed that through `attempt()`
   * alongside "this is a private window" — so a full device meant the newest turn was
   * silently dropped while the window carried on looking saved. That is the worst
   * failure mode available here: it is invisible, it loses the most recent thing you
   * said rather than the oldest, and you only find out on the reload that was the whole
   * reason for turning scrollback on.
   *
   * So: trim to a byte budget first, and if the write still fails for quota, drop the
   * oldest quarter and try again. A souvenir that is missing its beginning is worth
   * having; one that is missing its end is a lie about where you got to.
   *
   * WHAT THIS MUST NEVER DO is touch `messages`. It works on a copy throughout. Being
   * unable to save a conversation is not a reason to start deleting one — storage is the
   * souvenir, and the array is the conversation the user is still having.
   */
  function write(pick, messages) {
    const name = storageKey(pick);
    if (!name) return;

    let keep = messages.slice();

    // The cheap half: stay under budget without needing an exception at all.
    while (keep.length > MESSAGES_PER_TURN && serialize(keep).length > MAX_BLOB_BYTES) {
      keep = keep.slice(oldestQuarter(keep));
    }

    // The expensive half: the origin is full, which no per-thread budget can predict.
    for (let tries = 0; tries < 4; tries += 1) {
      try {
        storage.setItem(name, serialize(keep));
        return;
      } catch (err) {
        // Anything that is not the device being full is not ours to retry — a private
        // window will refuse the second attempt exactly as it refused the first.
        if (!isQuotaError(err) || keep.length <= MESSAGES_PER_TURN) return;
        keep = keep.slice(oldestQuarter(keep));
      }
    }
  }

  /** How many messages to shed on one pass. A quarter converges in a few tries without
   *  throwing away most of a thread to make room for one turn. */
  function oldestQuarter(messages) {
    const drop = Math.max(MESSAGES_PER_TURN, Math.ceil(messages.length / 4));
    // Land on a user message, for the same reason trimToWindow does.
    return messages[drop]?.role === "user" ? drop : drop + 1;
  }

  function erase(pick) {
    const name = storageKey(pick);
    if (!name) return;
    attempt(() => storage.removeItem(name));
  }

  return {
    /**
     * The message list for one pick, created empty on first use — or restored from this
     * device, if the pick asked for that.
     *
     * Returns a NEW empty array for an unpickable argument rather than null, so a
     * caller that forgets to check cannot accidentally write into a shared one — the
     * throwaway is discarded and nothing reaches anywhere it should not.
     */
    forPick(pick) {
      const key = threadKey(pick);
      if (key === null) return [];

      let messages = byKey.get(key);
      if (!messages) {
        // Hydration happens ONCE, on first touch. After that the in-memory array is the
        // live one and storage is only ever written to — re-reading later would let a
        // second tab's copy overwrite this one's turn mid-conversation.
        messages = (canStore && persists(pick) ? read(pick) : null) || [];
        // A blob written while the window was larger is still somebody's conversation,
        // but it is no longer the window they asked for. Trimming on the way in means
        // the dial takes effect the moment it is lowered, rather than the next time the
        // thread happens to grow past it.
        trimToWindow(messages, windowOf(pick));
        byKey.set(key, messages);
      }
      return messages;
    },

    /**
     * End of turn: trim the thread to its window, and commit it if it persists.
     *
     * Called when a turn is finished, never per streamed token: storage writes are
     * synchronous on the main thread, and doing one per frame would stutter the canvas
     * that is drawing the reply as it arrives. That cadence is also why this is the
     * right place to trim — a turn is the unit the window counts in, so a thread is only
     * ever measured at a point where it is a whole number of them.
     */
    save(pick) {
      const key = threadKey(pick);
      if (key === null) return;
      const messages = byKey.get(key);
      if (!messages) return;

      // TRIMMING COMES FIRST, AND HAPPENS IN BOTH MODES. The window is a contract about
      // what this pick holds, not a feature of the localStorage backend — an in-memory
      // thread left to grow forever is the same unbounded array, just one that nobody
      // can see the size of. Outside the storage guard below for exactly that reason.
      trimToWindow(messages, windowOf(pick));

      if (!canStore || !persists(pick)) return;
      write(pick, messages);
    },

    /** Forget one thread — used when a pick disappears from the menu. */
    drop(pick) {
      const key = threadKey(pick);
      if (key === null) return;
      byKey.delete(key);
      if (canStore) erase(pick);
    },

    /**
     * Forget what is stored for one pick, without touching the live conversation.
     *
     * This is what turning scrollback off means: the window carries on exactly as it
     * was, and the copy on this device goes. Erasing regardless of the pick's current
     * setting is the point — by the time this is called, the setting already says "no".
     */
    forget(pick) {
      if (canStore) erase(pick);
    },

    /**
     * Forget everything held in memory. The view's teardown calls this, so signing out
     * leaves no thread behind for whoever logs in next.
     *
     * Deliberately does NOT touch storage. A stored scrollback is namespaced to its
     * owner and is meant to outlive a sign-out — that is most of why anyone would turn
     * it on. Use drop() or forget() to actually erase one.
     */
    clear() {
      byKey.clear();
    },

    /** How many threads are being held. For tests, and for anything that wants a count. */
    get size() {
      return byKey.size;
    },
  };
}
