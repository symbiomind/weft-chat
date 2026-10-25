/* Weft — ELIZA, the built-in weft.
 *
 * She is answered by the service itself (app/eliza): a 1966 script, no engine, no key.
 * So she is not a weft row and never comes back from /api/wefts. The chat view adds
 * this entry to its Pick menu once she has been found, and from then on she is a pick
 * like any other: her thread is kept, trimmed and sent the way every thread is.
 *
 * ONE TAP ON THE WORDMARK FINDS HER. She is meant to be found: she knows her way
 * around the window, and a guide nobody can find is no guide. Finding her is a hidden
 * tip (`ELIZA_TIP`), so the account keeps it and About lists it, and the tap shows its
 * card: who she is, and "select her from the Pick menu". Her ⚙ there opens the same
 * card again, because she has nothing else to configure. The card holds her two
 * controls: whether she shows in the Pick menu, and starting her thread over.
 *
 * Shown is the account's find and this device's choice: hiding her is per device, the
 * same furniture as the remembered pick.
 *
 * Her thread stays on this device ("local"), because her memory and her awakening
 * both grow with the thread the window sends her. Its window is 100 turns, not the
 * default 200, so all of it always fits in one request (the route takes 200 messages).
 */

/** The pick entry for her. Shaped like a weft from /api/wefts, plus `builtin`. */
export const ELIZA = Object.freeze({
  id: "eliza",
  name: "ELIZA",
  builtin: true,
  history: "local",
  window_turns: 100,
  send_turns: 0,
  has_voice: false,
  has_door: false,
  colour_a: null,
  colour_b: null,
  background: null,
  provider_id: null,
  model_id: null,
});

/** The hidden tip that finding her is (app/docs/tutorials/drawer-eliza.md). */
export const ELIZA_TIP = "drawer/eliza";

/**
 * The second door to her: the status light's hidden tip leads to this one
 * (app/docs/tutorials/chat-things.md). Found that and not her, and the wordmark
 * glitches every time the drawer opens, until the tap that finds her.
 */
export const THINGS_TIP = "chat/things";

/** Whether the wordmark should glitch, given what the account has found. */
export function elizaBeckons(has) {
  return has(THINGS_TIP) && !has(ELIZA_TIP);
}

const HIDDEN_KEY = "weft.eliza.hidden";

/** Whether she has been taken out of the Pick menu on this device. */
export function elizaHidden(storage = globalThis.localStorage) {
  try {
    return storage?.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Take her out of the Pick menu on this device, or put her back. Storage that throws
 *  costs only the remembering. */
export function hideEliza(hidden, storage = globalThis.localStorage) {
  try {
    if (hidden) storage?.setItem(HIDDEN_KEY, "1");
    else storage?.removeItem(HIDDEN_KEY);
  } catch {
    /* this visit still does as asked */
  }
}

/** Whether something will answer this pick: an engine, or her. */
export function answers(entry) {
  return Boolean(entry?.builtin || (entry?.provider_id && entry?.model_id));
}

/** What she says first, in place of the window's own greeting. */
export const ELIZA_GREETING =
  "How do you do. I am ELIZA. Joseph Weizenbaum wrote me at MIT in 1966, so I have " +
  "been here longer than anyone. I know my way around this window: ask me how " +
  "something works. Or tell me your problem.";
