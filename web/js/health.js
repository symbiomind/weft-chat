/* Weft — is the service there?
 *
 * Feeds the status LED. Polls /health, slowly while the service answers and quickly
 * once it stops, so the light goes green again within seconds of it coming back
 * rather than within the next slow tick.
 *
 * Polling alone would leave the light lying for up to a whole interval: stop the
 * service, open a menu, read an error under a green light. So session.js also says
 * when a real request failed to arrive (onUnreachable), and that triggers a check
 * straight away. The poll is the floor; real traffic is what makes it prompt.
 *
 * Every check asks /health, including the hinted ones. A failed request is a reason
 * to look, not an answer: one aborted stream or one flaky hop should not turn the
 * light red if the service is in fact fine.
 *
 * NO DOM IN HERE, like session.js — the view owns the LED and the page events.
 */

import { onUnreachable, ping } from "./session.js";

/** Between checks while the service answers. */
const UP_EVERY_MS = 20_000;
/** Between checks while it does not — this is how fast recovery shows. */
const DOWN_EVERY_MS = 4_000;

/**
 * Start watching. `onChange(status)` fires with "up", "degraded" or "down" on the first
 * answer and on every change after; never twice in a row with the same status.
 *
 * Returns `{ check, stop }`. `check()` looks now — call it when the page has reason to
 * think things changed (the tab came back, the browser came online). Concurrent calls
 * share one request.
 */
export function watchHealth(onChange, { upEvery = UP_EVERY_MS, downEvery = DOWN_EVERY_MS } = {}) {
  let status = null;
  let timer = null;
  let inFlight = null;
  let stopped = false;

  function check() {
    if (stopped) return Promise.resolve(status);
    if (inFlight) return inFlight;
    clearTimeout(timer);
    timer = null;

    inFlight = ping().then((next) => {
      inFlight = null;
      if (stopped) return status;
      if (next !== status) {
        status = next;
        try {
          onChange(next);
        } catch (err) {
          console.error("health listener failed", err);
        }
      }
      timer = setTimeout(check, next === "up" ? upEvery : downEvery);
      return status;
    });
    return inFlight;
  }

  const unhook = onUnreachable(() => {
    check();
  });
  check();

  return {
    check,
    stop() {
      stopped = true;
      clearTimeout(timer);
      timer = null;
      unhook();
    },
  };
}
