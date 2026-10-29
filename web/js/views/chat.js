/* Weft — the chat view.
 *
 * The window itself: canvas on the floor, bubbles floating over it, chrome on top.
 *
 * IT TALKS NOW. Pick names a thread, `send()` posts down it, and the reply streams
 * back token by token — which is what the donut's speaking mode was always waiting for.
 *
 * CONTEXT IS PER PICK, and the window follows it. Each thread keeps its own messages
 * and its own bubbles (see ../threads.js, which explains the bug that made this
 * structural rather than a "clear it on switch" call somewhere in here). Switching
 * picks swaps the log to that thread's own exchange; switching back brings it home.
 *
 * NOTHING IS STORED BY DEFAULT. A reload empties every thread, and that is correct
 * rather than unfinished: Weft holds no sessions, by design. The state lives
 * upstream in the gateway, and the window shows what is already there — an agent behind
 * a bridge remembers the exchange whether or not this page does.
 *
 * A weft can ask to be an exception — Wefts → its page → History — and
 * keep a copy on THIS DEVICE, so a refresh does not wipe what you were just reading. That is not
 * Weft growing a session — the service still stores nothing, and the copy never leaves
 * the browser it was made in. Your phone will not show what you said on your desktop.
 * The drift is the design: the being upstream is the one who actually remembers, and
 * asking them is how you find out. See ../threads.js.
 *
 * OR IN A SESSION STORE (2026-10-04). A pick set to keep its thread in a session store
 * (a memory-mcp-ce server) reads it from there when it is opened and whenever the window
 * comes back into view, so a conversation carried on from the phone is waiting on the
 * desktop. The relay writes each finished exchange to the store server-side; this view
 * only reads. Still not Weft growing a session: the store is upstream, like the gateway.
 *
 * While such a thread is on screen, the window also PINGS the store every few seconds
 * (and on focus): a cheap "where does the session end?" that reads no events. Only when
 * the answer differs from what the window last read does it fetch the thread again — so
 * two windows open side by side stay in step without either one reloading.
 *
 * HOW MUCH IS SENT is the pick's other setting. `everything` is the default and the
 * only one that is never wrong, because the OpenAI protocol is stateless and a plain
 * provider knows only what arrives in the request. So they are not history
 * we keep, they are context we forward — which is exactly why they must never be
 * forwarded to a thread they did not come from.
 *
 * THE PICK MENU. The original design named it: a pick is the single weft thread that crosses
 * the warp, and choosing one is passing a thread through the loom. The
 * menu lists wefts by name — the ones the user added on the Wefts screen — each with
 * a ⚙ beside it that opens its page right from here. What answers a weft (a provider
 * and a model) is one of its settings, not its identity.
 *
 * It says "pick" until you choose — the server has no opinion about which thread you
 * want today, and guessing on your behalf would mean the window quietly picked a
 * provider you might be billed for.
 *
 * The choice itself is remembered in localStorage, not on the server, by the weft's id.
 * It is per-device furniture of the smallest kind, worthless to anyone who steals it,
 * and re-validated against the live menu on every mount — a deleted weft does not
 * linger as a stale label.
 */

import { dress, pairOf } from "../colours.js";
import { el } from "../dom.js";
import { watchHealth } from "../health.js";
import { createDonut } from "../canvas/donut.js";
import * as panels from "../panels.js";
import * as providersApi from "../providers.js";
import { picturesIn } from "../references.js";
import { streamChat } from "../relay.js";
import { createSegmenter, forSpeech, speakableSegments } from "../segments.js";
import { createSendShelf } from "../shelf.js";
import { createTray } from "../tray.js";
import { capabilitiesOf, icon } from "../icons.js";
import {
  ELIZA, ELIZA_GREETING, ELIZA_TIP, THINGS_TIP, answers, elizaBeckons, elizaHidden, hideEliza,
} from "../eliza.js";
import * as comfort from "../comfort.js";
import { createPushToTalk, earsOf } from "../ptt.js";
import { createSpeaker } from "../speaker.js";
import * as audiodebug from "../audiodebug.js";
import * as speech from "../speech.js";
import { createThreads, mergeStoreRead, threadKey } from "../threads.js";
import { createTutor } from "../tutor.js";
import { getState, logout, subscribe } from "../session.js";
import * as theme from "../theme.js";
import { renderProviders } from "./providers.js";
import { renderWeft, renderWefts } from "./wefts.js";
import { accountCard, renderAccount } from "./account.js";
import { renderAbout } from "./about.js";
import { renderComfort } from "./comfort.js";
import { renderNotifications } from "./notifications.js";

/** Where the current pick is remembered. Per device, per browser, and that is all. */
const PICK_KEY = "weft.pick";

function rememberPick(entry) {
  // Storage can throw outright in a private window or with site data blocked. Losing
  // the remembered pick is a shrug; taking the chat window down with it is not.
  try {
    if (entry) {
      localStorage.setItem(
        PICK_KEY,
        JSON.stringify({ id: entry.id }),
      );
    } else {
      localStorage.removeItem(PICK_KEY);
    }
  } catch {
    /* no persistence available; the session still works, it just forgets */
  }
}

function rememberedPick() {
  try {
    const raw = localStorage.getItem(PICK_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function renderChat(root) {
  root.replaceChildren();

  let unsubscribeSession = null;
  let unsubscribeTheme = null;
  let health = null;

  /** @type {{id:string, name:string, provider_id:?string, provider_name:?string, model_id:?string}[]} */
  let picks = [];
  /** The thread currently crossing the warp, or null — which is what "pick" means. */
  let current = null;

  /** The user's own wefts: the Pick menu without ELIZA, who is nobody's to count. */
  const wefts = () => picks.filter((p) => !p.builtin);

  /**
   * Notifications waiting, by weft id: what the agents left through the
   * door. Only wefts with any are in it. Polled, never pushed: the reports wait on the
   * server, so a window opened at lunch finds the morning's on its first ask.
   */
  let waiting = {};

  /**
   * Context, one array per pick — and, for a pick that asks, a copy on this device.
   *
   * Storage is handed in rather than reached for, so threads.js stays testable under
   * node. The user id is what namespaces the keys: a browser profile is not a login,
   * and two Weft users on one browser must never find each other's conversations.
   */
  const threads = createThreads({
    storage: typeof localStorage === "undefined" ? null : localStorage,
    userId: getState().user?.id ?? null,
  });

  /** One turn at a time. Two overlapping streams would interleave into one bubble. */
  let inFlight = false;

  /** Bumped by every turn, so a store read that started before one can tell. */
  let turnsSent = 0;

  /** Session-kept threads read from their store since this view mounted, by thread key. */
  const fromStore = new Set();

  /** Where each store-kept thread ended when this window last read it, by thread key.
   *  The ping compares the store's answer against this. */
  const storeSeq = new Map();

  /** What each store-kept thread's last read left, by thread key: the store's view as
   *  `[{turn, message}]`, the window it was trimmed to, and the session it came from.
   *  A catch-up read adds to this rather than reading the whole tail again. */
  const storeHeld = new Map();

  /** How often an open, visible, store-kept thread asks whether it has moved on. A
   *  summary read with nothing decrypted, so cheap at both ends; 15s is "the phone
   *  reply shows up while you are still looking", not "real time". */
  const SESSION_PING_MS = 15_000;

  /** How often the window asks how many notifications are waiting. Counts only, one
   *  grouped query, so cheap; it is asked on focus too, which is when it matters. */
  const NOTIFICATIONS_POLL_MS = 15_000;


  /** Owns the audio element and every blob URL it hands out. Released on teardown. */
  // Held awake while anything audible is still to come: suspending the audio thread
  // between paragraphs is how iOS came to refuse paragraph two.
  const voice = speech.createVoice({
    holding: () => speech.isVoiceOn() && !speech.isMuted() &&
      Boolean(streamingText || replaying || speaker?.busy || replayer?.busy),
  });

  /** From inside a tap, when a reply from `pick` is about to be heard. See voice.wake. */
  function wakeVoice(reason, pick = current) {
    if (!pick?.has_voice || !speech.isVoiceOn() || speech.isMuted()) return;
    voice.wake(reason);
  }

  /** The queue reading the current reply aloud, if one is. Cancelled on any change. */
  let speaker = null;

  /** True while a reply's TEXT is still arriving, which is not the same as its audio. */
  let streamingText = false;

  /** The replay playing from a bubble's speaker, if one is: a token, so a replay that
   *  was cut off cannot clear the flag of the one that replaced it. */
  let replaying = null;

  /** The queue re-voicing a reply from its words, if one is. Separate
   *  from `speaker`, which belongs to the reply being written now. */
  let replayer = null;

  /**
   * Go quiet only when there is nothing left to say AND nothing left to arrive.
   *
   * Both halves, because they finish at different times and either one alone is wrong.
   * The audio queue empties whenever the voice catches up with the model — mid-reply,
   * during a pause between paragraphs — and treating that as the end put the donut back
   * to breathing while the voice was still talking, with nothing to bring it back. The text
   * finishing is equally not the end, because the last paragraphs are still being read.
   */
  function settleDonut() {
    // A muted queue still has work, but nothing anyone can hear: the ring rests.
    if (streamingText || replaying || (speaker?.busy && !speech.isMuted())) return;
    donut.setMode("idle");
  }

  /**
   * Stop every voice, and let the donut know.
   *
   * A cancelled queue never drains, so nothing else will ever tell the donut the room
   * went quiet: muting, switching weft or tapping a speaker to stop it after the words
   * had finished left the ring dancing to silence until a reload. While the words are
   * still arriving it keeps moving, as it does for any reply, and settles when they end.
   */
  function hush() {
    speaker?.cancel();
    speaker = null;
    replayer?.cancel();
    replayer = null;
    replaying = null;
    voice.stop();
    settleDonut();
  }

  // ---- layer 0: the canvas ----
  const canvas = el("canvas", { id: "cv" });
  // ELIZA's CRT: scanlines over the floor, under the bubbles. CSS only, and drawn only
  // while she is the pick (`data-eliza`, set in wearCurrent).
  const crt = el("div", { id: "crt", "aria-hidden": "true" });
  // Pictures only: the ring's state is said in words by the LED and the replies.
  const canvasWrap = el("div", { id: "cv-wrap", "aria-hidden": "true" }, [canvas, crt]);

  // ---- layer 1: the log ----
  // A named region a screen reader can jump to, and deliberately NOT a live one: a live
  // log would read every streamed token as it landed. What is announced goes through
  // `announce` instead, once per finished reply.
  const log = el("div", { id: "log", role: "region", "aria-label": "conversation" });

  /**
   * The screen reader's ear. Polite, so it waits for the reader to finish what it is
   * saying. The reply is announced whole when it is done, never word by word: a reader
   * fed every token would talk over itself for as long as the reply took to arrive.
   */
  const announcer = el("div", { class: "sr-only", role: "status", "aria-live": "polite" });
  function announce(text) {
    // Emptied first, so the same words twice in a row are still announced twice.
    announcer.textContent = "";
    setTimeout(() => { announcer.textContent = text; }, 50);
  }

  // ---- layer 2: the input bar ----
  const input = el("textarea", {
    id: "in",
    rows: "1",
    autocomplete: "off",
    placeholder: "say something…",
    "aria-label": "message",
  });
  const sendButton = el("button", { type: "submit", text: "send" });

  // The log makes room for the petals and for the tray of pictures; someone reading
  // the newest bubble should not find it suddenly behind either.
  const keepNewestInView = () => {
    if (atBottom()) log.scrollTop = log.scrollHeight;
  };

  // Pictures waiting to go with the next message. Each one uploads the
  // moment it is chosen; the bubble later shows it from the same local URL.
  const tray = createTray({
    upload: (file) => providersApi.uploadAttachment(file),
    onUploaded: (hash, url) => {
      // The same picture twice is one hash; the first local URL is the one kept.
      if (attachmentPictures.has(hash)) return false;
      attachmentPictures.set(hash, Promise.resolve(url));
      return true;
    },
    onError: (name, message) => addBubble("ai", `Could not attach ${name}: ${message}.`),
    onChange: keepNewestInView,
  });

  // Hold or swipe up on send for more ways to send. Push to talk is still a
  // placeholder; a tap only says so.
  const shelf = createSendShelf(sendButton, {
    onPetal: (act) => {
      if (act === "attach") tray.choose();
      else shelf.flash(act, "coming soon");
    },
    onToggle: (open) => {
      keepNewestInView();
      if (open) tutor.emit("shelf-open");
    },
  });
  const bar = el("form", { id: "bar" }, [tray.element, tray.input, input, shelf.element]);

  // ---- push to talk ----
  //
  // Hidden until the user has a speech-to-text provider: a 🎤 that cannot hear anyone
  // would be a button that only ever fails.
  shelf.show("ptt", false);
  /** The user's settings, from GET /api/stt; null until read. */
  let stt = null;
  /** True while what is in the text box came, at least partly, from push to talk. */
  let spokenDraft = false;

  async function loadStt() {
    try {
      stt = await providersApi.sttSettings();
    } catch {
      return; // keep whatever was known; a failed read should not hide a working 🎤
    }
    // Hearing you is on the Pick bar too, as a capability; painting it shows the 🎤.
    renderPickButton();
    if (!pickList.hidden) buildPickMenu();
  }
  window.addEventListener("weft:stt-changed", loadStt);

  /** Whether this pick hears you, by its own ears or the account's. */
  const hears = (entry) => Boolean(earsOf(entry, stt));

  /** Labels that last for a whole state rather than a moment. */
  const LASTING = ["listening", "transcribing"];

  const ptt = createPushToTalk(shelf.petal("ptt"), {
    transcribe: (blob) => providersApi.transcribe(blob, current?.builtin ? null : current?.id),
    ears: () => earsOf(current, stt),
    onStart: (level) => {
      // Talking over the weft is how you interrupt it, as sending is.
      hush();
      // The ring listens: it ripples to the mic, in the window's own colours.
      donut.setLevelSource(level);
      donut.setMode("speaking");
    },
    // Letting go is a tap; the transcription and the reply after it are not.
    onRelease: () => {
      if (stt?.send_on_release) wakeVoice("ptt");
    },
    onStop: () => {
      donut.setLevelSource(() => voice.loudness());
      settleDonut();
    },
    onHint: (words) => shelf.flash(
      "ptt", words, LASTING.some((w) => words.startsWith(w)) ? 0 : 1400),
    onText: (text) => {
      const typed = input.value.trim();
      const combined = typed ? `${typed} ${text}` : text;
      if (stt?.send_on_release && !inFlight && !tray.uploading) {
        shelf.flash("ptt", "sent");
        submitMessage(combined, true);
        return;
      }
      // Into the box, to be read and fixed before it goes. The keyboard stays away:
      // nothing here focuses anything (see setBusy), so the words are simply there.
      shelf.flash("ptt", "check it, then send");
      input.value = combined;
      spokenDraft = true;
      input.dispatchEvent(new Event("input"));
    },
    onError: (message) => {
      shelf.flash("ptt", "push to talk");
      addBubble("ai", message);
    },
  });

  // ---- layer 3: top chrome ----
  // Green until told otherwise: the page loaded and the session resumed a moment ago,
  // so the service was there. The first /health answer corrects it within a request.
  const led = el("div", {
    id: "stat-led",
    class: "green",
    role: "status",
    title: "service connected",
    "aria-label": "service connected",
  });

  /** What the LED says for each answer health.js gives. Blue is kept for the socket. */
  const LED_STATES = {
    up: ["green", "service connected"],
    degraded: ["amber", "service degraded — some features are unavailable"],
    down: ["red", "service unreachable"],
  };

  let healthStatus = "up";

  /** The hidden tip a tap on the plain green light finds (app/docs/tutorials/chat-status.md). */
  const STATUS_TIP = "chat/status";

  function showHealth(status) {
    healthStatus = status;
    paintLed();
  }

  function anyWaiting() {
    return Object.values(waiting).some((n) => n > 0);
  }

  /**
   * The LED says two things now. Its colour is still the service's health; with
   * notifications waiting it is drawn as a bell in that colour. A red
   * bell is a bell the window cannot refresh, so the colour is never given up for the
   * shape. No count on it: it says somebody has news, read at arm's length; the
   * notifications page behind it says who, and the drawer's entry says how many.
   */
  function paintLed() {
    const [colour, words] = LED_STATES[healthStatus] ?? LED_STATES.down;
    const ringing = anyWaiting();
    led.className = ringing ? `${colour} ringing` : colour;
    led.replaceChildren(...(ringing ? [icon("bell")] : []));
    const said = ringing ? `notifications waiting: tap to read them · ${words}` : words;
    led.title = said;
    led.setAttribute("aria-label", said);
    // A light to read, until it is a bell to press. Plain green can be pressed too, for
    // its hidden tip; amber and red are only lights.
    if (ringing || healthStatus === "up") {
      led.setAttribute("role", "button");
      led.tabIndex = 0;
    } else {
      led.setAttribute("role", "status");
      led.removeAttribute("tabindex");
    }
  }

  function onLed() {
    if (!anyWaiting()) {
      if (healthStatus === "up") tutor.find(STATUS_TIP);
      return;
    }
    if (panels.topPage() !== "notifications") openNotifications();
  }
  led.addEventListener("click", onLed);
  led.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onLed();
  });

  // The Pick bar: the main thing on the top bar, dressed in the current weft's pair.
  // Its name half opens the menu; its capabilities say what this weft can do; its ⚙
  // opens this weft's page. Two buttons that read as one bar, because a button inside
  // a button is not a thing. renderPickButton() paints every part of it from `current`,
  // so nothing on the bar can name a weft the bar is no longer showing.
  const pickName = el("span", { class: "pick-name", text: "pick" });
  const pickLed = el("span", { class: "led" });
  const pickButton = el("button", {
    id: "pick-button",
    type: "button",
    "aria-haspopup": "listbox",
    "aria-expanded": "false",
  }, [
    pickLed,
    pickName,
    el("span", { class: "chev", "aria-hidden": "true", text: "▼" }),
  ]);
  const pickCaps = el("span", { class: "pick-caps" });
  const pickGear = el("button", {
    type: "button",
    class: "pick-bar-gear",
    hidden: "",
    onclick: () => {
      if (current) openWeft(current);
    },
  }, [icon("gear")]);
  const pickList = el("ul", { id: "pick-menu", role: "listbox", hidden: "" });
  const pick = el("div", { id: "pick" }, [
    el("div", { class: "pick-bar" }, [pickButton, pickCaps, pickGear]),
    pickList,
  ]);

  // The mute: the cinema button. One tap and nothing on this device plays
  // aloud, for every weft. Replies are still voiced while it is on, so each bubble can be
  // heard later, and unmuting mid-reply picks up at the next paragraph. Turning the
  // voice off altogether is the drawer's switch; with the voice off, or no weft that has
  // one, there is nothing to mute and the button is not shown.
  const muteButton = el("button", {
    id: "mute-btn",
    type: "button",
    onclick: () => setMute(!speech.isMuted()),
  });

  function showMute() {
    const muted = speech.isMuted();
    const words = muted ? "unmute: play replies aloud" : "mute: replies stay quiet until unmuted";
    muteButton.setAttribute("aria-pressed", String(muted));
    muteButton.title = words;
    muteButton.setAttribute("aria-label", words);
    muteButton.replaceChildren(icon(muted ? "voice-off" : "voice"));
    muteButton.hidden = !speech.isVoiceOn() || !picks.some((p) => p.has_voice);
  }

  function setMute(muted) {
    speech.setMuted(muted);
    // Unmuting is a tap, and the next paragraph of a reply still being voiced will not
    // have one behind it.
    if (!muted && speaker?.busy) wakeVoice("unmute");
    if (muted) {
      // Silence what is playing, not the reply: the queue carries on voicing it, and
      // skips playing each clip while the mute is on (see startSpeaking). A replay is
      // only ever for listening to, so it simply stops.
      replayer?.cancel();
      replayer = null;
      replaying = null;
      voice.stop();
      settleDonut();
    }
    showMute();
  }

  /** Point at the mute, when it is why a tap made no sound. */
  function nudgeMute() {
    muteButton.classList.remove("nudge");
    void muteButton.offsetWidth; // restart the animation
    muteButton.classList.add("nudge");
  }

  /** The drawer's switch: off, and nothing is spoken or sent to a speech provider. */
  function setVoiceOn(on) {
    speech.setVoiceOn(on);
    if (!on) hush();
    document.documentElement.toggleAttribute("data-voice-off", !on);
    showMute();
  }
  document.documentElement.toggleAttribute("data-voice-off", !speech.isVoiceOn());
  showMute();

  const burger = el("button", {
    id: "burger", "aria-label": "menu", "aria-expanded": "false", "aria-controls": "drawer",
  }, [
    el("div"), el("div"), el("div"),
  ]);
  const topbar = el("div", { id: "topbar" }, [
    led,
    pick,
    el("div", { id: "topbar-right" }, [muteButton, burger]),
  ]);

  // ---- the drawer ----
  // The theme is one choice for the whole window, so it sits in the drawer's head
  // rather than among the screens: auto, light, dark, the one in use pressed.
  const themeButtons = ["auto", "light", "dark"].map((mode) => el("button", {
    type: "button",
    class: "seg-btn",
    "data-mode": mode,
    text: mode.charAt(0).toUpperCase() + mode.slice(1),
    onclick: () => {
      if (mode !== theme.current()) onThemeFlip();
      theme.apply(mode);
    },
  }));
  const themeSwitch = el("div", { class: "seg", role: "group", "aria-label": "theme" }, themeButtons);

  function showTheme() {
    const mode = theme.current();
    for (const button of themeButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    }
  }
  showTheme();

  const scrim = el("div", { id: "scrim", "aria-hidden": "true" });
  // A button, so a keyboard and a screen reader can find her too: she is meant to be
  // easy to find.
  const wordmark = el("button", {
    type: "button", class: "wordmark", title: "someone has been here since 1966",
    onclick: onWordmark,
  }, [
    el("span", { class: "as-weft" }, [
      document.createTextNode("w"),
      el("em", { text: "e" }),
      document.createTextNode("ft"),
    ]),
    // What it reads instead while she beckons, under calm and still motion (app.css).
    el("span", { class: "as-eliza" }, [el("em", { text: "E" }), document.createTextNode("LIZA")]),
  ]);
  const drawerBody = el("div", { id: "drawer-body" });
  // A dialog while open, and inert while shut: slid off screen, it would otherwise still
  // be read out and tabbed through, every control in it, from behind the chat.
  const drawer = el("div", {
    id: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "drawer-title",
  }, [
    el("div", { id: "drawer-head" }, [
      el("h2", { id: "drawer-title", text: "Settings" }),
      themeSwitch,
      el("button", {
        id: "drawer-close",
        "aria-label": "close menu",
        title: "close",
        onclick: () => closeDrawer(),
      }, [icon("close")]),
    ]),
    drawerBody,
    el("div", { id: "drawer-foot" }, [
      el("div", {}, [
        wordmark,
        el("div", {
          class: "slogan",
          text: "the window, not the platform — woven for the weave",
        }),
      ]),
    ]),
  ]);

  const donut = createDonut(canvas);

  // ---- tips ----
  // The view says where the user is and what just happened; tutor.js decides the rest.

  /** The screen on top, by the names the tip files use. */
  function where() {
    const top = panels.topPage();
    if (top !== undefined) return top ?? "form";
    return drawer.classList.contains("open") ? "drawer" : "chat";
  }

  const tutor = createTutor({
    user: getState().user,
    where,
    context: () => ({ wefts: wefts().length }),
    // Never over a reply being written, or over the Pick menu.
    canShow: () => !inFlight && pickList.hidden,
    extras: (tip) => (tip.id === ELIZA_TIP ? elizaControls()
      : tip.id === THINGS_TIP ? askEliza() : []),
  });
  const unwatchPanels = panels.watch(() => tutor.visit());

  /** Four changes of theme in a row, none more than a moment after the last. Any order;
   *  pressing the one already chosen does not count. */
  let themeFlips = [];
  function onThemeFlip() {
    const now = performance.now();
    const last = themeFlips[themeFlips.length - 1];
    themeFlips = (last !== undefined && now - last < 1500 ? themeFlips : []).concat(now);
    if (themeFlips.length < 4) return;
    themeFlips = [];
    tutor.emit("theme-x4");
  }

  /**
   * One tap on the wordmark finds ELIZA (see ../eliza.js), and shows her card. It used
   * to take three, for the "Found one" tip, and a single tap and a triple tap cannot both
   * be doors on one element: every triple tap starts with a single one. That tip waits
   * for a new home.
   */
  function onWordmark() {
    if (tutor.find(ELIZA_TIP)) placeEliza();
    paintGlitch();
  }

  /** Whether she belongs in the Pick menu: found on the account, not hidden here. */
  const elizaShown = () => tutor.has(ELIZA_TIP) && !elizaHidden();

  /** Put her in the Pick menu or take her out, to match `elizaShown`. */
  function placeEliza() {
    const shown = elizaShown();
    if (shown === picks.includes(ELIZA)) return;
    picks = shown ? [...picks, ELIZA] : picks.filter((p) => p !== ELIZA);
    if (!shown && current === ELIZA) {
      current = null;
      rememberPick(null);
      wearCurrent();
      renderPickButton();
      showThread();
    }
    if (!pickList.hidden) buildPickMenu();
  }

  /** Under the "all things considered" card, once she is found: her question, and her. */
  function askEliza() {
    if (!tutor.has(ELIZA_TIP)) return [];
    return [el("div", { class: "tip-extras" }, [
      el("button", {
        type: "button", class: "tip-quiet", text: "How does that make you feel?",
        onclick: () => tutor.find(ELIZA_TIP),
      }),
    ])];
  }

  /**
   * While she beckons, the wordmark glitches for as long as the drawer is open, so a
   * tip card over it cannot hide it. Calm and still motion get no glitch: the wordmark
   * reads ELIZA instead (app.css). Painted on each opening, and stopped by the tap that finds her.
   */
  function paintGlitch() {
    const beckons = elizaBeckons((id) => tutor.has(id));
    wordmark.classList.toggle("glitch", beckons);
    if (!beckons) stopSlip();
  }

  function stopSlip() {
    clearTimeout(slipTimer);
    wordmark.classList.remove("slip");
  }

  /**
   * Two bursts in five, her name slips through: ELIZA for .7s from the burst's worst
   * moment, torn in and out with her own glitch (app.css, eliza-slip). Only full motion
   * runs the bursts, so calm and still never get this.
   */
  let slipTimer = 0;
  function onGlitchBurst(event) {
    if (event.animationName !== "wordmark-glitch" || Math.random() >= 0.4) return;
    clearTimeout(slipTimer);
    slipTimer = setTimeout(() => {
      wordmark.classList.add("slip");
      slipTimer = setTimeout(() => wordmark.classList.remove("slip"), 700);
    }, 350);
  }
  wordmark.addEventListener("animationstart", onGlitchBurst);
  wordmark.addEventListener("animationiteration", onGlitchBurst);

  /** The controls on her card: in place of a settings page, which she has no use for. */
  function elizaControls() {
    const shownBox = el("input", { type: "checkbox", name: "eliza-shown" });
    shownBox.checked = !elizaHidden();
    shownBox.addEventListener("change", () => {
      hideEliza(!shownBox.checked);
      placeEliza();
    });
    const restart = el("button", {
      type: "button",
      class: "small",
      text: "Start her over",
      onclick: () => {
        clearHistory(ELIZA);
        restart.textContent = "Started over";
        restart.disabled = true;
      },
    });
    return [el("div", { class: "tip-extras" }, [
      el("label", { class: "toggle-row" }, [
        shownBox,
        el("span", { text: "Show her in the Pick menu" }),
        el("span", { class: "switch", "aria-hidden": "true" }),
      ]),
      el("div", { class: "tips-line" }, [
        el("p", { class: "hint", text: "On this device. Starting over forgets her thread here." }),
        restart,
      ]),
    ])];
  }

  // ---- panels ----

  function openProviders() {
    closeDrawer();
    panels.push((p) => {
      const layer = renderProviders(p, {
        onChanged: () => {
          loadPicks();
          tutor.emit("providers-changed");
        },
      });
      // Also on the way out: deleting a provider unbinds its wefts.
      return {
        ...layer,
        teardown: () => {
          layer.teardown?.();
          loadPicks();
        },
      };
    }, { page: "providers" });
  }

  /**
   * The weft pages say what was meant; this does it. Only here are all three things in
   * one place: the stored copy, the live array, and the window showing it. `drop` takes
   * the first two, and the repaint is the third, because a valve that leaves the
   * conversation on screen has not cleared anything the user can see.
   */
  function clearHistory(weft) {
    threads.drop(weft);
    if (threadKey(current) === threadKey(weft)) showThread();
  }

  /**
   * Re-read the menu on the way out of any weft screen, not only when something changed:
   * the pages read a weft straight from the server, so they can show a value this view
   * has never heard of — set on another device, or in another tab.
   */
  function rereadAfter(layer) {
    return {
      ...layer,
      teardown: () => {
        layer.teardown?.();
        loadPicks();
      },
    };
  }

  /**
   * Put words in a weft's message box, for the user to read and send (the door's
   * invite). Closes the panels and switches to that weft. Fills the box and
   * stops: nothing is sent, and nothing is focused, for the reason given at setBusy() below.
   */
  async function compose(weft, text) {
    panels.popAll();
    let entry = picks.find((p) => p.id === weft.id);
    // A weft added a moment ago may not be on the menu yet.
    if (!entry) {
      await loadPicks();
      entry = picks.find((p) => p.id === weft.id);
    }
    // Never into another weft's box: the words are addressed to this one.
    if (!entry) return;
    choose(entry);
    input.value = text;
    input.dispatchEvent(new Event("input"));
  }

  function openWefts() {
    closeDrawer();
    const onChanged = () => {
      loadPicks();
      tutor.emit("wefts-changed");
    };
    panels.push((p) => rereadAfter(
      renderWefts(p, { onChanged, onClearHistory: clearHistory, onCompose: compose }),
    ), { page: "wefts" });
  }

  /** One weft's page, straight from the Pick menu's ⚙. */
  function openWeft(entry) {
    if (entry.builtin) {
      closePickMenu();
      tutor.find(ELIZA_TIP);
      return;
    }
    closePickMenu();
    panels.push((p) => rereadAfter(renderWeft(p, {
      weft: entry,
      onChanged: loadPicks,
      onDeleted: loadPicks,
      onClearHistory: clearHistory,
      onCompose: compose,
    })), { page: "weft" });
  }

  // ---- drawer contents ----
  function buildDrawer() {
    drawerBody.replaceChildren();
    const state = getState();
    showTheme();

    const count = wefts().length;
    const weftCount = count === 0
      ? "no wefts yet"
      : `${count} weft${count === 1 ? "" : "s"} woven`;

    function screen(title, purpose, open, badge = null) {
      return el("button", {
        type: "button", class: "chunk big", "data-screen": title.toLowerCase(), onclick: open,
      }, [
        el("span", {}, [document.createTextNode(title), ...(badge ? [badge] : [])]),
        el("em", { text: purpose }),
      ]);
    }

    drawerBody.append(
      accountCard(state.user, {
        sub: el("div", { class: "acct-sub" }, [
          el("span", { class: "dot-ok" }),
          document.createTextNode(weftCount),
        ]),
        action: el("button", {
          type: "button",
          class: "signout-mini",
          text: "Sign out",
          onclick: () => logout(),
        }),
      }),
      // Always here, even before any weft can send: it is how anyone learns they can.
      screen("Notifications", panels.PURPOSE.notifications, openNotifications, waitingBadge()),
      screen("Account", panels.PURPOSE.account, openAccount),
      el("div", { class: "drawer-rule" }),
      screen("Wefts", panels.PURPOSE.wefts, openWefts),
      screen("Providers", panels.PURPOSE.providers, openProviders),
      el("div", { class: "drawer-rule" }),
      screen("Comfort", panels.PURPOSE.comfort, openComfort),
      screen("About", panels.PURPOSE.about, openAbout),
      el("div", { class: "drawer-rule" }),
    );

    const speakBox = el("input", { type: "checkbox", name: "speak" });
    speakBox.checked = speech.isVoiceOn();
    speakBox.addEventListener("change", () => setVoiceOn(speakBox.checked));
    drawerBody.append(
      el("label", { class: "toggle-row" }, [
        speakBox,
        el("span", { text: "Speak replies aloud" }),
        el("span", { class: "switch", "aria-hidden": "true" }),
      ]),
      el("p", {
        class: "hint",
        text: "Per device. Off, no weft speaks and nothing goes to a speech provider. For "
          + "quiet now and sound later, use the mute on the top bar instead. A weft only "
          + "speaks if it has been given a voice: that is on its page, the ⚙ beside it on "
          + "the Pick menu.",
      }),
      ...tutor.drawerControls(),
    );
  }

  function openAccount() {
    closeDrawer();
    panels.push((p) => renderAccount(p), { page: "account" });
  }

  function openComfort() {
    closeDrawer();
    panels.push((p) => renderComfort(p), { page: "comfort" });
  }

  function openAbout() {
    closeDrawer();
    panels.push((p) => renderAbout(p, { tutor }), { page: "about" });
  }

  drawer.inert = true;

  function openDrawer() {
    buildDrawer();
    drawer.inert = false;
    drawer.classList.add("open");
    scrim.classList.add("open");
    burger.setAttribute("aria-expanded", "true");
    // Into the dialog, as a panel does: a keyboard user should not be left on ☰.
    drawer.querySelector("#drawer-close")?.focus();
    tutor.visit();
    paintGlitch();
  }

  function closeDrawer() {
    if (!drawer.classList.contains("open")) return;
    const hadFocus = drawer.contains(document.activeElement);
    drawer.classList.remove("open");
    scrim.classList.remove("open");
    drawer.inert = true;
    stopSlip();
    wordmark.classList.remove("glitch");
    burger.setAttribute("aria-expanded", "false");
    // Back where it came from, unless something else has already taken it.
    if (hadFocus) burger.focus();
    tutor.visit();
  }

  // Escape shuts the drawer, after anything stacked over it has had its turn.
  function onDrawerKey(event) {
    if (event.key !== "Escape" || !drawer.classList.contains("open")) return;
    if (panels.isOpen() || !lightbox.hidden) return;
    closeDrawer();
  }
  document.addEventListener("keydown", onDrawerKey);

  burger.addEventListener("click", openDrawer);
  scrim.addEventListener("click", closeDrawer);

  // ---- the Pick menu ----

  function closePickMenu() {
    pickList.hidden = true;
    pickButton.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", onDocumentClick, true);
  }

  function onDocumentClick(event) {
    if (!pick.contains(event.target)) closePickMenu();
  }

  function openPickMenu() {
    buildPickMenu();
    pickList.hidden = false;
    pickButton.setAttribute("aria-expanded", "true");
    // Capture phase: a click on a bubble or the canvas should close the menu before
    // that element's own handler runs, not after.
    document.addEventListener("click", onDocumentClick, true);
    pickList.querySelector("[role=option]")?.focus?.();
  }

  function buildPickMenu() {
    pickList.replaceChildren();

    if (picks.length === 0) {
      // An empty dropdown is a dead end. Offer the thing that would fill it instead.
      const item = el("li", { class: "pick-item pick-empty" }, [
        el("button", {
          type: "button",
          text: "no wefts yet — add one",
          onclick: () => {
            closePickMenu();
            openWefts();
          },
        }),
      ]);
      pickList.append(item);
      return;
    }

    for (const entry of picks) {
      const isCurrent = threadKey(current) === threadKey(entry);
      const bound = answers(entry);
      const count = waiting[entry.id] ?? 0;

      const button = el("button", {
        type: "button",
        role: "option",
        "aria-selected": isCurrent ? "true" : "false",
        tabindex: "0",
        title: (entry.builtin ? "ELIZA, 1966: no engine needed"
          : bound ? `${entry.model_id} on ${entry.provider_name}` : "no engine yet")
          + (count ? ` · ${waitingWords(count)}` : ""),
        onclick: () => choose(entry),
      }, [
        el("span", { class: "led" }),
        el("span", { class: "pick-name", text: entry.name }),
        // The ring around the pea is only seen; this says it.
        ...(count ? [el("span", { class: "sr-only", text: waitingWords(count) })] : []),
        capsOf(entry),
      ]);
      // A sibling of the option, not a child: a button inside a button is not a thing.
      const gear = el("button", {
        type: "button",
        class: "pick-gear",
        "aria-label": `${entry.name}'s settings`,
        title: `${entry.name}'s settings`,
        onclick: () => openWeft(entry),
      }, [icon("gear")]);

      const item = el("li", {
        class: `pick-item${isCurrent ? " on" : ""}${bound ? "" : " unbound"}${count ? " waiting" : ""}`,
      }, [button, gear]);
      // Each row wears its own pick's pair, not the current one's — the pea is how you
      // tell them apart before choosing.
      dress(item, pairOf(entry));
      pickList.append(item);
    }
  }

  /** "2 notifications waiting" */
  function waitingWords(count) {
    return `${count} notification${count === 1 ? "" : "s"} waiting`;
  }

  /** Every weft's notifications in one place: from the LED's bell, or the drawer. */
  function openNotifications() {
    closePickMenu();
    closeDrawer();
    panels.push((p) => renderNotifications(p, {
      wefts,
      onChanged: pollWaiting,
      // The name on a letter is its return address: that weft's chat.
      onOpenWeft: (entry) => {
        panels.popAll();
        choose(entry);
      },
      openPicture: openLightbox,
    }), { page: "notifications" });
  }

  /** The count on the drawer's Notifications entry: none at all when nothing waits. */
  function waitingBadge() {
    const total = Object.values(waiting).reduce((sum, n) => sum + n, 0);
    return total ? el("b", { class: "count", text: total > 99 ? "99+" : String(total) }) : null;
  }

  let polling = false;

  /** Ask how many notifications are waiting, and repaint the bells if that changed. */
  async function pollWaiting() {
    if (polling || document.visibilityState === "hidden") return;
    polling = true;
    try {
      const { wefts } = await providersApi.notificationCounts();
      setWaiting(wefts ?? {});
    } catch {
      // Quiet: the LED already says when the service is gone.
    } finally {
      polling = false;
    }
  }

  function setWaiting(next) {
    const key = (counts) => JSON.stringify(Object.entries(counts).sort());
    if (key(next) === key(waiting)) return;
    waiting = next;
    paintLed();
    renderPickButton();
    if (!pickList.hidden) buildPickMenu();
    drawerBody.querySelector("[data-screen=notifications] .count")?.remove();
    const badge = waitingBadge();
    if (badge) drawerBody.querySelector("[data-screen=notifications] > span")?.append(badge);
  }

  function choose(entry) {
    if (threadKey(entry) === threadKey(current)) {
      closePickMenu();
      return;
    }
    current = entry;
    rememberPick(entry);
    wearCurrent();
    renderPickButton();
    closePickMenu();
    showThread();
    announce(`Talking to ${entry.name}.`);
  }

  /* HOW MUCH OF A THREAD IS ACTUALLY ON THE GLASS.
   *
   * These are not settings and must never become any. The dial the user turns is
   * `window_turns`, which decides what the thread HOLDS — past it, turns are gone. What
   * is RENDERED is a different question with a different answer: a browser does not need
   * two hundred turns of bubbles in the document to show you the last three.
   *
   * So the log carries a tail, scrolling up asks for more of what is still held, and a
   * conversation that carries on afterwards slowly gives the extra rows back. Scroll
   * back five hundred turns, chat for a while, and the document quietly debloats to the
   * tail again without anything having been lost — because nothing here can lose
   * anything. Only threads.js trims the thread itself.
   */
  const RENDER_TAIL = 60;
  const RENDER_CHUNK = 40;

  /** Rows released per finished turn. The whole point is that it is a fade rather than
   *  a jump-cut: releasing the excess in one go is a visible lurch, and a lurch in a
   *  window somebody is reading looks exactly like a bug. */
  const DECAY_PER_TURN = 8;

  const NEAR_TOP = 120;
  const NEAR_BOTTOM = 80;

  /**
   * How many of the thread's NEWEST messages are on the glass.
   *
   * Counted from the end rather than as an index into the array, and that is the whole
   * trick: threads.js trims from the FRONT, so every index shifts under us at the end of
   * a turn while a count of the tail stays true through it without any bookkeeping.
   */
  let renderedCount = 0;

  function atBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM;
  }

  /** Repaint the log from the newest `renderedCount` messages of the current thread. */
  function paintThread() {
    const messages = threads.forPick(current);
    renderedCount = Math.min(Math.max(0, renderedCount), messages.length);

    log.replaceChildren();
    const pick = current;
    for (const message of messages.slice(messages.length - renderedCount)) {
      const role = message.role === "user" ? "user" : "ai";
      const bubble = addBubble(role, message.content, message.attachments, message.spoken);
      // Any reply in a thread with a voice can be heard again, on this device or any
      // other: the words are here, and the server keeps the clips. Hidden
      // by the stylesheet while the voice is off.
      if (role === "ai" && pick?.has_voice && speakableSegments(message.content).length) {
        attachSpeaker(bubble, { pick, words: () => message.content });
      }
    }
  }

  /**
   * Repaint the log with the picked thread's own exchange.
   *
   * Rebuilt from the message array rather than by stashing DOM nodes, so there is one
   * source of truth for what a thread contains — the same array that gets forwarded.
   * The cost is that an error notice shown inside a bubble does not survive a switch
   * away and back, which is the right thing to lose: it described a request, not a
   * conversation.
   */
  function showThread() {
    // A voice reading Iris's reply must not carry on over Juno's window — and the
    // queue behind it must stop making more, not just fall silent for one clip.
    hush();

    const messages = threads.forPick(current);
    renderedCount = Math.min(messages.length, RENDER_TAIL);
    paintThread();

    if (messages.length === 0) greet();
    log.scrollTop = log.scrollHeight;

    // First sight of a session-kept thread on this mount: read it from the store. Once
    // only; onVisible is what re-reads it later.
    syncSession(current);
  }

  /**
   * Fill a session-kept thread from its store, IN PLACE, and repaint if it is on screen.
   *
   * In place for threads.js's reason: the array identity is the contract, and the view
   * holds the same array this module hands out.
   *
   * The store is the truth for these threads — it is what the other devices wrote to —
   * so its tail replaces what this window holds rather than merging with it. Two guards
   * keep that from eating a turn: nothing is replaced while a reply is streaming, and a
   * read that started before a turn was sent is thrown away, because the store answered
   * a question about a thread that has since moved.
   */
  async function syncSession(pick, { force = false } = {}) {
    if (!pick || pick.history !== "session") return;
    const key = threadKey(pick);
    if (!force && fromStore.has(key)) return;
    fromStore.add(key);

    // Catch up when this window already holds the thread, read whole otherwise. The
    // store decrypts every event it sends back, so a long thread's tail costs it tens
    // of seconds and a new exchange costs it milliseconds. A changed window size needs
    // the whole tail, because a catch-up never reaches back for older turns.
    const held = storeHeld.get(key);
    const since = held && held.turns === pick.window_turns && storeSeq.has(key)
      ? storeSeq.get(key) : null;

    const before = turnsSent;
    let thread;
    try {
      thread = await providersApi.sessionThread(pick.id, since);
    } catch (err) {
      // Not remembered as read, so the next look tries again.
      fromStore.delete(key);
      if (threadKey(current) === key) {
        addBubble(
          "ai",
          `Could not read this thread from its session store: ${
            err.detail || err.message || "no answer"}`,
        );
      }
      return;
    }
    if (inFlight || turnsSent !== before) return;
    if (since !== null && thread.session_key !== held.sessionKey) {
      // The weft was moved to another session since the last read, so that seq numbers
      // somebody else's events. Read the new one whole.
      storeHeld.delete(key);
      return syncSession(pick, { force: true });
    }
    const entries = mergeStoreRead(since === null ? null : held.entries, thread, pick.window_turns);
    storeHeld.set(key, { entries, turns: pick.window_turns, sessionKey: thread.session_key });
    storeSeq.set(key, thread.last_seq);
    const fresh = entries.map((entry) => entry.message);

    const messages = threads.forPick(pick);
    const same = messages.length === fresh.length
      && messages.every((m, i) =>
        m.role === fresh[i].role && m.content === fresh[i].content
        && sameAttachments(m.attachments, fresh[i].attachments));
    if (same) return;

    messages.splice(0, messages.length, ...fresh);
    if (threadKey(current) === key) {
      renderedCount = Math.min(messages.length, RENDER_TAIL);
      paintThread();
      if (messages.length === 0) greet();
      log.scrollTop = log.scrollHeight;
    }
  }

  /** True while a reveal is repainting, because setting scrollTop fires `scroll`. */
  let revealing = false;

  /**
   * Put another chunk of the held thread back on the glass.
   *
   * The scroll position is restored by how much TALLER the log got, not to where it
   * was: rows were added above the reader, so holding `scrollTop` still would slide the
   * words they were looking at down the screen. This is the same arithmetic that makes
   * the decay below wait until they are at the bottom.
   */
  function revealEarlier() {
    const messages = threads.forPick(current);
    if (revealing || inFlight || renderedCount >= messages.length) return;

    revealing = true;
    const heightBefore = log.scrollHeight;
    const topBefore = log.scrollTop;

    renderedCount = Math.min(messages.length, renderedCount + RENDER_CHUNK);
    paintThread();

    log.scrollTop = topBefore + (log.scrollHeight - heightBefore);
    revealing = false;
  }

  /**
   * Give back a few rows, once a turn, and only to somebody who is not reading them.
   *
   * The `atBottom` guard is manners and mechanics at once. Removing nodes above the
   * viewport yanks `scrollTop` out from under whoever is scrolled up, so the rule that
   * keeps the decay polite is the same rule that keeps it from throwing the reader
   * somewhere they did not ask to be.
   *
   * Rows are removed from the top until the document matches the count, which also
   * sweeps up anything in the log that is NOT a message — the greeting, an error notice
   * from a turn that never landed. Those are oldest by definition and are exactly what
   * should go first.
   */
  function decayRendered() {
    // Clamped first: the save that precedes this call is what trims the thread, so the
    // count can be describing rows for messages that no longer exist.
    const messages = threads.forPick(current);
    renderedCount = Math.min(renderedCount, messages.length);

    if (renderedCount <= RENDER_TAIL || !atBottom()) return;

    renderedCount = Math.max(RENDER_TAIL, renderedCount - DECAY_PER_TURN);
    while (log.children.length > renderedCount) log.firstElementChild.remove();
  }

  log.addEventListener("scroll", () => {
    if (log.scrollTop < NEAR_TOP) revealEarlier();
  });

  function greet() {
    if (!current) {
      addBubble(
        "ai",
        "You are in. Pick a weft above and say something — the reply streams back as " +
          "it is written. If the menu is empty, the hamburger has a Wefts screen " +
          "waiting for its first name.",
      );
      return;
    }
    if (current.builtin) {
      addBubble("ai", ELIZA_GREETING);
      return;
    }
    // The last clause has to match what this pick is actually set to. It used to say
    // "nothing is kept once you reload" to everybody, which stopped being true the day
    // a pick could ask for a scrollback — a greeting is a small place to be wrong, but
    // it is the first sentence the window says about where somebody's words go.
    const kept = current.history === "local"
      ? "this one is kept on this device until you clear it."
      : current.history === "session"
        ? "this one is kept in its session store, so every device you open it on sees it."
        : "nothing here is kept once you reload.";
    addBubble(
      "ai",
      `This window is now pointed at ${current.name}. ` +
        `Each weft keeps its own thread — what you say here is not sent to any of the ` +
        `others, and ${kept}`,
    );
  }

  /** True while a ping is out, so a slow store never has two stacked up. */
  let pinging = false;

  /**
   * Ask the store whether the current thread has moved on, and re-read it if so.
   *
   * Quiet on failure: the status LED already says when the service is gone, and a store
   * that is down shows up properly on the next full read. A dozen "could not reach"
   * bubbles stacked one per ping would be noise about noise.
   */
  async function pingSession() {
    const pick = current;
    if (pinging || inFlight || !pick || pick.history !== "session") return;
    if (document.visibilityState !== "visible") return;
    const key = threadKey(pick);
    // Never read on this mount yet: that first read is showThread's, not ours.
    if (!storeSeq.has(key)) return;

    pinging = true;
    try {
      const head = await providersApi.sessionHead(pick.id);
      if (head.last_seq !== storeSeq.get(key) && threadKey(current) === key) {
        await syncSession(pick, { force: true });
      }
    } catch {
      /* see above */
    } finally {
      pinging = false;
    }
  }

  const pingTimer = setInterval(pingSession, SESSION_PING_MS);
  // Two windows side by side never change visibility, but focus moves between them.
  window.addEventListener("focus", pingSession);

  /**
   * Dress the window in the current pick's colours, or the theme's. setPair sets the
   * pair on :root — every bubble follows — and tints the donut's rings, which ease
   * into it rather than snapping. Called on every menu refresh as well as on a choice,
   * because the colours can change while the pick does not.
   */
  function wearCurrent() {
    const pair = pairOf(current);
    donut.setPair(pair);
    if (pair) document.documentElement.dataset.dressed = "";
    else document.documentElement.removeAttribute("data-dressed");
    const root = document.documentElement;
    if (root.hasAttribute("data-eliza") !== Boolean(current?.builtin)) {
      root.toggleAttribute("data-eliza", Boolean(current?.builtin));
      // Her floor is dark in either theme, and the ring's idle grey is judged on it.
      donut.refreshPalette();
    }
    wearPicture(current?.background ?? null);
  }

  /**
   * The current pick's picture, behind the ring.
   *
   * Fetched with the credential and handed to the donut as a blob URL, because the
   * pictures are served only to their owner and an <img src> carries no bearer. One
   * fetch per picture per view: the hash names the bytes, so a URL made once is good
   * for as long as the view lives, and switching back to a pick is instant. A failed
   * fetch is forgotten rather than cached, so the next choice of that pick tries again.
   */
  /** @type {Map<string, Promise<string|null>>} */
  const pictures = new Map();
  let pictureWanted = null;

  function pictureUrl(hash) {
    if (!pictures.has(hash)) {
      pictures.set(
        hash,
        providersApi
          .backgroundBlob(hash)
          .then((blob) => URL.createObjectURL(blob))
          .catch(() => {
            pictures.delete(hash);
            return null;
          }),
      );
    }
    return pictures.get(hash);
  }

  async function wearPicture(hash) {
    pictureWanted = hash;
    if (!hash) {
      donut.setBackdrop(null);
      return;
    }
    const url = await pictureUrl(hash);
    // The pick may have changed while the picture was on its way.
    if (pictureWanted === hash) donut.setBackdrop(url);
  }

  /** Off the stage and out of memory. Signing out must not leave a picture behind. */
  function releasePictures() {
    pictureWanted = null;
    donut.setBackdrop(null);
    for (const pending of pictures.values()) {
      pending.then((url) => url && URL.revokeObjectURL(url));
    }
    pictures.clear();
  }

  // ---- pictures on messages ----
  //
  // The same arrangement as the backgrounds above, keyed by hash: fetched with the
  // credential, shown from a blob: URL, cached for the life of the view and revoked
  // with it. A picture this window uploaded is seeded with the local URL of the file
  // that was chosen, so the bubble never fetches back what it just sent.

  /** @type {Map<string, Promise<string|null>>} */
  const attachmentPictures = new Map();

  function attachmentUrl(hash) {
    if (!attachmentPictures.has(hash)) {
      attachmentPictures.set(
        hash,
        providersApi
          .attachmentBlob(hash)
          .then((blob) => URL.createObjectURL(blob))
          .catch(() => {
            attachmentPictures.delete(hash);
            return null;
          }),
      );
    }
    return attachmentPictures.get(hash);
  }

  function sameAttachments(a = [], b = []) {
    return a.length === b.length && a.every((p, i) => p.hash === b[i].hash);
  }

  /** The row of pictures at the top of a bubble. Each opens full size on a tap. */
  function picturesFor(attached) {
    const row = el("div", { class: "bubble-pics" });
    for (const { hash, name } of attached) {
      const img = el("img", { alt: name || "picture", title: name || "picture" });
      const frame = el("button", {
        type: "button",
        class: "bubble-pic",
        "aria-label": `open ${name || "picture"}`,
        onclick: () => img.src && openLightbox(img.src, name),
      }, [img]);
      attachmentUrl(hash).then((url) => {
        if (url) img.src = url;
        else frame.classList.add("gone");
      });
      row.append(frame);
    }
    return row;
  }

  const lightbox = el("div", { id: "lightbox", hidden: "", onclick: () => closeLightbox() });

  function openLightbox(src, name) {
    lightbox.replaceChildren(el("img", { src, alt: name || "picture" }));
    lightbox.hidden = false;
  }

  function closeLightbox() {
    lightbox.hidden = true;
    lightbox.replaceChildren();
  }

  function onLightboxKey(event) {
    if (event.key === "Escape" && !lightbox.hidden) closeLightbox();
  }
  document.addEventListener("keydown", onLightboxKey);

  function releaseAttachmentPictures() {
    for (const pending of attachmentPictures.values()) {
      pending.then((url) => url && URL.revokeObjectURL(url));
    }
    attachmentPictures.clear();
  }

  /**
   * What a weft can do, as the quiet row of icons the bar and the menu both show. A weft
   * with no engine can do none of it, so it shows one ⊘ instead: narrower than the words
   * "no engine", which were what pushed the ⚙ off a phone's bar.
   */
  function capsOf(entry) {
    const bound = answers(entry);
    const marks = entry && !bound
      ? [icon("no-entry", { label: "no engine yet" })]
      : capabilitiesOf(entry, { hears: hears(entry) })
        .map((cap) => icon(cap.icon, { label: cap.label }));
    return el("span", { class: "pick-caps" }, marks);
  }

  /** Paint the whole Pick bar from `current`: name, title, capabilities and ⚙. */
  function renderPickButton() {
    const bound = answers(current);
    pickName.textContent = current ? current.name : "pick";
    pickButton.classList.toggle("chosen", Boolean(current));
    pickButton.title = !current
      ? "choose a weft"
      : current.builtin
        ? `${current.name}: 1966, no engine needed`
        : bound
        ? `${current.name}: ${current.model_id} on ${current.provider_name}`
        : `${current.name}: no engine yet`;
    pickCaps.replaceChildren(...capsOf(current).childNodes);
    shelf.show("ptt", hears(current));
    // The bar's pea wears the ring too, only to be seen: the bell to press is the LED.
    pickLed.classList.toggle("waiting", Boolean(current && waiting[current.id] > 0));
    pickGear.hidden = !current;
    const settings = current ? `${current.name}'s settings` : "";
    pickGear.title = settings;
    pickGear.setAttribute("aria-label", settings);
  }

  /**
   * Refresh the menu from the server, and drop a remembered pick that no longer
   * exists. Called on mount and whenever the settings screens change something.
   */
  async function loadPicks() {
    try {
      // The tips too, because whether she is on the menu is a find. Without waiting, a
      // remembered pick of her would be dropped on every reload, before they arrived.
      const [list] = await Promise.all([providersApi.listWefts(), tutor.whenLoaded]);
      picks = [...list, ...(elizaShown() ? [ELIZA] : [])];
    } catch {
      // A failed refresh should not blank a working menu; the session module already
      // handles the only failure that matters (an expired session), by logging out.
      return;
    }

    // Turning a pick back to `memory` is the gesture that means "forget this device's
    // copy", and this is the only code that can honour it: the setting is saved from a
    // panel that has no access to storage, and the server has no reach into this
    // browser at all. Idempotent, so running it on every menu refresh costs nothing.
    for (const entry of picks) {
      if (entry.history !== "local") threads.forget(entry);
      // A thread that stopped being session-kept must be read again if it starts again.
      if (entry.history !== "session") {
        fromStore.delete(threadKey(entry));
        storeHeld.delete(threadKey(entry));
      }
    }

    const before = threadKey(current);

    const remembered = current ?? rememberedPick();
    current = remembered?.id ? picks.find((p) => p.id === remembered.id) ?? null : null;

    if (!current) rememberPick(null);

    wearCurrent();
    renderPickButton();
    showMute();

    // Only when the thread actually moved. This also runs after the settings screens
    // change something, and repainting then would throw away the exchange on screen
    // for no reason.
    if (threadKey(current) !== before) showThread();
    // Same thread, but it may have just been switched to a session store on the
    // weft page. A no-op for one already read on this mount.
    else syncSession(current);
    if (!pickList.hidden) buildPickMenu();
    if (drawer.classList.contains("open")) buildDrawer();
    // The account is known now, so the tips can judge which of them apply.
    tutor.setReady();
    tutor.visit();
  }

  pickButton.addEventListener("click", () => {
    if (pickList.hidden) openPickMenu();
    else closePickMenu();
  });

  pickList.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closePickMenu();
    pickButton.focus();
  });

  // ---- the log ----
  /**
   * A bubble's words live in a child span, not in the bubble itself.
   *
   * The bubble also holds things that are NOT the words — the speaker button, an error
   * notice — and those now arrive DURING the stream, because the first clip is ready
   * long before the last token. Writing `bubble.textContent` on the next delta would
   * delete them. One span keeps the text and the furniture from fighting.
   */
  function addBubble(role, text, attached = [], spoken = false) {
    const span = el("span", { class: "bubble-text" });
    const bubble = el("div", { class: spoken ? "bubble spoken" : "bubble" }, [span]);
    if (spoken) {
      bubble.title = "spoken";
      bubble.append(el("span", { class: "spoken-mark", "aria-hidden": "true" }, [icon("mic")]));
    }
    if (attached?.length) bubble.prepend(picturesFor(attached));
    if (role === "ai") setBubbleText(bubble, text);
    else span.textContent = text;
    // Who said it, for a screen reader moving through the log; the eye has the colours
    // and the sides for that.
    const who = role === "user" ? "You" : current?.name ?? "Weft";
    bubble.prepend(el("span", { class: "sr-only", text: `${who}: ` }));
    log.append(el("div", { class: `row ${role}` }, [bubble]));
    log.scrollTop = log.scrollHeight;
    return bubble;
  }

  /**
   * A weft's words, with any pictures they reference shown under them.
   *
   * Called on every delta, so the row of pictures is only rebuilt when the set of
   * references changes, not each time a token lands.
   */
  function setBubbleText(bubble, text, { streaming = false } = {}) {
    const shown = picturesIn(text, { streaming });
    const span = bubble.querySelector(".bubble-text");
    span.textContent = shown.text;

    const row = bubble.querySelector(".bubble-shown");
    if (sameAttachments(bubble.shown, shown.pictures)) return;
    bubble.shown = shown.pictures;
    row?.remove();
    if (shown.pictures.length) {
      const pics = picturesFor(shown.pictures);
      pics.classList.add("bubble-shown");
      span.after(pics);
    }
  }

  /**
   * THIS FUNCTION DOES NOT TOUCH FOCUS, and that is the whole fix.
   *
   * It used to do two things that each opened the soft keyboard on their own. It
   * disabled the textarea, which blurs a focused field and then invites the focus back
   * when re-enabled; and it called `input.focus()` when the reply's text finished — by
   * which point the voice is still speaking for another twenty seconds, so the keyboard
   * reappeared in the middle of what the user was watching.
   *
   * A first attempt kept the focus call for desktop and skipped it on a soft-keyboard
   * device, detected with `matchMedia("(pointer: coarse)")`. That query reported the
   * wrong answer inside the installed PWA on a real phone, so the bug survived a fix
   * that was correct in principle. There is no third guess: nothing here focuses
   * anything, on any device. **The keyboard belongs to whoever tapped the box.**
   *
   * The textarea also stays enabled — `inFlight` already refuses a second send, so the
   * disabled attribute was doing that job twice at the cost of making the keyboard
   * flap. The send button still greys out, so there is something visibly saying "not
   * yet"; it simply is not the thing holding the caret.
   *
   * Greyed out, not disabled: a disabled button receives no pointer events, and the
   * send shelf opens and closes from a hold on this button even while a reply streams.
   * The submit handler's `inFlight` check is what actually refuses the tap.
   */
  function setBusy(busy) {
    inFlight = busy;
    sendButton.setAttribute("aria-disabled", String(busy));
  }

  function streamError(bubble, message) {
    // Inside the bubble, not as a separate row: the failure belongs to the reply it
    // interrupted, and a user who scrolled away should not have to correlate two
    // things that arrived a second apart.
    bubble.append(el("div", { class: "stream-error", text: message }));
    announce(message);
    log.scrollTop = log.scrollHeight;
  }

  /** A dimmer note than streamError: the words arrived, only the voice did not. */
  function voiceNote(bubble, message) {
    bubble.append(el("div", { class: "voice-note", text: message }));
    log.scrollTop = log.scrollHeight;
  }

  /**
   * Hang a speaker on a bubble, so its reply can be heard again.
   *
   * Two ways to play it, cheapest first:
   *
   *   - The clips this device already holds: the live reply's (`live.clips`), or the
   *     ones a previous replay made. Played straight from their blobs, nothing fetched.
   *   - The words. Cut exactly as the live stream cut them, and each
   *     segment asked for again. The server keeps clips by voice and words, so what was
   *     already made comes back without being bought again, and anything that was not
   *     (expired, made on no device yet, or failed the first time) is made now. That is
   *     replay after a reload, replay on another device, and filling in a paragraph the
   *     speech provider dropped, all one path.
   *
   * A live reply with a failed segment goes the second way once its words have finished
   * arriving, so a tap fills in the gap rather than replaying it.
   */
  function attachSpeaker(bubble, { pick, words, live = null }) {
    if (bubble.querySelector(".speaker")) return;

    /** Clips a replay from the words made, kept so the next tap here costs nothing. */
    let heard = null;

    const button = el("button", {
      class: "speaker",
      type: "button",
      "aria-label": "play this reply",
      title: "play this reply",
      onclick: () => {
        if (voice.speaking() || replaying) {
          hush();
          return;
        }
        // Muted means quiet, even for a tap that might have been an accident.
        if (speech.isMuted()) {
          nudgeMute();
          return;
        }
        // A reply the browser held back carries on from where it stopped, inside this
        // tap. Its clips kept being made while it waited, so that is the whole reply.
        if (live?.queue && live.queue === speaker && live.queue.blocked) {
          donut.setMode("speaking");
          live.queue.resume();
          audiodebug.log("replay: resumed held-back reply", { gesture: audiodebug.gesture() });
          return;
        }
        // `live.clips` is live: a reply still being spoken keeps growing, and pressing
        // the button mid-reply replays what exists so far rather than a stale snapshot.
        // A replay is the voice in the room as much as a reply is, so the ring dances
        // to it, and settles when it ends or is stopped.
        // Unless they were cut off: a reply interrupted while it was still being
        // voiced only has its first paragraphs, and replaying those looks like the
        // browser refusing the rest. Those are said again from the words.
        const held = heard ??
          (live && !live.cut && (!live.failed || !live.finished) ? live.clips : null);
        // A re-speak fetches before it plays, which outlasts the tap.
        if (!held) wakeVoice("replay", pick);
        const token = {};
        replaying = token;
        donut.setMode("speaking");
        const playing = held
          ? voice.playSequence([...held])
          : replayWords(pick, bubble, words(), (made) => { heard = made; });
        audiodebug.log("replay: tapped", { clips: held ? held.length : "re-speak", gesture: audiodebug.gesture() });
        playing.catch((err) => {
          audiodebug.log("replay: refused", { error: err?.name });
          /* Refused again. Pressing the button IS a gesture, so this is rare — a device
             with audio switched off outright, most likely. */
        }).finally(() => {
          if (replaying !== token) return;
          replaying = null;
          settleDonut();
        });
      },
    }, [icon("voice")]);
    bubble.append(button);
  }

  /**
   * Say a reply again from its words, through the same queue a live reply uses: the
   * next segment is being fetched while this one plays, and a failed one does not stop
   * the rest.
   *
   * `onWhole` gets the clips when every segment came back, so the bubble can keep them.
   * A reply with a gap is not kept: the next tap should try the gap again.
   *
   * Resolves when the queue drains or is refused. A replay that is stopped never
   * resolves, which is harmless: hush() has already settled everything this would.
   */
  function replayWords(pick, bubble, words, onWhole) {
    const segments = speakableSegments(words);
    const made = [];
    let failed = false;

    return new Promise((resolve) => {
      const queue = createSpeaker({
        clipFor: ({ text }) => voice.clipFor({ weftId: pick.id, text }),
        playToEnd: (url) => {
          donut.setMode("speaking");
          return voice.playToEnd(url);
        },
        stop: () => voice.stop(),
      }, {
        onClip: (url) => made.push(url),
        onError: (err) => {
          if (!failed) {
            voiceNote(bubble, err.detail || err.message || "Part of this could not be spoken.");
          }
          failed = true;
        },
        onBlocked: (err) => {
          audiodebug.log("re-speak: held back", { error: err?.name });
          if (replayer === queue) replayer = null;
          resolve();
        },
        onDrain: () => {
          if (replayer === queue) replayer = null;
          if (!failed && made.length === segments.length) onWhole(made);
          resolve();
        },
      });
      replayer = queue;
      for (const segment of segments) queue.say(segment);
      // Nothing to say: drain will never fire for a queue that was never fed.
      if (!segments.length) resolve();
    });
  }

  /**
   * Start reading a reply aloud while it is still being written.
   *
   * Returns a `say(text)` to feed with finished segments, or null when this thread is
   * silent. The clips belong to the bubble, so the speaker button can replay the whole
   * reply afterwards without paying for a second synthesis.
   */
  function startSpeaking(pick, bubble, words) {
    if (!pick?.has_voice || !speech.isVoiceOn()) return null;

    /** The reply's clips as they arrive, and whether any segment failed. `finished` is
     *  set by send() when the words stop arriving; see attachSpeaker. */
    const live = { clips: [], failed: false, finished: false, cut: false, queue: null };
    bubble.live = live;
    let noted = false;

    speaker = createSpeaker({
      // The queue does not know about picks or providers; it is handed a voice that
      // already knows which thread it is speaking for.
      clipFor: ({ text }) => voice.clipFor({ weftId: pick.id, text }),
      // Muted, a clip is still made and kept for the bubble, and skipped here, so the
      // queue keeps pace with the reply and unmuting picks up at the next paragraph.
      // The ring lights up whenever a clip actually plays: sound in the room is what it
      // dances to, so it comes back with the voice even after the words have finished.
      playToEnd: (url) => {
        if (speech.isMuted()) return Promise.resolve();
        donut.setMode("speaking");
        return voice.playToEnd(url);
      },
      stop: () => voice.stop(),
    }, {
      onClip: (url) => {
        live.clips.push(url);
        // On the first clip, so a reply can be replayed the moment any of it exists.
        attachSpeaker(bubble, { pick, words, live });
      },
      onError: (err) => {
        // A speaker even when nothing came back at all, because a tap is now how the
        // gap gets filled in.
        live.failed = true;
        attachSpeaker(bubble, { pick, words, live });
        // Once per reply. A provider that is down will fail on every paragraph, and
        // twelve identical notices under one bubble helps nobody.
        if (noted) return;
        noted = true;
        voiceNote(bubble, err.detail || err.message || "Part of this could not be spoken.");
      },
      onBlocked: (err) => {
        audiodebug.log("reply: held back", { error: err?.name });
        if (noted) return;
        noted = true;
        voiceNote(bubble, "Tap the speaker to hear this — your browser held the audio back.");
      },
      // The words finish well before the voice does. Hold the donut until the room is
      // actually quiet, or it stops mid-sentence.
      // Not `setMode("idle")`. The queue draining means the voice has caught up, which
      // happens mid-reply whenever the model pauses — it is a question, not an answer.
      onDrain: settleDonut,
      // Stopped with work outstanding, or before the words had all arrived: its clips
      // are only part of the reply.
      onCancel: (unfinished) => {
        if (unfinished || !live.finished) live.cut = true;
      },
    });
    live.queue = speaker;

    return (text) => {
      const spoken = forSpeech(text);
      if (!spoken) return;
      // Re-assert it. After the voice has caught up and gone quiet, the next paragraph
      // has to bring the ring back — the mode was only ever set once, on the first
      // token, which is why a pause used to end the animation for the whole reply.
      if (!speech.isMuted()) donut.setMode("speaking");
      speaker?.say(spoken);
    };
  }

  /**
   * The tail of a thread that goes upstream, in turns rather than messages.
   *
   * 0 means "no cap beyond the window", which is what the old `everything` became once
   * the window got a size: the whole thread stopped being a thing anyone could send,
   * because turns that have fallen out of the window are gone from the array too. The
   * honest ceiling is whatever is still held, and that is what this returns.
   */
  function sendSlice(messages, turns) {
    const n = Number(turns);
    if (!Number.isFinite(n) || n <= 0) return messages;
    return messages.slice(-(n * 2 - 1));
  }

  /**
   * One turn: post the conversation down the picked thread, stream the answer in.
   *
   * The donut's three modes map onto the three states of a request — waiting for the
   * first token, speaking while they arrive, idle when it ends. `speaking` is set on
   * the FIRST delta rather than when the request is sent, so the heartbeat lasts
   * exactly as long as the model is actually thinking.
   */
  async function send(text, attached = [], spoken = false) {
    if (!current) {
      addBubble(
        "ai",
        "Nothing is picked yet, so there is nowhere to send that. Choose a thread at " +
          "the top of the window — or open the menu and add a provider, if the list " +
          "is empty.",
      );
      return;
    }

    if (!answers(current)) {
      addBubble(
        "ai",
        `${current.name} has no engine yet, so there is nobody to answer. Give it one on ` +
          "its page — the ⚙ beside it on the Pick menu.",
      );
      return;
    }

    // Bound once, here. Everything below belongs to the thread this turn started in,
    // even if the user changes the pick while the reply is still streaming — including
    // which voice reads it out.
    const pick = current;
    const messages = threads.forPick(pick);
    turnsSent += 1;

    addBubble("user", text, attached, spoken);
    messages.push({
      role: "user",
      content: text,
      ...(attached.length ? { attachments: attached } : {}),
      ...(spoken ? { spoken: true } : {}),
    });
    renderedCount += 1;

    // A new turn silences the previous one. Talking over yourself is worse than
    // losing the tail of a reply you have already read.
    hush();

    setBusy(true);
    streamingText = true;
    donut.setMode("waiting");

    const bubble = addBubble("ai", "");
    // Counted now rather than when the reply lands, because the bubble is on the glass
    // now. A turn that fails leaves the count one ahead of the array, and the decay
    // below reconciles that by dropping the oldest row — which is the right correction,
    // since the row it is one ahead BY is an error notice nobody needs to keep.
    renderedCount += 1;
    let reply = "";
    // How the words reach the glass (comfort.js). The reply arrives at full speed
    // whatever this is; only the painting changes. Full: every delta. Calm: twice a
    // second. Still: a paragraph at a time, so nothing on screen moves mid-sentence.
    const pace = comfort.current().motion;
    let paintTimer = null;
    let paintedUpTo = 0;
    const paintReply = (text) => {
      setBubbleText(bubble, text, { streaming: true });
      log.scrollTop = log.scrollHeight;
    };
    const say = startSpeaking(pick, bubble, () => reply);
    const segmenter = say ? createSegmenter() : null;
    let speaking = false;

    try {
      for await (const event of streamChat({
        // `pick`, not `current`: this turn belongs to the thread it started in, even if
        // the menu moves while the reply is still streaming.
        weftId: pick.id,
        // What is SHOWN and what is SENT are the same array until here, and this is the
        // one line where they part.
        //
        // `send_turns` is a count of turns, and the newest turn has no reply yet — the
        // user's message was pushed a few lines above and the answer is what this call
        // is for. So n turns is 2n-1 messages, which at n=1 is the single message just
        // typed: the setting for a gateway that holds the session itself and discards
        // whatever history it is handed, as the bridge and hermes both do. Saying that
        // on the wire beats uploading the whole transcript every turn for the far end
        // to parse and bin, and beats relying on its good manners if it does not.
        messages: sendSlice(messages, pick.send_turns),
      })) {
        if (event.error) {
          streamError(bubble, event.error);
          continue;
        }
        if (!event.delta) continue;

        if (!speaking) {
          speaking = true;
          donut.setMode("speaking");
        }
        reply += event.delta;
        if (pace === "full") {
          paintReply(reply);
        } else if (pace === "calm") {
          paintTimer ??= setTimeout(() => {
            paintTimer = null;
            paintReply(reply);
          }, 500);
        } else {
          const cut = reply.lastIndexOf("\n\n");
          if (cut > paintedUpTo) {
            paintedUpTo = cut;
            paintReply(reply.slice(0, cut));
          }
        }

        // Each completed paragraph goes to the voice the moment it is complete, so the
        // first one is being read aloud while the model is still writing the second.
        if (segmenter) for (const segment of segmenter.push(event.delta)) say(segment);
      }

      // Whatever the last paragraph was, finished or not — it is still something the
      // model said.
      if (segmenter) for (const segment of segmenter.flush()) say(segment);

      // Only a reply that actually arrived becomes context. Recording an empty turn
      // would teach the next request that the model answered with silence.
      if (reply) {
        // Once more as a finished reply, so the tail held back while streaming (the
        // start of what might have become a picture reference) is shown after all.
        setBubbleText(bubble, reply);
        // Calm and still may have painted less than all of it until now.
        if (pace !== "full") log.scrollTop = log.scrollHeight;
        // Whole, once: see `announce`.
        announce(`${pick.name}: ${reply}`);
        messages.push({ role: "assistant", content: reply });
      }

    } catch (err) {
      // A failure BEFORE the first token — the server could still use a status code,
      // so this is a real error with the provider's own words in it.
      streamError(bubble, err.detail || err.message || "That provider could not be reached.");
      // The user's message never got an answer, so it must not sit in the context as
      // though it did; otherwise a retry sends the same turn twice.
      messages.pop();
    } finally {
      // A calm repaint still waiting would otherwise land after the finished text.
      clearTimeout(paintTimer);
      // Once per turn, never per token: a storage write is synchronous on the main
      // thread and one per frame would stutter the canvas drawing the reply. Here in
      // the finally rather than after the push, so a turn that failed is committed in
      // its corrected state too — the unanswered message has already been popped.
      threads.save(pick);

      // After the save, because that is what trims the thread to its window — the log
      // can only be measured against the array once the array is the size it is going
      // to be. Guarded on the pick still being the one on screen: a turn that finished
      // after the user moved on belongs to a thread nobody is looking at.
      if (threadKey(current) === threadKey(pick)) decayRendered();

      // The words are done; the voice may not be. settleDonut knows the difference, and
      // the speaker's onDrain asks it again when the last clip finishes.
      streamingText = false;
      if (bubble.live) bubble.live.finished = true;
      settleDonut();
      setBusy(false);
    }
  }

  bar.addEventListener("submit", (event) => {
    event.preventDefault();
    submitMessage(input.value.trim(), spokenDraft);
  });

  /** Send what is in the box (or `value` in its place), with the tray's pictures. */
  function submitMessage(value, spoken) {
    if (inFlight) return;
    // A picture still uploading would be left behind by a send now; it is quicker to
    // wait the moment it takes than to explain afterwards where it went.
    if (tray.uploading) return;
    if (!value && !tray.ready) return;
    const attached = tray.take();
    input.value = "";
    input.style.height = "auto";
    spokenDraft = false;

    // Unconditional, for the same reason nothing refocuses: a device test that can be
    // wrong is worse than no device test. On a phone this is the keyboard getting out
    // of the way of a reply that is about to be watched and listened to; on a desktop
    // it costs one click back into the box, which is a fair price for behaviour that is
    // the same everywhere and cannot be wrong about where it is running.
    input.blur();

    // Still inside the tap (or the key) that sent it; the reply's first clip is not.
    // A spoken send arrives after transcription, outside any tap: push-to-talk wakes
    // the voice when it is let go of instead.
    wakeVoice("send");
    send(value, attached, spoken);
  }

  // Auto-grow: the box expands over the scene, and the log's edge follows the bar (the
  // observer below) so bubbles clear it instead of hiding behind it. The canvas never
  // moves.
  input.addEventListener("input", () => {
    // Emptied by hand: whatever is typed next was not spoken.
    if (!input.value.trim()) spokenDraft = false;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  });

  // The log sits between the two bars, so it needs their real heights: the input bar
  // grows with its text, the top bar with the safe area and the font. Measured, not
  // guessed, which is what the old fixed bottom padding got wrong by a bubble's edge.
  // A reader at the newest bubble stays there as the log shrinks under them.
  const barHeights = new ResizeObserver(() => {
    const stuck = atBottom();
    const rootStyle = document.documentElement.style;
    rootStyle.setProperty("--top-h", `${topbar.offsetHeight}px`);
    rootStyle.setProperty("--bar-h", `${bar.offsetHeight}px`);
    if (stuck) log.scrollTop = log.scrollHeight;
  });
  barHeights.observe(topbar);
  barHeights.observe(bar);

  root.append(canvasWrap, log, bar, topbar, scrim, drawer, lightbox, announcer);

  /**
   * A pick's settings are furniture: they live on the server and follow the user to
   * whatever device they open next. So this window can be holding a stale answer
   * through no fault of its own — the phone changed it an hour ago.
   *
   * Re-reading on focus is what closes that. It costs one indexed join and no upstream
   * call (see the pick_menu docstring), which is cheap enough to do on a maybe.
   */
  function onVisible() {
    if (document.visibilityState !== "visible") return;
    loadPicks();
    loadStt();
    health?.check();
    pollWaiting();
    // The other half of the session store: what the phone said while this was hidden.
    syncSession(current, { force: true });
  }
  document.addEventListener("visibilitychange", onVisible);

  // The browser knows before we do when the network itself went away.
  function onNetworkChange() {
    health?.check();
  }
  window.addEventListener("online", onNetworkChange);
  window.addEventListener("offline", onNetworkChange);
  health = watchHealth(showHealth);

  donut.start();
  // The donut asks, once a frame, whether anything is speaking. It holds no
  // AudioContext and cannot make a sound — the graph stays inside speech.js, next to
  // the <audio> element that owns it.
  donut.setLevelSource(() => voice.loudness());
  renderPickButton();
  loadPicks();
  loadStt();
  pollWaiting();
  const notificationsTimer = setInterval(pollWaiting, NOTIFICATIONS_POLL_MS);
  window.addEventListener("focus", pollWaiting);

  // A canvas samples CSS variables once; the stylesheet repaints itself and the canvas
  // does not. Re-sample on every theme change, including the OS flipping at dusk.
  unsubscribeTheme = theme.subscribe(() => {
    donut.refreshPalette();
    showTheme();
  });

  // Calm or still: the ring holds still and tells the state by colour (comfort.js).
  donut.setMotion(comfort.current().motion);
  const unwatchComfort = comfort.subscribe((state) => donut.setMotion(state.motion));

  unsubscribeSession = subscribe((state) => {
    if (state.status !== "active") return;
    if (drawer.classList.contains("open")) buildDrawer();
  });

  greet();

  return function teardown() {
    clearInterval(pingTimer);
    clearInterval(notificationsTimer);
    window.removeEventListener("focus", pollWaiting);
    window.removeEventListener("focus", pingSession);
    donut.stop();
    donut.setLevelSource(null);
    shelf.teardown();
    ptt.teardown();
    window.removeEventListener("weft:stt-changed", loadStt);
    tray.teardown();
    releaseAttachmentPictures();
    document.removeEventListener("keydown", onLightboxKey);
    document.removeEventListener("keydown", onDrawerKey);
    barHeights.disconnect();
    document.documentElement.style.removeProperty("--top-h");
    document.documentElement.style.removeProperty("--bar-h");
    releasePictures();
    // Stops any audio and revokes every blob URL. Without the revoke, a long session
    // holds every reply it ever spoke.
    hush();
    voice.release();
    // Every thread goes with the view. Signing out must not leave one user's context
    // sitting in memory for whoever logs in next on this device.
    threads.clear();
    // Panels live outside this view's root, so they have to be closed explicitly.
    // Nothing else can: they are only ever opened from here.
    panels.popAll();
    unwatchPanels();
    tutor.teardown();
    clearTimeout(slipTimer);
    closePickMenu();
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("online", onNetworkChange);
    window.removeEventListener("offline", onNetworkChange);
    health?.stop();
    health = null;
    unsubscribeTheme?.();
    unsubscribeSession?.();
    unwatchComfort();
    document.documentElement.removeAttribute("data-dressed");
    document.documentElement.removeAttribute("data-eliza");
    document.documentElement.style.removeProperty("--weft-a");
    document.documentElement.style.removeProperty("--weft-b");
  };
}
