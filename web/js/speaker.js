/* Weft — saying a reply while it is still being written.
 *
 * Two loops, running independently, which is the whole trick:
 *
 *   synthesis:  take the next segment, ask the server for audio, put the clip in line
 *   playback:   take the next clip, play it to the end, take the next
 *
 * Because they do not wait for each other, the second paragraph is being synthesised
 * while the first one is being read aloud — so after the first clip there is no gap,
 * and the voice keeps pace with a model that is still typing.
 *
 * ORDER IS NEVER IN DOUBT. Synthesis is strictly sequential: one request at a time, in
 * the order the segments arrived. Firing them in parallel would be faster and would
 * also, on any unlucky day, read the third paragraph before the second — a bug that
 * appears only under load and sounds like the model has lost its mind.
 *
 * A FAILED SEGMENT DOES NOT STOP THE REST. If paragraph two will not synthesise, one
 * and three are still worth hearing. The error is reported once and the queue carries
 * on, because a reply with a gap in it is better than a reply that stops dead.
 *
 * AUTOPLAY REFUSAL STOPS PLAYBACK, NOT SYNTHESIS. If the browser will
 * not let audio start, it will not let the next clip start either, so the first
 * refusal parks playback and the caller is told to offer a tap. Synthesis carries on,
 * as it does while muted, and the refused clip goes back to the front of the line.
 * The tap then calls `resume()` and hears the whole reply from where it stopped.
 *
 * This used to cancel everything instead, to save the speech provider. That turned
 * one refusal into a reply that only ever had its first paragraph: the rest was never
 * made, so a replay from the bubble played paragraph one and stopped, which looked
 * exactly like the browser refusing paragraph two.
 *
 * No DOM and no fetch in here — the voice is injected, which is what lets the ordering
 * be tested rather than merely believed (tests/client/speaker.test.mjs).
 */

/**
 * @param voice an object with `clipFor({text})`, `playToEnd(url)` and `stop()`.
 *              See web/js/speech.js for the real one.
 */
export function createSpeaker(voice, { onClip, onError, onBlocked, onDrain, onCancel } = {}) {
  /** @type {string[]} segments waiting to be turned into audio */
  const pending = [];
  /** @type {string[]} clip urls waiting to be played, in order */
  const ready = [];

  let synthesising = false;
  let playing = false;
  let cancelled = false;
  /** Set by a refused autoplay: playback waits for resume(), synthesis does not. */
  let blocked = false;
  //: Starts true so a speaker nobody used never announces that it finished.
  let drained = true;

  /**
   * Fire onDrain once when the last clip has finished and nothing is left to make.
   *
   * The view uses this to hold the donut in its speaking mode for as long as there is
   * actually a voice in the room — the text stream ends well before the audio does, and
   * going idle at the end of the words would make the donut stop mid-sentence.
   */
  function maybeDrain() {
    if (drained || cancelled) return;
    // Blocked, the clips in line are waiting for a tap rather than for the voice, so
    // the queue has done everything it can once synthesis is through.
    if (synthesising || playing || pending.length || (ready.length && !blocked)) return;
    drained = true;
    onDrain?.();
  }

  async function pumpSynthesis() {
    if (synthesising || cancelled) return;

    const text = pending.shift();
    if (text === undefined) {
      maybeDrain();
      return;
    }

    synthesising = true;
    try {
      const url = await voice.clipFor({ text });
      // A cancel that landed while this request was in flight. The clip is already paid
      // for, but playing it now would talk over whatever replaced it.
      if (cancelled) return;
      ready.push(url);
      onClip?.(url);
      pumpPlayback();
    } catch (err) {
      if (!cancelled) onError?.(err);
    } finally {
      synthesising = false;
      // Keep going even after a failure: the next paragraph may be fine.
      if (!cancelled) pumpSynthesis();
    }
  }

  async function pumpPlayback() {
    if (playing || cancelled || blocked) return;

    const url = ready.shift();
    if (url === undefined) {
      maybeDrain();
      return;
    }

    playing = true;
    try {
      await voice.playToEnd(url);
    } catch (err) {
      if (cancelled) return;
      // The browser refused to start audio. Every later clip would be refused too, so
      // wait for a tap; this clip is first in line when it comes.
      ready.unshift(url);
      blocked = true;
      onBlocked?.(err);
      return;
    } finally {
      // Before maybeDrain can look: it reads `playing`.
      playing = false;
      if (blocked) maybeDrain();
    }
    if (!cancelled) pumpPlayback();
  }

  function cancel() {
    const unfinished = synthesising || playing || pending.length > 0 || ready.length > 0;
    cancelled = true;
    pending.length = 0;
    ready.length = 0;
    voice.stop();
    onCancel?.(unfinished);
  }

  return {
    /** Queue one segment. Safe to call while earlier ones are still being spoken. */
    say(text) {
      if (cancelled || !text) return;
      drained = false;
      pending.push(text);
      pumpSynthesis();
    },

    /** Stop now, drop anything queued, and refuse anything queued later. */
    cancel,

    /**
     * Carry on after a refused autoplay, from the clip that was refused. Call it from
     * inside a tap: the first clip starts synchronously, so it rides the gesture.
     * False when there is nothing to resume.
     */
    resume() {
      if (cancelled || !blocked) return false;
      blocked = false;
      drained = false;
      pumpPlayback();
      return true;
    },

    /** True while a refused autoplay is waiting for resume(). */
    get blocked() {
      return blocked && !cancelled;
    },

    /** True while there is still audio to make or to play. Blocked, nothing is going to
     *  be heard until a tap, so a blocked queue is not busy. */
    get busy() {
      return !cancelled && !blocked &&
        (synthesising || playing || pending.length > 0 || ready.length > 0);
    },

    get cancelled() {
      return cancelled;
    },
  };
}
