/* Weft — the voice.
 *
 * Fetches a spoken reply and plays it. Small, but there are four things in here that
 * are easy to get wrong and unpleasant to debug:
 *
 * **One voice at a time.** Starting a clip stops whatever was speaking. Without that,
 * sending a second message while the first reply is still being read gives you two
 * overlapping voices and no way to silence either.
 *
 * **Object URLs are revoked.** Every clip is a blob URL, and a blob URL pins its blob in
 * memory until it is revoked or the document goes away. A long session with a chatty
 * weft would otherwise hold every reply it ever spoke. `release()` is called by the
 * view's teardown and when a thread is repainted.
 *
 * **Autoplay can be refused, and that is normal.** Browsers block audio that did not
 * follow a user gesture, and mobile Safari is strictest. `play()` returns a promise that
 * rejects with NotAllowedError; the caller turns that into "tap to hear" rather than
 * into an error, because nothing is actually broken.
 *
 * **The voice and the mute are per device, and they are two settings**.
 * They live in localStorage next to the remembered pick: a property of where you are
 * sitting, not of your account. Muting on your phone at night should not silence the
 * desktop.
 *   - Voice off (the drawer's switch): nothing is spoken and nothing is sent to a speech
 *     provider. No voice, no cost.
 *   - Muted (the top bar): replies are still voiced, so every bubble can be heard later,
 *     but nothing plays aloud until you unmute. Quiet for now, sound later.
 *
 * What this deliberately does NOT do yet: start speaking before the reply has finished.
 * Doing that properly means detecting sentence or paragraph boundaries in the stream and
 * pipelining clips so the voice keeps up with the text. That is the right end state and
 * it is separate work — this waits for the whole reply, which is enough to find out
 * whether the room sounds better with voices in it.
 */

import * as debug from "./audiodebug.js";
import { apiBlob } from "./session.js";

const VOICE_OFF_KEY = "weft.voice-off";
const MUTED_KEY = "weft.muted";
/** Before the two were split there was one switch, stored here, and it meant voice off. */
const LEGACY_KEY = "weft.mute";

// Storage throws outright in a private window. A session that cannot remember a setting
// should still make sound, not fall over.
function flag(key) {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function setFlag(key, on) {
  try {
    if (on) localStorage.setItem(key, "1");
    else localStorage.removeItem(key);
  } catch {
    /* no persistence available; the setting still holds for this page */
  }
}

export function isVoiceOn() {
  return !flag(VOICE_OFF_KEY) && !flag(LEGACY_KEY);
}

export function setVoiceOn(on) {
  setFlag(VOICE_OFF_KEY, !on);
  setFlag(LEGACY_KEY, false);
}

export function isMuted() {
  return flag(MUTED_KEY);
}

export function setMuted(muted) {
  setFlag(MUTED_KEY, muted);
}

/* ---------------------------------------------------------------------------
 * The analyser graph — one per page, shared by every clip.
 *
 * THE ONE LINE THAT MATTERS: `node.connect(ctx.destination)`. Routing a media element
 * through WebAudio takes its audio OUT of the normal output path. Without that
 * connection the element plays perfectly, reports no error, fires `ended` on schedule,
 * and is completely silent. It is the single worst failure available here, so the graph
 * is built destination-first and nothing is ever captured before it exists.
 *
 * Every failure path below ends with "play it normally, without analysis". A donut that
 * does not wibble is a disappointment; audio that does not play is a broken product.
 *
 * One AudioContext, not one per clip: browsers cap how many a page may hold (Safari is
 * strictest), and a new one per reply would hit that within a conversation.
 * ------------------------------------------------------------------------- */

let audioCtx = null;
let analyser = null;
/** Reused between frames — see loudness() for why this is not allocated per read. */
let samples = null;
/** Elements already captured. An element may be captured exactly once, ever. */
const captured = new WeakSet();
/** Set once we know analysis is impossible here, so we stop trying every clip. */
let analysisImpossible = false;

/**
 * LETTING GO OF THE AUDIO THREAD.
 *
 * The graph below is built once and connected to `destination`, which means that from
 * the first spoken reply until the tab closes the page holds a RUNNING AudioContext —
 * a real-time, high-priority audio thread — through hours of silence. Nothing ever
 * suspended it: `release()` stopped playback and revoked blobs, and left the context
 * running.
 *
 * That is the best candidate for the mobile symptom: TTS with the screen off
 * sounding like "background struggle" while still playing. A silent-but-running context
 * is precisely the thing that competes with the clip it exists to analyse.
 *
 * The grace period is what makes this safe to call often. `playSequence` awaits each
 * clip, so there is a real gap between them, and suspending into every gap would
 * thrash the context — and a resume is not instant, so the thrash would be audible.
 * Two seconds is far longer than any inter-clip gap and far shorter than a pause worth
 * holding a thread through.
 *
 * BUT NOT WHILE A REPLY IS STILL GOING. In a live reply the gap between
 * paragraphs is the model writing and the server synthesising, which is often more
 * than two seconds. Suspending into it re-armed iOS's autoplay gate mid-reply: the next
 * paragraph had no tap behind it and no running context, and was refused. So the view
 * says when it is still busy (`createVoice({ holding })`), and the timer re-arms itself
 * instead of suspending until it is not.
 */
const SUSPEND_GRACE_MS = 2000;
let suspendTimer = null;
/** Whether the view still has a reply in flight. Set by createVoice. */
let holding = () => false;

function cancelSuspend() {
  if (suspendTimer === null) return;
  clearTimeout(suspendTimer);
  suspendTimer = null;
}

/** Hand the audio thread back, unless something starts speaking first. */
function suspendWhenQuiet() {
  cancelSuspend();
  suspendTimer = setTimeout(() => {
    suspendTimer = null;
    // Re-checked at fire time rather than trusted from when it was scheduled: a clip
    // may have started and finished inside the grace period.
    if (!audioCtx || audioCtx.state !== "running") return;
    if (holding()) {
      suspendWhenQuiet();
      return;
    }
    debug.log("ctx: suspending after quiet");
    audioCtx.suspend().catch(() => {});
  }, SUSPEND_GRACE_MS);
}

/**
 * Ask a context that is not running to run, and say how it went. Null when there is
 * nothing to ask. Never rejects: a refusal is logged, and the clip is tried anyway.
 *
 * `interrupted` is iOS's own state — a call, another app, the microphone — and
 * resume() without a tap is refused with InvalidStateError. With a tap it comes back.
 */
function wakeContext(label) {
  if (!audioCtx || audioCtx.state === "running" || audioCtx.state === "closed") return null;
  return audioCtx.resume().then(
    () => debug.log(`${label}: ctx resume ok`, { state: audioCtx.state }),
    (err) => debug.log(`${label}: ctx resume refused`, { error: err?.name, state: audioCtx.state }),
  );
}

/**
 * Tell iOS this page plays sound out loud.
 *
 * The clips go through Web Audio for the donut's sake, and Web Audio on iOS defaults to
 * the "ambient" session, which the ring/silent switch mutes. A voice reply is not
 * ambient. Safari 17+ has `navigator.audioSession`; elsewhere this does nothing.
 */
function claimSession() {
  const session = navigator.audioSession;
  if (!session || session.type === "playback") return;
  try {
    session.type = "playback";
    debug.log("session: playback");
  } catch (err) {
    debug.log("session: refused", { error: err?.name });
  }
}

/** A twentieth of a second of silence, as a WAV blob URL — what a tap plays to unlock. */
let silenceUrl = null;
function silence() {
  if (silenceUrl) return silenceUrl;
  const rate = 8000;
  const bytes = (rate / 20) * 2;
  const view = new DataView(new ArrayBuffer(44 + bytes));
  const text = (at, value) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(at + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // bytes per second
  view.setUint16(32, 2, true); // bytes per frame
  view.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  view.setUint32(40, bytes, true);
  silenceUrl = URL.createObjectURL(new Blob([view.buffer], { type: "audio/wav" }));
  return silenceUrl;
}

/** Whether the page is inside a tap right now. Unknown counts as yes, which is how this
 *  behaved before anybody asked. */
function inGesture() {
  return navigator.userActivation ? navigator.userActivation.isActive : true;
}

function ensureGraph() {
  if (analyser) return analyser;
  if (analysisImpossible) return null;

  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) {
    analysisImpossible = true;
    return null;
  }

  try {
    audioCtx = new Ctor();
    debug.log("ctx: created", { state: audioCtx.state, gesture: debug.gesture() });
    audioCtx.addEventListener?.("statechange", () => {
      debug.log("ctx: state", { state: audioCtx.state, gesture: debug.gesture() });
    });
    const node = audioCtx.createAnalyser();
    // A window of samples, not a spectrum. 1024 is about 21ms at 48kHz — long enough
    // for a stable RMS, short enough that a consonant still registers as a hit.
    node.fftSize = 1024;
    // Low, deliberately. This used to be 0.7 to stop a spectrum strobing; the donut now
    // runs its own attack/release envelope, and smoothing twice would only add latency
    // and turn every hit to mush.
    node.smoothingTimeConstant = 0.2;
    node.connect(audioCtx.destination);

    analyser = node;
    samples = new Uint8Array(node.fftSize);
    return analyser;
  } catch (err) {
    debug.log("ctx: failed", { error: err?.name });
    audioCtx = null;
    analyser = null;
    analysisImpossible = true;
    return null;
  }
}

/** Route one clip through the analyser, or leave it well alone. */
function listen(el) {
  const node = ensureGraph();
  if (!node || captured.has(el)) return;

  let source;
  try {
    source = audioCtx.createMediaElementSource(el);
  } catch (err) {
    // Already captured, or the browser refused. The element is untouched and audible.
    debug.log("ctx: capture refused", { error: err?.name });
    return;
  }

  captured.add(el);

  try {
    source.connect(node);
  } catch {
    // The element IS captured now, so it is already off the normal output path. Wire
    // it straight to the speakers so the reply is still heard, just not analysed.
    try {
      source.connect(audioCtx.destination);
    } catch {
      /* nothing left to try; the clip is lost, which is why this path is so guarded */
    }
  }
}

/** Numbers each clip in the debug log, so its events can be told from the next one's. */
let clipNumber = 0;

/**
 * Write down every event an element fires that matters to whether it was heard.
 * `label()` names what the element is playing now, since one element plays them all.
 */
function watch(el, label) {
  const report = (type) => () => {
    debug.log(`${label()}: ${type}`, {
      t: el.currentTime.toFixed(2),
      dur: Number.isFinite(el.duration) ? el.duration.toFixed(2) : el.duration,
      muted: el.muted || undefined,
      ctx: audioCtx?.state,
      code: type === "error" ? el.error?.code : undefined,
    });
  };
  for (const type of ["loadedmetadata", "playing", "pause", "waiting", "stalled", "ended", "error"]) {
    el.addEventListener(type, report(type));
  }
}

/**
 * @param holding `() => boolean`, true while the view still has a reply in flight:
 *                words arriving, or clips queued to play. The audio thread is kept
 *                running until it says false; see SUSPEND_GRACE_MS.
 */
export function createVoice({ holding: busy = () => false } = {}) {
  holding = busy;

  /**
   * The clip currently playing, and the function that releases whoever is awaiting it.
   *
   * Kept as a pair because a stop has to settle that promise by hand: pausing an
   * <audio> fires no `ended`, so a queue awaiting playToEnd would wait forever for a
   * clip that was deliberately cut off.
   *
   * @type {{el: HTMLAudioElement, done: () => void}|null}
   */
  let current = null;

  /**
   * Identity of the sequence currently running, so that stopping one does not merely
   * skip a clip and carry on with the next.
   *
   * It has to be separate from `current`, because starting each clip necessarily stops
   * the previous one — if the two shared a signal, a sequence would cancel itself
   * between every pair of clips.
   */
  let sequence = null;

  /** Every blob URL handed out, so none is left pinning its blob. */
  const urls = new Set();

  /**
   * ONE ELEMENT FOR EVERY CLIP. This used to be `new Audio(url)` per
   * clip. iOS lets an element play once a tap has played it, so a single element that
   * a tap has unlocked (see wake) stays playable for every clip after, whatever the
   * gesture state when the clip arrives. It is also captured into the graph once,
   * rather than once per clip.
   */
  let player = null;
  /** What the element is playing, for the debug log. */
  let playing = "idle";

  function element() {
    if (player) return player;
    player = new Audio();
    player.preload = "auto";
    if (debug.enabled()) watch(player, () => playing);
    listen(player);
    return player;
  }

  /** Silence the current clip WITHOUT cancelling a running sequence. */
  function halt() {
    if (!current) return;
    const { el, done } = current;
    current = null;
    el.pause();
    done();
  }

  const voice = {
    /**
     * Ask the server to say some text in the voice its thread was given.
     *
     * The request names the WEFT, never a voice — the server resolves what that weft
     * was configured to sound like. Returns a blob URL the caller can replay for free.
     */
    async clipFor({ weftId, text }) {
      const asked = performance.now();
      const blob = await apiBlob("/api/speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ weft_id: weftId, text }),
      });

      const url = URL.createObjectURL(blob);
      urls.add(url);
      debug.log("fetch: clip", {
        chars: text.length,
        bytes: blob.size,
        type: blob.type,
        ms: Math.round(performance.now() - asked),
      });
      return url;
    },

    /**
     * Get audio ready to start without a tap. CALL IT FROM INSIDE ONE — send, the
     * speaker, unmute, letting go of push-to-talk — and only when something is about
     * to be said aloud.
     *
     * A reply's first clip arrives seconds after the tap that asked for it, by which
     * time the tap no longer counts. So the tap does the unlocking now: it wakes the
     * context, and plays a moment of silence on the element the clips will use.
     */
    wake(reason) {
      const el = element();
      claimSession();
      cancelSuspend();
      debug.log("wake", { reason, gesture: debug.gesture(), ctx: audioCtx?.state ?? "none" });
      wakeContext("wake");
      // Not over a clip that is playing: that element is unlocked already.
      if (!current) {
        playing = "wake";
        el.src = silence();
        el.play().then(
          () => debug.log("wake: primed"),
          (err) => debug.log("wake: prime refused", { error: err?.name }),
        );
      }
      // A tap that never leads to a clip must not leave the thread running.
      suspendWhenQuiet();
    },

    /**
     * Play a clip and resolve when it has FINISHED, stopping anything already speaking.
     *
     * Resolving at the end rather than at the start is what lets a queue play clips
     * back to back with no gap and no overlap — `audio.play()` on its own resolves as
     * soon as playback begins, which would start every clip at once.
     *
     * REJECTS only when the browser refuses to start audio at all. That is an ordinary
     * outcome, not an error: autoplay policy wants a user gesture, and mobile Safari is
     * strictest. A clip that fails to DECODE resolves instead — one bad clip should not
     * take down the rest of a reply.
     */
    playToEnd(url) {
      halt();
      const el = element();
      const n = (clipNumber += 1);
      playing = `clip ${n}`;
      debug.log(`clip ${n}: start`, { gesture: debug.gesture(), ctx: audioCtx?.state ?? "none" });
      cancelSuspend();
      const waking = wakeContext(`clip ${n}`);

      return new Promise((resolve, reject) => {
        let settled = false;
        /** Listeners for this clip only; the element outlives it. */
        const mine = new AbortController();
        const finish = () => {
          settled = true;
          mine.abort();
          if (current?.done === done) current = null;
        };
        const done = () => {
          if (settled) return;
          finish();
          // Armed on every ending — natural, halted or failed. The next clip in a
          // sequence cancels it well inside the grace period.
          suspendWhenQuiet();
          resolve();
        };
        // The src check: an event still queued from the previous clip is not this one.
        const ours = () => {
          if (el.src === url) done();
        };

        current = { el, done };
        el.addEventListener("ended", ours, { signal: mine.signal });
        el.addEventListener("error", ours, { signal: mine.signal });
        el.src = url;

        // Inside a tap, play now: an await would spend the gesture. Outside one, a
        // context that is not running would take the clip into a graph that is not
        // making sound — and the log showed the resume landing 89ms after play() was
        // refused — so let it land first. Bounded, because `interrupted` can hang.
        // Synchronous when there is no waiting to do: even a microtask may be too late
        // for WebKit to count the tap.
        const start = () => {
          if (settled) return;
          el.play().then(
            () => debug.log(`clip ${n}: play() ok`, { ctx: audioCtx?.state }),
            (err) => {
              debug.log(`clip ${n}: play() REFUSED`, { error: err?.name, msg: err?.message });
              if (settled) return;
              finish();
              reject(err);
            },
          );
        };
        if (waking && !inGesture()) {
          Promise.race([waking, new Promise((r) => setTimeout(r, 1000))]).then(start);
        } else {
          start();
        }
      });
    },

    /**
     * Play several clips back to back — replaying a whole spoken reply.
     *
     * The token check is what makes a stop mid-sequence actually stop: without it,
     * halting one clip would simply advance to the next and the replay would be
     * unstoppable.
     */
    async playSequence(clips) {
      const token = {};
      sequence = token;

      for (const url of clips) {
        if (sequence !== token) return;
        await this.playToEnd(url);
        if (sequence !== token) return;
      }

      if (sequence === token) sequence = null;
    },

    stop() {
      debug.log("voice: stop");
      sequence = null;
      halt();
    },

    speaking() {
      return Boolean(current && !current.el.paused);
    },

    /**
     * How loud the voice is right now, 0..1 — or null when nothing is speaking.
     *
     * TIME DOMAIN, NOT FREQUENCY. This used to average the FFT magnitudes, which is
     * correlated with loudness but skewed by spectral tilt: a bright voice reads louder
     * than a warm one at the same actual volume. RMS over the raw waveform is simply
     * how loud it is, which is the only question being asked now that the ring breathes
     * rather than analyses.
     *
     * Null rather than 0 when there is no graph, so "silent" and "not listening" stay
     * distinguishable — the donut treats both as calm, but they are not the same fact.
     *
     * The buffer is reused. This is read once per animation frame, and allocating a
     * kilobyte sixty times a second to throw it away is how a canvas starts stuttering
     * on a phone.
     */
    loudness() {
      if (!analyser || !current || current.el.paused) return null;

      analyser.getByteTimeDomainData(samples);

      // Bytes are centred on 128; the deviation from that is the signal.
      let sum = 0;
      for (let i = 0; i < samples.length; i += 1) {
        const deviation = (samples[i] - 128) / 128;
        sum += deviation * deviation;
      }
      // Plain RMS, unscaled. Speech sits low here — roughly 0.05 to 0.2 even for a
      // confident voice — and it is tempting to multiply it up before handing it over.
      // Deliberately not: this module measures, the donut decides how dramatic to be.
      // Keeping the scaling in VOICE_KNOBS puts every taste dial in one block instead
      // of splitting them across two files that are tuned at different times.
      return Math.sqrt(sum / samples.length);
    },

    /** Stop, and let go of every blob this voice has held. */
    release() {
      this.stop();
      suspendWhenQuiet();
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    },
  };

  return voice;
}
