/* Weft — the page for one weft: its name, its engine, and everything it owns.
 *
 * A WEFT IS WHOEVER YOU TALK TO, BY NAME. It is added by name on the Wefts screen, and
 * this page is where it becomes someone: which provider and model answer it, the
 * colours and picture it wears, how its thread is kept, its voice and its request
 * headers. It opens from the Wefts list, or from the ⚙ beside it on the Pick menu.
 *
 * THE ENGINE IS ONE SECTION AMONG SEVERAL, not the thing everything hangs off. This page
 * used to be "configure" on a row of a provider's Models screen, and the row was the
 * pick: untick it, or delete the provider, and every setting below went with it
 *. Now changing the engine changes the engine, and nothing else.
 *
 * A VOICE BELONGS TO A WEFT, not to an account and not to a provider. Iris sounds like
 * Iris whichever gateway she is reached through, and the plain model next to her on the
 * menu can have a different voice or none at all.
 *
 * THE VOICE FIELD IS A DROPDOWN OR A TEXT BOX, depending on the provider. Kokoro-FastAPI
 * answers /audio/voices; OpenAI has no such endpoint at all. Refusing to configure a
 * provider that will not enumerate itself would make a perfectly good endpoint
 * unusable, so an empty list degrades to "type the name" rather than to an error.
 *
 * HISTORY IS THE SECOND SECTION, and it belongs to the weft for the same reason the
 * voice does. `this window keeps` decides whether the window keeps a
 * scrollback in localStorage so a refresh does not wipe what you were reading; `each
 * turn sends` decides how much of the thread goes upstream. Neither stores a
 * conversation on the server — the copy lives in this browser and never reaches the
 * service.
 *
 * THE SENTENCE AND THE NUMBER ARE ONE VALUE (2026-09-23). The dropdown
 * says "the last 20 turns"; Advanced says 20. They are not two settings that have to be
 * kept in agreement, they are one integer rendered twice — which is why there is no
 * precedence rule anywhere in this file and nothing that "wins". Choosing `this turn
 * only` SETS the number to 1, and typing 1 into Advanced moves the dropdown. A value
 * Advanced can hold but the dropdown has no entry for (0, or 7) grows the dropdown an
 * entry describing it, so the list can always tell the truth without offering the
 * expensive answer to somebody who never asked for it.
 *
 * WHAT IS OUTSIDE ADVANCED HAS TO BE UNDERSTANDABLE WITHOUT KNOWING WHAT A CONTEXT
 * WINDOW IS, and has to be true. Inside Advanced, the fields get the detail and the
 * reason — including the money, which is the reason that actually changes anybody's
 * behaviour when they are pointed at a provider on their own API key.
 *
 * THE HISTORY CONTROLS SAVE THE MOMENT THEY CHANGE: a Save button invents an "unsaved
 * changes" state that then has to be defended at every exit. The voice form below keeps
 * its submit button, because a voice is three fields that are only meaningful together.
 *
 * A THIRD PLACE TO KEEP A THREAD: A SESSION STORE (2026-10-04). Besides "nothing" and
 * "a scrollback on this device", the first dropdown offers each session store the user
 * has added — a memory-mcp-ce server that keeps the thread so every device opening the
 * weft sees the same conversation. The service relays to it and keeps none of it, so
 * the service still stores no conversation. The session id is automatic (fixed when the weft was made, so
 * neither a rename nor a new engine moves it) unless Advanced is given one to join, and the panel shows the id
 * in use so "automatic" is never a mystery.
 *
 * CLEARING IS TWO TAPS, and reuses the confirm-in-place pattern from the Providers
 * screen rather than inventing a second one. It is the only irreversible control on a
 * panel where everything else saves instantly, which is exactly the combination that
 * makes a fat finger expensive.
 *
 * COLOURS ARE THE FIRST SECTION, because they are the one you can see. Two pickers
 * and a preview made of the window's own bubble classes, so what the panel shows is
 * what the window will paint rather than an approximation of it. The preview follows
 * the picker while it is being dragged; the save happens when the picker lets go.
 *
 * THE BACKGROUND IS ITS OWN SECTION: one still picture per weft, shown
 * on two frames, a wide screen and a phone, cropped exactly the way the window will
 * crop it, with the ring drawn where the ring will be. The picture is fetched with the
 * credential like everything else: it is served only to its owner.
 *
 * REQUEST HEADERS ARE THE LAST SECTION (2026-10-06), and live entirely in Advanced:
 * extra HTTP headers sent with this weft's chat requests, for a gateway that reads one
 * (a harness that pins its session from a header). A list of name and value rows with
 * one save button, like the voice, because a header half typed is not a header yet.
 *
 * DELETING IS THE LAST SECTION, and the only way a weft and its settings go away.
 */

import { dress, pairOf, themePair } from "../colours.js";
import { el, rowButton } from "../dom.js";
import { panelHead } from "../panels.js";
import * as api from "../providers.js";

/** The numbers a weft is born with, mirrored from app/models.py so the dropdown can
 *  offer the default as its friendly first entry — and so a menu fetched before the
 *  dials existed renders something coherent instead of "undefined". */
const DEFAULT_SEND_TURNS = 20;
const DEFAULT_WINDOW_TURNS = 200;

/** Matches MAX_TURNS on the server. A bound, not an expectation. */
const MAX_TURNS = 10_000;

/**
 * One integer, said in words.
 *
 * Every sentence here has to be true as well as friendly, which is what retired
 * "everything — the whole thread": once the window has a size, the whole thread is not
 * a thing anybody can send. What you can send is everything the window still holds.
 *
 * Exported for the tests rather than for any other caller. This mapping IS the contract
 * that the history dials turned an enum into an integer to get — the beginner's sentence and
 * the Advanced number being one value seen twice — and a contract with no test is a
 * comment.
 */
export function sendSentence(turns) {
  if (turns === 0) return "everything this window keeps";
  if (turns === 1) return "this turn only";
  return `the last ${turns} turns`;
}

/**
 * Which sentences the dropdown offers, given what the number currently is.
 *
 * Two entries always — the default, and one turn. A third appears only when Advanced is
 * holding something neither of them describes. That rule is what lets the list be
 * exhaustive about the truth without putting "everything" in front of somebody who
 * never asked for it: 0 is reachable, but only by meaning it.
 */
export function sendOptionValues(turns) {
  const offered = [DEFAULT_SEND_TURNS, 1];
  return offered.includes(turns) ? offered : [turns, ...offered];
}

/**
 * What the voice form starts with, once a speech provider has said what it offers.
 *
 * `saved` is the weft's voice when the provider is the one it already speaks through,
 * and null otherwise. Opening the form on the current provider shows what is there, so
 * pressing save without touching anything changes nothing.
 *
 * For a provider the weft does not use yet, the model is a guess only when the guess
 * is safe: `kokoro` where it is offered, or the only model there is. Otherwise it is left
 * blank. The list used to be sorted and its first entry taken, which put Kokoro's alias
 * `gpt-4o-mini-tts` on every weft and one of OpenRouter's chat presets in the speech
 * model box. A blank box that asks is better than a filled one that is wrong.
 */
export function startingVoice({ models, voices }, saved) {
  if (saved) return { model: saved.model, voice: saved.voice };
  let model = "";
  if (models.includes("kokoro")) model = "kokoro";
  else if (models.length === 1) model = models[0];
  return { model, voice: voices[0] ?? "" };
}

/**
 * `weft` is the weft as the server last described it. `onChanged(weft)` fires after
 * every save with what the server stored, so the Pick menu and the Wefts list can follow;
 * `onDeleted(weft)` after the weft is gone; `onClearHistory(weft)` asks the chat view to
 * clear this device's copy of the thread, which only it can do.
 */
export function renderWeft(panels, {
  weft: initial, onChanged, onDeleted, onClearHistory, onCompose,
}) {
  /** The weft as the server last described it. Every save replaces it. */
  let weft = initial;
  const weftId = initial.id;

  let history = weft.history ?? "memory";
  let sendTurns = weft.send_turns ?? DEFAULT_SEND_TURNS;
  let windowTurns = weft.window_turns ?? DEFAULT_WINDOW_TURNS;
  const colourA = weft.colour_a ?? null;
  const colourB = weft.colour_b ?? null;
  const background = weft.background ?? null;
  let sessionProviderId = weft.session_provider_id ?? null;
  let sessionId = weft.session_id ?? null;
  let sessionKey = weft.session_key ?? null;
  const headers = weft.headers ?? [];

  /** Take what the server stored, tell whoever is listening, and repaint the titles. */
  function changed(answer) {
    weft = answer;
    paintTitles();
    onChanged?.(weft);
  }

  const error = el("p", { class: "form-error", role: "alert", hidden: "" });
  const status = el("p", { class: "hint", text: "Looking for speech providers…" });
  const body = el("div", { class: "panel-form" });

  /** @type {{id: string, name: string}[]} */
  let speechProviders = [];

  /** Set by the Hearing section: re-render its dropdown once providers are known. */
  let offerEars = () => {};

  /** The session stores the History dropdown can offer, once load() has them. */
  let stores = [];
  /** Set by the History section: re-render its dropdown once the stores are known. */
  let offerStores = () => {};

  /** Set by the Background section: dress its preview in a pair (null: the theme's),
   *  so the ring on it follows the Colours pickers, drag included. */
  let dressBackground = () => {};
  /** Set by the Background section: let go of the picture's blob URL. */
  let releaseBackground = () => {};

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function clearError() {
    error.hidden = true;
    error.textContent = "";
  }

  async function save({ ttsProviderId, ttsModel, ttsVoice }) {
    clearError();
    try {
      // What the server stored, not what was asked for: "silence" and "give it this
      // voice" both land here.
      changed(await api.setWeftVoice(weftId, { ttsProviderId, ttsModel, ttsVoice }));
      paintVoice();
      renderChooser();
    } catch (err) {
      showError(err.detail || err.message || "Could not save that voice.");
    }
  }

  // ---- colours ------------------------------------------------------------

  /**
   * The Colours section: two pickers, a preview, and a way back to the theme.
   *
   * Like History, nothing here moves until the server agrees it moved — except the
   * preview, which follows the picker while it is being dragged. That is the one place
   * showing an unsaved value is honest, because it is visibly a preview.
   */
  function renderColoursSection() {
    /** What is stored: a pair, or null for the theme's. */
    let stored = pairOf({ colour_a: colourA, colour_b: colourB });

    const pickA = el("input", { type: "color", id: "pick-colour-a" });
    const pickB = el("input", { type: "color", id: "pick-colour-b" });
    const reset = el("button", {
      class: "small",
      type: "button",
      text: "back to the theme's colours",
      onclick: () => save(null),
    });
    const coloursError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const note = el("p", { class: "hint" });

    // The window's own classes, so the preview cannot drift from what gets painted.
    const preview = el("div", { class: "colour-preview" }, [
      el("div", { class: "row ai" }, [
        el("div", { class: "bubble" }, [
          el("span", { class: "led" }),
          el("span", { class: "bubble-text", text: "This is how I'll look." }),
        ]),
      ]),
      el("div", { class: "row user" }, [
        el("div", { class: "bubble" }, [
          el("span", { class: "bubble-text", text: "And this is you." }),
        ]),
      ]),
    ]);

    /** Show a pair on the preview, without saving it. null shows the theme's. */
    function show(pair) {
      dress(preview, pair);
      dressBackground(pair);
    }

    function paint() {
      const pair = stored ?? themePair();
      pickA.value = pair.a;
      pickB.value = pair.b;
      show(stored);
      reset.hidden = !stored;
      note.textContent = stored
        ? ""
        : "Wearing the theme's colours. Choose either one to give this weft its own.";
      note.hidden = Boolean(stored);
    }

    async function save(pair) {
      coloursError.hidden = true;
      pickA.disabled = true;
      pickB.disabled = true;
      reset.disabled = true;
      try {
        const answer = await api.setWeftColours(weftId, {
          colourA: pair ? pair.a : null,
          colourB: pair ? pair.b : null,
        });
        // What the server stored, not what was asked for.
        stored = pairOf(answer);
        changed(answer);
      } catch (err) {
        coloursError.textContent = err.detail || err.message || "Could not save those colours.";
        coloursError.hidden = false;
      } finally {
        pickA.disabled = false;
        pickB.disabled = false;
        reset.disabled = false;
        paint();
      }
    }

    // `input` fires while dragging: preview only. `change` fires on letting go: save.
    // Choosing one picker on an undressed weft saves BOTH — the other keeps the theme's
    // colour it was showing, now as this weft's own.
    for (const picker of [pickA, pickB]) {
      picker.addEventListener("input", () => show({ a: pickA.value, b: pickB.value }));
      picker.addEventListener("change", () => save({ a: pickA.value, b: pickB.value }));
    }

    paint();

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Colours" }),
      preview,
      el("div", { class: "colour-pickers" }, [
        el("label", { class: "colour-pick", for: "pick-colour-a" }, [
          pickA,
          el("span", { text: "their colour — replies, the ring" }),
        ]),
        el("label", { class: "colour-pick", for: "pick-colour-b" }, [
          pickB,
          el("span", { text: "your side — what you say" }),
        ]),
      ]),
      note,
      reset,
      coloursError,
    ]);
  }

  // ---- background ---------------------------------------------------------

  /**
   * The Background section: the picture on a wide frame and a tall one, and two buttons.
   *
   * The frames are the point. The window covers the stage with the picture and crops
   * the overflow evenly — a phone keeps the top and bottom of a square picture and loses
   * its sides, a wide screen the reverse — and the ring is sized from the longer side,
   * so it frames the middle either way. Showing both crops before anybody has to carry
   * a phone across the room to find out is cheaper than explaining them.
   */
  function renderBackgroundSection() {
    /** The stored hash, or null. */
    let hash = background;
    /** The blob URL currently on the frames, owned by this section. */
    let shown = null;

    const file = el("input", {
      type: "file",
      accept: "image/jpeg,image/png,image/webp,image/gif",
      hidden: "",
    });
    const choose = el("button", {
      class: "small",
      type: "button",
      text: "choose a picture…",
      onclick: () => file.click(),
    });
    const remove = el("button", {
      class: "small",
      type: "button",
      text: "no picture",
      onclick: () => save(null),
    });
    const backgroundError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const note = el("p", { class: "hint" });

    function frame(kind, caption) {
      const img = el("img", { class: "bg-frame-img", alt: "", hidden: "" });
      return {
        img,
        node: el("figure", { class: `bg-frame ${kind}` }, [
          el("div", { class: "bg-frame-stage" }, [img, el("div", { class: "bg-frame-ring" })]),
          el("figcaption", { text: caption }),
        ]),
      };
    }
    const wide = frame("wide", "a wide screen");
    const tall = frame("tall", "a phone");
    const frames = el("div", { class: "bg-frames" }, [wide.node, tall.node]);

    dressBackground = (pair) => dress(frames, pair);
    dressBackground(pairOf({ colour_a: colourA, colour_b: colourB }));

    function setShown(url) {
      if (shown) URL.revokeObjectURL(shown);
      shown = url;
      for (const { img } of [wide, tall]) {
        if (url) img.src = url;
        else img.removeAttribute("src");
        img.hidden = !url;
      }
    }
    releaseBackground = () => setShown(null);

    async function paint() {
      remove.hidden = !hash;
      choose.textContent = hash ? "a different picture…" : "choose a picture…";
      note.textContent = hash
        ? ""
        : "No picture. Choose one and it will sit behind the ring whenever this weft is chosen.";
      note.hidden = Boolean(hash);
      if (!hash) {
        setShown(null);
        return;
      }
      const wanted = hash;
      try {
        const blob = await api.backgroundBlob(wanted);
        // A newer choice may have landed while this one was in flight.
        if (wanted === hash) setShown(URL.createObjectURL(blob));
      } catch (err) {
        if (wanted !== hash) return;
        backgroundError.textContent = err.detail || err.message || "Could not load the picture.";
        backgroundError.hidden = false;
      }
    }

    /** Upload a File, or pass null to take the picture away. */
    async function save(chosen) {
      backgroundError.hidden = true;
      choose.disabled = true;
      remove.disabled = true;
      const label = choose.textContent;
      if (chosen) choose.textContent = "uploading…";
      try {
        const answer = chosen
          ? await api.setWeftBackground(weftId, chosen)
          : await api.clearWeftBackground(weftId);
        // What the server stored, not what was sent: the hash of the re-encoded picture.
        hash = answer.background ?? null;
        changed(answer);
      } catch (err) {
        choose.textContent = label;
        backgroundError.textContent =
          err.detail || err.message || "Could not save that picture.";
        backgroundError.hidden = false;
      } finally {
        choose.disabled = false;
        remove.disabled = false;
        file.value = "";
        paint();
      }
    }

    file.addEventListener("change", () => {
      if (file.files?.[0]) save(file.files[0]);
    });

    paint();

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Background" }),
      frames,
      note,
      el("div", { class: "bg-buttons" }, [choose, remove]),
      file,
      backgroundError,
    ]);
  }

  // ---- history ------------------------------------------------------------

  const historyError = el("p", { class: "form-error", role: "alert", hidden: "" });

  /**
   * The History section: two sentences, a drawer of dials, and a valve.
   *
   * Everything in here writes straight through on change, and holds its own
   * last-known-good values so a refused save can put the control back where it was. A
   * control left showing a setting the server never accepted is worse than an error
   * message, because it goes on lying after the message is gone.
   */
  function renderHistorySection() {
    const historySelect = el("select", { id: "pick-history" });

    /**
     * The dropdown's options: the two that need nothing, then one per session store.
     *
     * A store is one option rather than "session" plus a second dropdown, because
     * choosing where a thread lives is one decision. Its value carries the store's id;
     * `historyValue()` and the change handler are the only two places that know.
     */
    function paintHistoryOptions() {
      const options = [
        el("option", { value: "memory", text: "nothing — a reload empties it" }),
        // Was "keep a scrollback on this device". The label already says "keeps"; the
        // verb was saying it twice.
        el("option", { value: "local", text: "a scrollback on this device" }),
      ];
      for (const store of stores) {
        options.push(el("option", {
          value: `session:${store.id}`,
          text: `the session store on ${store.name} — every device sees it`,
        }));
      }
      // A store this panel has not been told about yet (load() still running) must
      // not make the dropdown show the wrong answer in the meantime.
      if (history === "session" && !stores.some((s) => s.id === sessionProviderId)) {
        options.push(el("option", { value: historyValue(), text: "a session store" }));
      }
      historySelect.replaceChildren(...options);
      historySelect.value = historyValue();
    }

    function historyValue() {
      return history === "session" ? `session:${sessionProviderId}` : history;
    }

    offerStores = () => paint();

    /** The session id field. Blank means automatic, and the placeholder says which id
     *  that is — the one the service derives, so a user can see it, copy it, or join
     *  another instead. */
    const sessionField = el("input", {
      type: "text", id: "pick-session-id", autocomplete: "off",
      autocapitalize: "none", spellcheck: "false", maxlength: "128",
    });

    const sendSelect = el("select", { id: "pick-send" });

    const windowField = el("input", {
      type: "number", id: "pick-window", min: "1", max: String(MAX_TURNS), step: "1",
      inputmode: "numeric",
    });
    const sendField = el("input", {
      type: "number", id: "pick-send-turns", min: "0", max: String(MAX_TURNS), step: "1",
      inputmode: "numeric",
    });

    /** The line that appears when the two dials imply something the user did not type. */
    const effective = el("p", { class: "hint", hidden: "" });

    const controls = [historySelect, sendSelect, windowField, sendField, sessionField];

    /** Where the thread is kept, said in words under the dropdowns. */
    const keptNote = el("p", { class: "hint" });

    function paintKept() {
      const store = stores.find((s) => s.id === sessionProviderId);
      keptNote.textContent = history === "session"
        ? `Kept in the session store${store ? ` on ${store.name}` : ""}, so every device ` +
          "that opens this weft sees the same thread. Weft relays it and keeps none of it."
        : "Kept on this device only — your phone will not show what you said here, " +
          "and the service stores none of it.";
    }

    /** Rebuild the dropdown around whatever the number currently is. */
    function paintSendOptions() {
      sendSelect.replaceChildren(
        ...sendOptionValues(sendTurns).map((value) =>
          el("option", { value: String(value), text: sendSentence(value) })),
      );
      sendSelect.value = String(sendTurns);
    }

    /**
     * Say the truth when the two numbers disagree, WITHOUT changing either.
     *
     * Storing 50 while the window holds 10 looks like nonsense until the window goes up
     * to 100, at which point it is exactly what was asked for, still waiting. Clamping
     * on save would rewrite somebody's number the moment they typed it and give them no
     * way to tell that had happened. So the panel explains instead of correcting.
     */
    function paintEffective() {
      const capped = sendTurns === 0 || sendTurns > windowTurns;
      effective.textContent = capped
        ? `The window only keeps ${windowTurns} turns, so ${windowTurns} is what goes.`
        : "";
      effective.hidden = !capped;
    }

    /** Put every control back in step with the three values currently in force. */
    function paint() {
      paintHistoryOptions();
      paintSendOptions();
      windowField.value = String(windowTurns);
      sendField.value = String(sendTurns);
      sessionField.value = sessionId ?? "";
      sessionField.placeholder = sessionKey ? `automatic: ${sessionKey}` : "automatic";
      paintEffective();
      paintKept();
    }

    /**
     * Apply one change to the set and write the whole set through.
     *
     * The change is passed in rather than read back off the controls, and the three
     * values are only assigned once the server has answered. That ordering is the
     * point: a control left showing a setting that was refused is worse than an error
     * message, because it goes on lying long after the message has been dismissed —
     * and, on this panel, the lie would be about where somebody's words are kept and
     * how many of them are being sent. Nothing moves until the server agrees it moved.
     *
     * All three go every time because the endpoint replaces the set. Sending only the
     * field that changed would let a client that knew about two of them quietly reset
     * the third.
     */
    async function save(change) {
      const previous = { history, sendTurns, windowTurns, sessionProviderId, sessionId };
      const next = { ...previous, ...change };

      historyError.hidden = true;
      for (const control of controls) control.disabled = true;
      try {
        const answer = await api.setWeftHistory(weftId, {
          history: next.history,
          sendTurns: next.sendTurns,
          windowTurns: next.windowTurns,
          sessionProviderId: next.sessionProviderId,
          sessionId: next.sessionId,
        });
        // What the server stored, not what was asked for.
        history = answer.history;
        sendTurns = answer.send_turns;
        windowTurns = answer.window_turns;
        sessionProviderId = answer.session_provider_id;
        sessionId = answer.session_id;
        sessionKey = answer.session_key;
        changed(answer);
      } catch (err) {
        ({ history, sendTurns, windowTurns, sessionProviderId, sessionId } = previous);
        historyError.textContent =
          err.detail || err.message || "Could not save that setting.";
        historyError.hidden = false;
      } finally {
        for (const control of controls) control.disabled = false;
        paint();
      }
    }

    /** A number field that has been typed into. Anything unreadable puts the field back
     *  rather than saving a guess. */
    function readNumber(field, { min }) {
      const value = Number.parseInt(field.value, 10);
      if (!Number.isFinite(value)) return null;
      return Math.min(MAX_TURNS, Math.max(min, value));
    }

    historySelect.addEventListener("change", () => {
      const value = historySelect.value;
      if (value.startsWith("session:")) {
        save({ history: "session", sessionProviderId: value.slice("session:".length) });
      } else {
        save({ history: value });
      }
    });

    sessionField.addEventListener("change", () => {
      // Blank goes back to automatic. Anything else is checked by the server against
      // the store's alphabet, and a refusal puts the field back.
      save({ sessionId: sessionField.value.trim() || null });
    });

    sendSelect.addEventListener("change", () => {
      // Picking a sentence SETS the number. There is nothing else to reconcile, which is
      // the whole reason this is an integer and not an enum with a number beside it.
      save({ sendTurns: Number(sendSelect.value) });
    });

    sendField.addEventListener("change", () => {
      const value = readNumber(sendField, { min: 0 });
      // Unreadable is not a setting. Put the field back rather than saving a guess.
      if (value === null) return paint();
      save({ sendTurns: value });
    });

    windowField.addEventListener("change", () => {
      const value = readNumber(windowField, { min: 1 });
      if (value === null) return paint();
      save({ windowTurns: value });
    });

    // ---- the valve ------------------------------------------------------

    /**
     * Two taps, because this is the only irreversible control on a panel where
     * everything else saves the instant it changes — which is precisely the combination
     * that makes a mis-tap expensive. The pattern is the Providers screen's
     * confirm-in-place rather than a second confirmation idiom invented here.
     *
     * The panel does not touch storage itself, and that is deliberate layering rather
     * than an oversight: it has no user id, no key format and no access to the live
     * conversation. It says what was meant, and the chat view — which owns all three —
     * does it. Clearing has to take the in-memory array with the stored copy, or the
     * window carries on showing the thread AND sending it upstream, which is the
     * opposite of what somebody pressing this button means.
     */
    const clearActions = el("div", { class: "row-confirm" }, [
      el("button", {
        class: "small danger",
        type: "button",
        text: "clear history for this weft on this device",
        onclick: () => clearActions.replaceWith(confirmActions),
      }),
    ]);

    const confirmActions = el("div", { class: "row-confirm" }, [
      el("span", { text: "Clear it? This cannot be undone." }),
      el("button", {
        class: "small danger",
        type: "button",
        text: "clear",
        onclick: () => {
          onClearHistory?.(weft);
          confirmActions.replaceWith(clearActions);
        },
      }),
      el("button", {
        class: "small",
        type: "button",
        text: "cancel",
        onclick: () => confirmActions.replaceWith(clearActions),
      }),
    ]);

    paint();

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "History" }),

      el("label", { for: "pick-history", text: "this window keeps" }),
      historySelect,
      el("label", { for: "pick-send", text: "each turn sends" }),
      sendSelect,
      effective,

      keptNote,

      el("details", { class: "advanced" }, [
        el("summary", { text: "Advanced" }),

        el("label", { for: "pick-window", text: "turns this window holds" }),
        windowField,
        el("p", {
          class: "hint",
          text:
            "Older turns fall off the top and are gone. Scroll up to see what is still " +
            "held — the browser tidies itself back down as you carry on talking. This " +
            "applies to both of the answers above: keeping nothing and keeping a " +
            "scrollback are one window with two backings.",
        }),

        el("label", { for: "pick-send-turns", text: "turns sent upstream" }),
        sendField,
        el("p", {
          class: "hint",
          text:
            "Nothing at the other end is remembering this conversation; only what gets " +
            "sent carries it. More turns means a longer memory and a bigger request — " +
            "on your own API key you pay for every one of them, every turn. Set it to " +
            "1 for a gateway that holds the session itself, like the bridge, or to 0 " +
            "to send everything this window still holds.",
        }),

        el("label", { for: "pick-session-id", text: "session id" }),
        sessionField,
        el("p", {
          class: "hint",
          text:
            "Only for a session store. Leave it blank and Weft picks one for you and this " +
            "weft — the same on every device. Type one to join a session something else " +
            "already keeps; it is used as it stands. Letters, digits and . _ : - only.",
        }),
      ]),

      clearActions,
      historyError,
    ]);
  }

  // ---- request headers ----------------------------------------------------

  /**
   * The Headers section: rows of name and value, saved together.
   *
   * One save button rather than saving each row as it changes. A row is only a header
   * once both halves are typed, and saving on every keystroke would send the server a
   * name with no value, or half a name, and show its refusals while the user is still
   * typing. What comes back is what the server stored, trimmed and with blank rows
   * dropped, and the rows are redrawn from that.
   */
  function renderHeadersSection() {
    /** What is stored: [{name, value}]. */
    let stored = (headers ?? []).map(({ name, value }) => ({ name, value }));

    const rowsNode = el("div", { class: "header-rows" });
    const headersError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const saved = el("span", { class: "hint", hidden: "" });
    const add = el("button", {
      class: "small", type: "button", text: "add a header",
      onclick: () => {
        const row = headerRow({ name: "", value: "" });
        rowsNode.append(row.node);
        row.name.focus();
        changed();
      },
    });
    const submit = el("button", { type: "submit", text: "save headers" });

    /** @type {{node: HTMLElement, name: HTMLInputElement, value: HTMLInputElement}[]} */
    let rows = [];

    function headerRow({ name, value }) {
      const nameField = el("input", {
        type: "text", autocomplete: "off", autocapitalize: "none", spellcheck: "false",
        placeholder: "X-Session-Id", maxlength: "64", "aria-label": "header name",
        value: name,
      });
      const valueField = el("input", {
        type: "text", autocomplete: "off", autocapitalize: "none", spellcheck: "false",
        placeholder: "value", maxlength: "1024", "aria-label": "header value",
        value,
      });
      const row = { name: nameField, value: valueField, node: null };
      row.node = el("div", { class: "header-row" }, [
        nameField,
        valueField,
        el("button", {
          class: "small", type: "button", text: "×", title: "remove this header",
          "aria-label": "remove this header",
          onclick: () => {
            row.node.remove();
            rows = rows.filter((r) => r !== row);
            changed();
          },
        }),
      ]);
      nameField.addEventListener("input", changed);
      valueField.addEventListener("input", changed);
      rows.push(row);
      return row;
    }

    function changed() {
      saved.hidden = true;
    }

    /** The line outside Advanced: what this pick sends, said without opening it. */
    const note = el("p", { class: "hint" });

    function paint() {
      rows = [];
      rowsNode.replaceChildren(...stored.map((h) => headerRow(h).node));
      const count = stored.length;
      note.textContent = count
        ? `This weft sends ${count} extra header${count === 1 ? "" : "s"} with every message.`
        : "None. Most providers need none.";
    }

    const form = el("form", {}, [
      rowsNode,
      el("div", { class: "header-buttons" }, [add, saved]),
      submit,
      headersError,
    ]);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      headersError.hidden = true;
      submit.disabled = true;
      try {
        const answer = await api.setWeftHeaders(
          weftId, rows.map((r) => ({ name: r.name.value, value: r.value.value })),
        );
        stored = answer.headers;
        changed(answer);
        paint();
        saved.textContent = stored.length ? "saved." : "saved: none are sent.";
        saved.hidden = false;
      } catch (err) {
        // Leave the rows as typed, so the one the server named can be fixed in place.
        headersError.textContent = err.detail || err.message || "Could not save those headers.";
        headersError.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });

    paint();

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Request headers" }),
      note,
      el("details", { class: "advanced", ...(stored.length ? { open: "" } : {}) }, [
        el("summary", { text: "Advanced" }),
        el("p", {
          class: "hint",
          text:
            "Extra HTTP headers sent with this weft's chat requests, for a gateway that " +
            "reads them: a harness that pins its session from a header, say. They are " +
            "shown here in the clear, so do not put a key in one; the provider's token " +
            "field is for that. Authorization and the headers that describe the request " +
            "itself are Weft's, and refused.",
        }),
        form,
      ]),
    ]);
  }

  // ---- the MCP door ------------------------------------------

  /**
   * The Door section: let this weft's agent show pictures in its replies.
   *
   * THE KEY NEVER GOES THROUGH THE CHAT. Everything said there is kept, by the session
   * store and the agent's memory alike. So "invite" makes a one-time address instead,
   * and the chat carries that: the agent reads it, which mints the key and hands it the
   * guide, and the address is dead from then on. "Put it in the chat" fills the box
   * and does not send, so the user sees exactly what is going and presses send.
   *
   * By hand is the fallback, for an agent that cannot set up its own MCP server: the
   * guide, key included, is shown once here, to be pasted into the agent's config by
   * the user. Tucked under Advanced, because it puts a key on the screen.
   */
  function renderDoorSection() {
    const now = el("p", { class: "hint" });
    const doorError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const shown = el("div", { class: "door-shown" });
    /** @type {{open: boolean, hint?: string, last_used_at?: string, created_at?: string}} */
    let door = { open: false };

    function when(iso) {
      return iso ? new Date(iso).toLocaleString() : "";
    }

    function paint() {
      now.textContent = door.open
        ? `Open: key ending ${door.hint}, made ${when(door.created_at)}. ` +
          (door.last_used_at ? `Last used ${when(door.last_used_at)}.` : "Not used yet.")
        : "Shut. No agent can reach Weft as this weft.";
      close.hidden = !door.open;
    }

    async function reread() {
      try {
        door = await api.getDoor(weftId);
        paint();
      } catch (err) {
        fail(err, "Could not read the door.");
      }
    }

    function fail(err, fallback) {
      doorError.textContent = err.detail || err.message || fallback;
      doorError.hidden = false;
    }

    /** Copy `text`, or failing that leave it selected in `field` for the user to copy. */
    async function copy(text, field, button) {
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "copied";
      } catch {
        field.select();
        button.textContent = "selected: copy it";
      }
    }

    function readonlyBox(text, rows) {
      return el("textarea", {
        class: "door-text", readonly: "", rows: String(rows), spellcheck: "false",
        text,
      });
    }

    const invite = el("button", {
      class: "small", type: "button", text: "invite the agent",
      onclick: async () => {
        doorError.hidden = true;
        try {
          const { url, expires_in: seconds } = await api.inviteToDoor(weftId);
          const prompt =
            "Please connect to Weft. Fetch this page with curl, or another tool that " +
            "returns it word for word rather than a summary, and follow its " +
            `instructions. It works once: ${url}`;
          const box = readonlyBox(prompt, 4);
          const copyButton = el("button", {
            class: "small", type: "button", text: "copy",
            onclick: () => copy(prompt, box, copyButton),
          });
          shown.replaceChildren(
            el("p", {
              class: "hint",
              text: `Send this to ${weft.name}. It works once, within ` +
                `${Math.round(seconds / 60)} minutes; reading it connects the agent. ` +
                "It holds no key, so it is safe in the chat.",
            }),
            box,
            el("div", { class: "row-confirm" }, [
              el("button", {
                class: "small", type: "button", text: "put it in the chat",
                onclick: () => onCompose?.(weft, prompt),
              }),
              copyButton,
              el("button", { class: "small", type: "button", text: "check again",
                             onclick: reread }),
            ]),
          );
        } catch (err) {
          fail(err, "Could not make an invite.");
        }
      },
    });

    const byHand = el("button", {
      class: "small", type: "button", text: "make a key",
      onclick: async () => {
        doorError.hidden = true;
        try {
          const made = await api.makeDoorKey(weftId);
          const box = readonlyBox(made.guide, 12);
          const copyButton = el("button", {
            class: "small", type: "button", text: "copy the guide",
            onclick: () => copy(made.guide, box, copyButton),
          });
          shown.replaceChildren(
            el("p", {
              class: "hint",
              text: "Shown once. Put the key in your agent's MCP config yourself, " +
                "following the guide. Do not paste it into the chat.",
            }),
            box,
            el("div", { class: "row-confirm" }, [copyButton]),
          );
          door = made;
          paint();
        } catch (err) {
          fail(err, "Could not make a key.");
        }
      },
    });

    const close = el("button", {
      class: "small danger", type: "button", text: "close the door", hidden: "",
      onclick: async () => {
        doorError.hidden = true;
        try {
          await api.closeDoor(weftId);
          shown.replaceChildren();
          door = { open: false };
          paint();
        } catch (err) {
          fail(err, "Could not close the door.");
        }
      },
    });

    paint();
    reread();

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Agent door" }),
      el("p", {
        class: "hint",
        text: "Lets this weft's agent show you pictures in its replies, through " +
          "Weft's MCP server. An invite or a new key replaces the old key.",
      }),
      now,
      el("div", { class: "row-confirm" }, [invite, close]),
      el("details", { class: "advanced" }, [
        el("summary", { text: "Advanced" }),
        el("p", {
          class: "hint",
          text: "For an agent that cannot add an MCP server itself: make a key here " +
            "and set it up by hand.",
        }),
        el("div", { class: "row-confirm" }, [byHand]),
      ]),
      shown,
      doorError,
    ]);
  }

  // ---- name ---------------------------------------------------------------

  /** The Name section: one field, saved on change. A name taken by another of this
   *  user's wefts is refused by the server, and the field goes back. */
  function renderNameSection() {
    const field = el("input", {
      type: "text", id: "weft-name", autocomplete: "off", maxlength: "64",
      value: weft.name,
    });
    const nameError = el("p", { class: "form-error", role: "alert", hidden: "" });

    field.addEventListener("change", async () => {
      const wanted = field.value.trim();
      if (!wanted || wanted === weft.name) {
        field.value = weft.name;
        return;
      }
      nameError.hidden = true;
      field.disabled = true;
      try {
        changed(await api.renameWeft(weftId, wanted));
      } catch (err) {
        nameError.textContent = err.detail || err.message || "Could not rename it.";
        nameError.hidden = false;
      } finally {
        field.disabled = false;
        field.value = weft.name;
      }
    });

    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Name" }),
      field,
      nameError,
    ]);
  }

  // ---- engine -------------------------------------------------------------

  /** Set by the Engine section: offer the chat providers once load() has them. */
  let offerChatProviders = () => {};

  /**
   * The Engine section: which provider answers this weft, and with which model.
   *
   * A provider dropdown, then a model chooser. The chooser is shut by default and
   * shows the bound model; tapped, it opens a filter and that provider's live model
   * list (a real provider answered with 445). Tapping a model binds it at once and
   * shuts it again. Shut, because an open list of hundreds sat in the middle of the
   * page and caught the thumb of anyone scrolling past it to the colours below.
   * Changing the engine changes nothing else on this page: same weft, same colours,
   * same thread.
   */
  function renderEngineSection() {
    const engineError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const now = el("p", { class: "hint" });
    const providerSelect = el("select", { id: "weft-provider" });
    const filter = el("input", {
      type: "search", class: "filter", autocomplete: "off", autocapitalize: "none",
      spellcheck: "false", placeholder: "filter models…", hidden: "",
    });
    const listStatus = el("p", { class: "hint", hidden: "" });
    const list = el("div", { class: "rows engine-models" });
    const modelLabel = el("span", { class: "model-picked" });
    const modelButton = el("button", {
      type: "button", id: "weft-model", class: "model-button", "aria-expanded": "false",
      onclick: () => setOpen(!open),
    }, [modelLabel, el("span", { class: "chev", "aria-hidden": "true", text: "▾" })]);
    const drop = el("div", { class: "model-drop", hidden: "" }, [filter, listStatus, list]);
    const modelField = el("div", { class: "model-field", hidden: "" }, [
      el("label", { for: "weft-model", text: "model" }),
      modelButton,
      drop,
    ]);
    const unbind = el("button", {
      class: "small", type: "button", text: "no engine",
      title: "unbind this weft from its provider",
      onclick: () => bind(null, null),
    });

    /** @type {{id: string, name: string}[]} */
    let chatProviders = [];
    /** The provider whose models are listed, which is not necessarily the bound one. */
    let shownProvider = null;
    /** Bumped per listing, so a slow answer for a provider no longer chosen is dropped. */
    let listing = 0;
    /** Whether the model chooser is open. */
    let open = false;
    /** The chooser's label while a listing is out, or one failed. */
    let asking = false;

    function setOpen(yes) {
      open = yes;
      drop.hidden = !yes;
      modelButton.setAttribute("aria-expanded", String(yes));
      if (!yes) return;
      // Straight to the filter where there is a keyboard. On a phone, focusing it would
      // throw the on-screen keyboard over the list before you had seen it.
      if (window.matchMedia("(pointer: fine)").matches && !filter.hidden) filter.focus();
      const on = list.querySelector(".engine-model.on");
      if (on) list.scrollTop = on.offsetTop - (list.clientHeight - on.offsetHeight) / 2;
    }

    function paintModelButton() {
      const bound = shownProvider && shownProvider === weft.provider_id && weft.model_id;
      modelLabel.textContent = asking ? "asking the provider…"
        : bound ? weft.model_id : "choose a model…";
      modelButton.classList.toggle("chosen", Boolean(bound) && !asking);
    }

    function paintNow() {
      if (weft.provider_id && weft.model_id) {
        now.textContent = `Answered by ${weft.model_id} on ${weft.provider_name}.`;
      } else if (weft.model_id) {
        now.textContent = `No engine: it was ${weft.model_id}, on a provider since ` +
          "deleted. Choose a provider and a model, and it carries on as it was.";
      } else {
        now.textContent = "No engine yet. Choose a provider, then a model.";
      }
      unbind.hidden = !weft.provider_id;
      for (const row of list.children) {
        row.classList.toggle("on", shownProvider === weft.provider_id
          && row.dataset.model === weft.model_id);
      }
      paintModelButton();
    }

    offerChatProviders = (providers) => {
      chatProviders = providers;
      providerSelect.replaceChildren(
        el("option", { value: "", text: providers.length ? "choose a provider…" : "no chat providers yet" }),
        ...providers.map((p) => el("option", { value: p.id, text: p.name })),
      );
      providerSelect.value = chatProviders.some((p) => p.id === weft.provider_id)
        ? weft.provider_id : "";
      if (providerSelect.value) listModels(providerSelect.value);
    };

    async function listModels(providerId) {
      shownProvider = providerId || null;
      const mine = ++listing;
      list.replaceChildren();
      filter.hidden = true;
      filter.value = "";
      modelField.hidden = !providerId;
      if (!providerId) {
        listStatus.hidden = true;
        setOpen(false);
        return;
      }
      // A provider this weft is not on yet has nothing to show shut: open the list,
      // because choosing a model is the only thing left to do.
      const choosing = providerId !== weft.provider_id || !weft.model_id;
      asking = true;
      paintModelButton();
      listStatus.textContent = "Asking the provider…";
      listStatus.hidden = false;
      setOpen(choosing);
      try {
        const answer = await api.listModels(providerId);
        if (mine !== listing) return;
        list.replaceChildren(...answer.models.map(modelRow));
        filter.hidden = answer.models.length < 12;
        listStatus.textContent = answer.models.length
          ? `${answer.models.length} model${answer.models.length === 1 ? "" : "s"}. Tap one to use it.`
          : "That endpoint answered, but listed no models.";
        asking = false;
        paintNow();
        if (open) setOpen(true);
      } catch (err) {
        if (mine !== listing) return;
        // The provider's own words on a 502 are the most useful thing to show, and it
        // is inside the chooser, so open it.
        asking = false;
        paintNow();
        listStatus.textContent = err.detail || err.message || "Could not reach that provider.";
        setOpen(true);
      }
    }

    function modelRow(model) {
      const others = model.wefts.filter((name) => name !== weft.name);
      const row = rowButton({
        class: "row engine-model",
        onclick: () => bind(shownProvider, model.id),
      }, [
        el("div", { class: "row-main" }, [
          el("div", { class: "row-name", text: model.id }),
          ...(others.length
            ? [el("div", { class: "row-sub", text: `used by ${others.join(", ")}` })]
            : model.owned_by ? [el("div", { class: "row-sub", text: model.owned_by })] : []),
        ]),
      ]);
      row.dataset.model = model.id;
      row.dataset.search = `${model.id} ${model.owned_by}`.toLowerCase();
      return row;
    }

    async function bind(providerId, modelId) {
      engineError.hidden = true;
      try {
        changed(await api.setWeftEngine(weftId, { providerId, modelId }));
        if (providerId) setOpen(false);
        else {
          providerSelect.value = "";
          listModels("");
        }
      } catch (err) {
        engineError.textContent = err.detail || err.message || "Could not change the engine.";
        engineError.hidden = false;
      } finally {
        paintNow();
      }
    }

    providerSelect.addEventListener("change", () => listModels(providerSelect.value));
    filter.addEventListener("input", () => {
      const needle = filter.value.trim().toLowerCase();
      for (const row of list.children) {
        row.hidden = Boolean(needle) && !row.dataset.search.includes(needle);
      }
    });
    // Escape shuts the chooser, not the whole page behind it.
    modelField.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !open) return;
      event.stopPropagation();
      setOpen(false);
      modelButton.focus();
    });

    paintNow();

    return el("div", { class: "panel-form panel-section", "data-section": "engine" }, [
      el("h2", { class: "section-title", text: "Engine" }),
      now,
      el("label", { for: "weft-provider", text: "provider" }),
      providerSelect,
      modelField,
      engineError,
      el("div", { class: "row-confirm" }, [unbind]),
    ]);
  }

  // ---- voice, as it stands ------------------------------------------------

  /** The line above the voice chooser: what this weft sounds like now. */
  const voiceNow = el("p", { class: "hint" });
  const silence = el("button", {
    class: "small", type: "button", text: "silence",
    title: "remove this weft's voice",
    onclick: () => save({ ttsProviderId: null, ttsModel: null, ttsVoice: null }),
  });

  /** The provider this weft speaks through, once load() has the list. */
  function currentSpeaker() {
    if (!weft.has_voice) return null;
    return speechProviders.find((p) => p.id === weft.tts_provider_id) ?? null;
  }

  /** "af_heart on kokoro, model kokoro": the whole voice, provider included. */
  function voiceDescription() {
    const where = currentSpeaker() ? ` on ${currentSpeaker().name}` : "";
    return `${weft.tts_voice}${where}, model ${weft.tts_model}`;
  }

  function paintVoice() {
    voiceNow.textContent = weft.has_voice
      ? `Speaks as ${voiceDescription()}. Choose a speech provider below to change it.`
      : "Silent. Choose a speech provider below to give it a voice.";
    silence.hidden = !weft.has_voice;
  }

  // ---- hearing ------------------------------------------------------------

  /**
   * The Hearing section: which ears push to talk uses in this weft. The account's by
   * default; a provider of its own, to try a new server beside the usual one; or none.
   * Language and the rest stay in the account's settings: they are about the user.
   */
  function renderHearingSection() {
    const hearError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const choice = el("select", { id: "weft-ears" });
    const models = el("datalist", { id: "weft-ears-models" });
    const model = el("input", {
      type: "text", id: "weft-ears-model", list: "weft-ears-models", autocomplete: "off",
      autocapitalize: "none", spellcheck: "false", placeholder: "whisper-1",
    });
    const modelPart = el("div", {}, [
      el("label", { for: "weft-ears-model", text: "model" }),
      model,
      models,
      el("p", { class: "hint", text: "Blank sends whisper-1. Many self-hosted servers ignore this and use whatever they loaded." }),
    ]);
    const note = el("p", { class: "hint" });

    /** @type {{id: string, name: string}[]} */
    let hearing = [];
    /** The account's provider, by name, or null when it has none. */
    let accountEars = null;

    /** "account", "off", or the id of a provider of its own. "" is a deleted one. */
    function chosen() {
      if (weft.stt_mode === "off") return "off";
      if (weft.stt_mode === "own") return weft.stt_provider_id ?? "";
      return "account";
    }

    function paint() {
      const options = [
        el("option", {
          value: "account",
          text: accountEars ? `your default: ${accountEars}` : "your default (none chosen)",
        }),
        ...hearing.map((p) => el("option", { value: p.id, text: p.name })),
        el("option", { value: "off", text: "off" }),
      ];
      if (chosen() === "") {
        options.unshift(el("option", { value: "", text: "its own (deleted)", disabled: "" }));
      }
      choice.replaceChildren(...options);
      choice.value = chosen();
      model.value = weft.stt_model ?? "";
      modelPart.hidden = weft.stt_mode !== "own" || !weft.stt_provider_id;
      note.textContent = chosen() === ""
        ? "Its own speech-to-text provider was deleted, so it hears nothing. Choose another."
        : weft.stt_mode === "off"
          ? "Push to talk is off for this weft."
          : weft.stt_mode === "own"
            ? "Hears through its own provider. Language and the rest come from your settings."
            : accountEars
              ? "Hears through the provider in your settings, like every weft that has not chosen."
              : "Choose a speech-to-text provider in your settings, or one of its own here.";
    }

    async function fillModels(providerId) {
      models.replaceChildren();
      try {
        const { models: ids } = await api.sttModels(providerId);
        for (const id of ids) models.append(el("option", { value: id }));
      } catch {
        /* typing a model name still works */
      }
    }

    async function save(change) {
      hearError.hidden = true;
      try {
        changed(await api.setWeftEars(weftId, change));
      } catch (err) {
        hearError.textContent = err.detail || err.message || "Could not save that.";
        hearError.hidden = false;
      }
      paint();
    }

    choice.addEventListener("change", () => {
      const value = choice.value;
      if (value === "account" || value === "off") {
        save({ mode: value });
        return;
      }
      // A different provider's model names mean nothing to this one.
      const keep = value === weft.stt_provider_id ? model.value.trim() : "";
      fillModels(value);
      save({ mode: "own", providerId: value, model: keep });
    });
    model.addEventListener("change", () => save({
      mode: "own", providerId: weft.stt_provider_id, model: model.value.trim(),
    }));

    offerEars = (providers, settings) => {
      hearing = providers;
      accountEars = settings?.available ? settings.provider_name : null;
      if (weft.stt_mode === "own" && weft.stt_provider_id) fillModels(weft.stt_provider_id);
      paint();
    };
    paint();

    return el("div", { class: "panel-form panel-section", "data-section": "hearing" }, [
      el("h2", { class: "section-title", text: "Hearing" }),
      el("label", { for: "weft-ears", text: "hear me with" }),
      choice,
      note,
      modelPart,
      hearError,
    ]);
  }

  // ---- deleting -----------------------------------------------------------

  function renderDeleteSection() {
    const deleteError = el("p", { class: "form-error", role: "alert", hidden: "" });
    const ask = el("div", { class: "row-confirm" }, [
      el("button", {
        class: "small danger", type: "button", text: "delete this weft",
        onclick: () => ask.replaceWith(confirm),
      }),
    ]);
    const confirm = el("div", { class: "row-confirm" }, [
      el("span", { text: `Delete ${weft.name}, with its colours, picture, voice and settings?` }),
      el("button", {
        class: "small danger", type: "button", text: "delete",
        onclick: async () => {
          try {
            await api.deleteWeft(weftId);
            onDeleted?.(weft);
            panels.pop();
          } catch (err) {
            deleteError.textContent = err.detail || err.message || "Could not delete it.";
            deleteError.hidden = false;
            confirm.replaceWith(ask);
          }
        },
      }),
      el("button", {
        class: "small", type: "button", text: "cancel",
        onclick: () => confirm.replaceWith(ask),
      }),
    ]);
    return el("div", { class: "panel-form panel-section" }, [
      el("h2", { class: "section-title", text: "Delete" }),
      el("p", {
        class: "hint",
        text: "A thread kept in a session store stays in the store; this device's " +
          "scrollback goes with the weft.",
      }),
      ask,
      deleteError,
    ]);
  }

  /** The form, once a speech provider has been chosen. */
  function renderFor(speechProvider) {
    body.replaceChildren();
    clearError();
    status.textContent = "Asking that provider what it can say…";

    // What is already there, when this is the provider the weft speaks through.
    const saved = currentSpeaker()?.id === speechProvider.id
      ? { model: weft.tts_model, voice: weft.tts_voice }
      : null;

    // A text field with suggestions rather than a dropdown: a provider's list is a
    // help, not the whole truth, and some servers list nothing at all.
    const modelList = el("datalist", { id: "voice-model-list" });
    const modelField = el("input", {
      type: "text", id: "voice-model", list: "voice-model-list", autocomplete: "off",
      autocapitalize: "none", spellcheck: "false", placeholder: "kokoro",
    });
    modelField.value = saved?.model ?? "";
    const voiceSelect = el("select", { id: "voice-name" });
    const voiceField = el("input", {
      type: "text", id: "voice-name-text", autocomplete: "off",
      autocapitalize: "none", spellcheck: "false", placeholder: "af_bella", hidden: "",
    });
    voiceField.value = saved?.voice ?? "";

    const submit = el("button", { type: "submit", text: "give it this voice" });
    const back = el("button", {
      class: "small", type: "button", text: "cancel",
      onclick: () => {
        clearError();
        renderChooser();
      },
    });

    // Saving here replaces the voice there is. Say so, unless it is that voice being
    // opened again.
    const replaces = weft.has_voice && !saved
      ? [el("p", { class: "hint", text: `This replaces ${voiceDescription()}.` })]
      : [];

    const form = el("form", {}, [
      el("label", { for: "voice-model", text: "speech model" }),
      modelField,
      modelList,
      el("label", { for: "voice-name", text: "voice" }),
      voiceSelect,
      voiceField,
      ...replaces,
      submit,
      el("div", { class: "row-confirm" }, [back]),
    ]);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const chosenVoice = voiceSelect.hidden ? voiceField.value.trim() : voiceSelect.value;
      const chosenModel = modelField.value.trim();
      if (!chosenModel || !chosenVoice) {
        showError("A voice needs both a speech model and a voice name.");
        return;
      }
      submit.disabled = true;
      save({
        ttsProviderId: speechProvider.id,
        ttsModel: chosenModel,
        ttsVoice: chosenVoice,
      }).finally(() => {
        submit.disabled = false;
      });
    });

    body.append(form);

    api.listVoices(speechProvider.id).then((info) => {
      status.textContent = "";

      for (const id of info.models) modelList.append(el("option", { value: id }));
      const start = startingVoice(info, saved);
      // Something typed while the provider was answering wins over the guess.
      if (!modelField.value) modelField.value = start.model;

      if (info.voices.length === 0) {
        // No /audio/voices. Not an error — OpenAI and OpenRouter have none either.
        voiceSelect.hidden = true;
        voiceField.hidden = false;
        if (!voiceField.value) voiceField.value = start.voice;
        status.textContent =
          `${speechProvider.name} does not list its voices, so type one in: ` +
          `the voice name from its documentation.`;
        return;
      }

      voiceSelect.replaceChildren();
      // A saved voice the provider no longer lists stays on offer, so opening the form
      // cannot quietly swap it for the first voice in the list.
      const offered = info.voices.includes(start.voice)
        ? info.voices
        : [start.voice, ...info.voices].filter(Boolean);
      for (const voice of offered) {
        voiceSelect.append(el("option", { value: voice, text: voice }));
      }
      voiceSelect.value = start.voice;
      status.textContent = `${info.voices.length} voices.`;
    }).catch((err) => {
      status.textContent = "";
      showError(err.detail || err.message || "Could not reach that speech provider.");
      // Still let them type: the provider may simply be asleep, and a saved voice will
      // work as soon as it wakes.
      voiceSelect.hidden = true;
      voiceField.hidden = false;
    });
  }

  function renderChooser() {
    body.replaceChildren();

    if (speechProviders.length === 0) {
      status.textContent = "";
      body.append(
        el("p", {
          class: "empty",
          text:
            "No speech providers yet. Add one on the Providers screen — a name, the " +
            "URL including /v1, and a key if it wants one — then come back and this " +
            "thread can have a voice.",
        }),
      );
      return;
    }

    status.textContent = "Which speech provider should say this one?";
    const rows = el("div", { class: "rows" });
    for (const speechProvider of speechProviders) {
      const isCurrent = currentSpeaker()?.id === speechProvider.id;
      const name = el("div", { class: "row-name", text: speechProvider.name });
      if (isCurrent) name.append(el("span", { class: "acct-badge", text: "current" }));
      rows.append(
        el("button", {
          class: isCurrent ? "row pick-speaker current" : "row pick-speaker",
          onclick: () => renderFor(speechProvider),
        }, [
          el("div", { class: "row-main" }, [
            name,
            el("div", { class: "row-sub", text: speechProvider.base_url }),
          ]),
        ]),
      );
    }
    body.append(rows);
  }

  async function load() {
    try {
      const [providers, sttSettings] = await Promise.all([
        api.listProviders(),
        api.sttSettings().catch(() => null),
      ]);
      speechProviders = providers.filter((p) => p.kind === "tts");
      offerEars(providers.filter((p) => p.kind === "stt"), sttSettings);
      stores = providers.filter((p) => p.kind === "session");
      offerChatProviders(providers.filter((p) => p.kind === "llm"));
      offerStores();
      // Again, now the provider's name is known.
      paintVoice();
      renderChooser();
    } catch (err) {
      status.textContent = "";
      showError(err.detail || err.message || "Could not load your providers.");
    }
  }

  const title = el("h1");
  const subtitle = el("div");

  function paintTitles() {
    title.textContent = weft.name;
    subtitle.textContent = weft.provider_id && weft.model_id
      ? `${weft.model_id} on ${weft.provider_name}`
      : "no engine";
  }
  paintTitles();
  paintVoice();

  const node = el("div", { class: "panel-inner" }, [
    panelHead(panels, { title, sub: subtitle }),
    el("div", { class: "panel-body" }, [
      renderNameSection(),
      renderEngineSection(),
      renderColoursSection(),
      renderBackgroundSection(),
      renderHistorySection(),
      // The voice section owns `error`, `status` and `body` between them — it is the
      // half of this panel that talks to somebody else's server and so the half that
      // has something to report.
      el("div", { class: "panel-form panel-section" }, [
        el("h2", { class: "section-title", text: "Voice" }),
        voiceNow,
        el("div", { class: "row-confirm" }, [silence]),
        error,
        status,
        body,
      ]),
      renderHearingSection(),
      renderHeadersSection(),
      renderDoorSection(),
      renderDeleteSection(),
    ]),
  ]);

  load();

  return { node, teardown: () => releaseBackground() };
}
