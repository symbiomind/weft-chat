/* Weft — the chat relay client.
 *
 * One call. It posts the conversation and the picked thread to /api/chat and yields
 * the answer as it arrives.
 *
 * The frames are Weft's own, not OpenAI's: `{delta}` for text, `{error}` for something
 * that broke after the reply had already started. The server normalises every gateway's
 * shape into those two, which is what keeps this file — and the view above it — from
 * having to know that some servers say `delta.content` and others say `message.content`.
 *
 * When the planned [socket] capability lands, this module is what changes and the
 * view is not: the frames stay the same, the pipe under them becomes a WebSocket, and
 * inbound messages nobody asked for start arriving on it.
 */

import { apiStream } from "./session.js";

/**
 * Send one turn and stream the reply.
 *
 * `messages` is the whole conversation so far. That is not an accident of laziness:
 * the OpenAI protocol is stateless, so a plain provider only knows what we send it. A
 * gateway that holds its own session is free to ignore ours.
 *
 * Returns an async iterator — consume it with `for await`.
 */
export function streamChat({ weftId, messages }) {
  return apiStream("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weft_id: weftId, messages }),
  });
}
