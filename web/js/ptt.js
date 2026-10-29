/* Weft — push to talk.
 *
 * Hold the 🎤 petal and talk; let go and the recording goes off to be transcribed.
 * Slide off the petal to cancel — an "ugh, no" never gets sent. A quick tap only says
 * "hold to talk", because a recording too short to hold a word is a mis-tap.
 *
 * THE MIC IS OPENED ON PRESS AND CLOSED ON RELEASE. Holding the stream between presses
 * would save a moment and keep the phone's "recording" light on the whole time the
 * window is open, which is not something anyone wants from a chat app. The first press
 * also brings up the permission prompt; a press that ends while it is up is treated as
 * a tap, and the next one records.
 *
 * WHAT IS RECORDED is whatever the browser does best: Opus in WebM on Chrome and
 * Android, AAC in MP4 on Safari. Whisper decodes either, and the server passes the
 * container's type along with the bytes. Some servers read only wav and mp3 (Ollama's
 * transcription endpoint does); the server answers 415 for those, and the recording
 * goes again as 16 kHz mono wav, which is what that provider gets from then on.
 *
 * This module owns the petal's pointer events outright — they stop here, so the shelf
 * never mistakes a slide-to-cancel for its own swipe-down-to-close.
 */

/** Long enough that a tap is not a recording, short enough not to clip "yes". */
const MIN_MS = 450;
/** A recording stops itself here. Two minutes is a speech, not a message. */
const MAX_MS = 120_000;
/** How far a finger may wander before the hold counts as cancelled. */
const CANCEL_PX = 64;
/** What speech recognisers listen at. Two minutes of it is under 4 MB. */
const WAV_RATE = 16_000;

/**
 * The providers that refused a recording and took the same one as wav, by id. For the
 * life of the page, and per provider: wav is several times the size of Opus, so one
 * weft trying a wav-only server should not make every other weft upload wav too.
 */
const wavFor = new Set();

/**
 * The provider a pick's recordings go to, or null when it does not hear you: its own,
 * the account's (`account` is GET /api/stt), or none. The built-in pick never hears:
 * it has no voice either. A weft whose own provider was deleted hears nothing, rather
 * than quietly the account's.
 */
export function earsOf(weft, account) {
  if (!weft || weft.builtin || weft.stt_mode === "off") return null;
  if (weft.stt_mode === "own") return weft.stt_provider_id ?? null;
  return account?.available ? account.provider_id : null;
}

/** A recording as 16-bit mono wav at WAV_RATE. Decoding resamples; rendering downmixes. */
async function toWav(blob) {
  const decoded = await new OfflineAudioContext(1, 1, WAV_RATE)
    .decodeAudioData(await blob.arrayBuffer());
  const offline = new OfflineAudioContext(1, decoded.length, WAV_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const samples = (await offline.startRendering()).getChannelData(0);

  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (at, words) => [...words].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);         // fmt chunk size
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, 1, true);          // mono
  view.setUint32(24, WAV_RATE, true);
  view.setUint32(28, WAV_RATE * 2, true); // bytes per second
  view.setUint16(32, 2, true);          // bytes per frame
  view.setUint16(34, 16, true);         // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + i * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  });
  return new Blob([view], { type: "audio/wav" });
}

/** The first container this browser can record, in order of preference. */
function recordingType() {
  if (typeof MediaRecorder === "undefined") return null;
  for (const type of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]) {
    if (MediaRecorder.isTypeSupported?.(type)) return type;
  }
  return "";
}

/**
 * @param petal       the 🎤 button.
 * @param transcribe  `(blob) => Promise<{text}>`.
 * @param ears        `()` the id of the provider a recording now goes to, or null.
 * @param onStart     `(level)` when recording begins; `level()` is the mic's RMS now.
 * @param onRelease   `()` from inside the pointerup that ends a recording, so the
 *                    caller can do what needs a tap. Not on a cancel.
 * @param onStop      `()` when recording ends, however it ended.
 * @param onBusy      `(busy)` while a recording is being transcribed.
 * @param onText      `(text)` with the transcript.
 * @param onHint      `(words)` for a short label on the petal.
 * @param onError     `(message)` when it went wrong in a way worth a bubble.
 */
export function createPushToTalk(petal, {
  transcribe, ears, onStart, onRelease, onStop, onBusy, onText, onHint, onError,
} = {}) {
  /** "idle" | "opening" | "recording" | "transcribing" */
  let state = "idle";
  let press = null;
  let stream = null;
  let recorder = null;
  let chunks = [];
  let audio = null;
  let maxTimer = null;
  let startedAt = 0;
  /** Set when the recording should be thrown away rather than transcribed. */
  let discard = false;
  /** Set by teardown: a recording that stops afterwards reports to nobody. */
  let dead = false;

  function setState(next) {
    state = next;
    petal.classList.toggle("recording", next === "recording");
    petal.classList.toggle("transcribing", next === "transcribing");
  }

  function closeMic() {
    clearTimeout(maxTimer);
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = null;
    audio?.context.close().catch(() => {});
    audio = null;
  }

  /** A reading of the mic for the donut: plain RMS, as speech.js gives for a voice. */
  function level() {
    if (!audio) return null;
    audio.analyser.getByteTimeDomainData(audio.samples);
    let sum = 0;
    for (const sample of audio.samples) {
      const deviation = (sample - 128) / 128;
      sum += deviation * deviation;
    }
    return Math.sqrt(sum / audio.samples.length);
  }

  async function open() {
    if (!navigator.mediaDevices?.getUserMedia || recordingType() === null) {
      onError?.(
        window.isSecureContext
          ? "This browser cannot record audio."
          : "Push to talk needs the microphone, and browsers only allow that over https.",
      );
      return;
    }

    setState("opening");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      setState("idle");
      onError?.(err?.name === "NotAllowedError"
        ? "The microphone is blocked for this site. Allow it in the browser's site settings."
        : `Could not open the microphone (${err?.name || "unknown error"}).`);
      return;
    }

    // Let go while the permission prompt was up: that was a tap, not a recording.
    if (!press || dead) {
      closeMic();
      setState("idle");
      onHint?.("hold to talk");
      return;
    }

    try {
      const type = recordingType();
      recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
    } catch (err) {
      closeMic();
      setState("idle");
      onError?.(`Could not start recording (${err?.name || "unknown error"}).`);
      return;
    }

    try {
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      context.createMediaStreamSource(stream).connect(analyser);
      audio = { context, analyser, samples: new Uint8Array(analyser.fftSize) };
    } catch {
      audio = null; // the ring just does not listen; the recording is unaffected
    }

    chunks = [];
    discard = false;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", finished, { once: true });
    recorder.start();
    startedAt = performance.now();
    maxTimer = setTimeout(stop, MAX_MS);
    setState("recording");
    onHint?.("listening… slide off to cancel");
    onStart?.(level);
  }

  function stop({ cancel = false } = {}) {
    if (state !== "recording") return;
    if (cancel) discard = true;
    else if (performance.now() - startedAt < MIN_MS) discard = "short";
    recorder.stop();
  }

  /** The recording as recorded, or as wav when the provider will not read it. */
  async function transcribeEither(blob) {
    const provider = ears?.() ?? null;
    if (!wavFor.has(provider)) {
      try {
        return await transcribe(blob);
      } catch (err) {
        if (err.status !== 415) throw err;
      }
    }
    const answer = await transcribe(await toWav(blob));
    wavFor.add(provider);
    return answer;
  }

  async function finished() {
    const type = recorder.mimeType || recordingType() || "audio/webm";
    recorder = null;
    closeMic();
    if (dead) return;
    onStop?.();

    if (discard) {
      setState("idle");
      onHint?.(discard === "short" ? "hold to talk" : "cancelled");
      return;
    }

    const blob = new Blob(chunks, { type: type.split(";")[0] });
    chunks = [];
    setState("transcribing");
    onBusy?.(true);
    onHint?.("transcribing…");
    try {
      const { text } = await transcribeEither(blob);
      if (text) onText?.(text);
      else onHint?.("heard nothing");
    } catch (err) {
      onError?.(`Could not transcribe that: ${err.detail || err.message || "no answer"}.`);
    } finally {
      setState("idle");
      onBusy?.(false);
    }
  }

  // ---- the petal's own pointer handling ----

  function down(event) {
    event.stopPropagation();
    if (event.button !== 0) return;
    event.preventDefault();
    if (state !== "idle") return;
    press = { id: event.pointerId, x: event.clientX, y: event.clientY };
    petal.setPointerCapture?.(event.pointerId);
    open();
  }

  function move(event) {
    event.stopPropagation();
    if (!press || event.pointerId !== press.id) return;
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > CANCEL_PX) {
      press = null;
      stop({ cancel: true });
    }
  }

  function up(event) {
    event.stopPropagation();
    if (!press || event.pointerId !== press.id) return;
    press = null;
    const cancel = event.type === "pointercancel";
    if (!cancel && state === "recording") onRelease?.();
    stop({ cancel });
  }

  function swallow(event) {
    event.stopPropagation();
    event.preventDefault();
  }

  petal.addEventListener("pointerdown", down);
  petal.addEventListener("pointermove", move);
  petal.addEventListener("pointerup", up);
  petal.addEventListener("pointercancel", up);
  petal.addEventListener("click", swallow);
  petal.addEventListener("contextmenu", swallow);

  return {
    teardown() {
      press = null;
      dead = true;
      discard = true;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      closeMic();
    },
  };
}
