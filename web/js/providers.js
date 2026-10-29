/* Weft — the providers and wefts API.
 *
 * Transport only: every call goes through session.js's apiFetch(), which is the one
 * door in this client that knows a credential exists. Nothing here calls fetch(),
 * holds a token, or touches the DOM.
 *
 * A note on what these endpoints will NOT give you: there is no call that returns a
 * provider's bearer token, because there is no route that could. A read gives you
 * `hint` — the last four characters — and `has_secret`. That is why the edit form
 * leaves the token field blank instead of prefilling it: not an omission, the
 * contract.
 */

import { apiBlob, apiFetch } from "./session.js";

const BASE = "/api/providers";

function jsonBody(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function listProviders() {
  return apiFetch(BASE);
}

export function createProvider({ name, baseUrl, secret, kind = "llm" }) {
  return apiFetch(BASE, jsonBody("POST", { name, base_url: baseUrl, secret, kind }));
}

/**
 * Patch a provider. Omit `secret` entirely to leave the stored token alone; pass an
 * empty string to clear it. The server reads those as two different instructions and
 * so must every caller — `secret: undefined` and `secret: ""` are not the same edit.
 */
export function updateProvider(id, patch) {
  const body = {};
  if (patch.name !== undefined) body.name = patch.name;
  if (patch.baseUrl !== undefined) body.base_url = patch.baseUrl;
  if (patch.secret !== undefined) body.secret = patch.secret;
  return apiFetch(`${BASE}/${encodeURIComponent(id)}`, jsonBody("PATCH", body));
}

export function deleteProvider(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Live from the provider's own /v1/models. Slow by nature — show a loading state. */
export function listModels(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}/models`);
}

/**
 * What a speech provider can say, and in whose voice.
 *
 * `voices` comes back empty for an endpoint with no /audio/voices — OpenAI itself has
 * none. That is not a failure: offer a text field instead of a dropdown.
 */
export function listVoices(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}/voices`);
}

// ---- wefts ----------------------------------------------------
//
// A weft is whoever you talk to, by name, and owns its settings. Every call below addresses one by
// its id; the engine (provider and model) is just one more of its settings.

const WEFTS = "/api/wefts";

function weftPath(id, rest = "") {
  return `${WEFTS}/${encodeURIComponent(id)}${rest}`;
}

/** The Pick menu: every weft this user has, by name. No upstream call. */
export function listWefts() {
  return apiFetch(WEFTS);
}

/** One weft, fresh from the server, for its page. */
export function getWeft(id) {
  return apiFetch(weftPath(id));
}

/** Add a weft by name. A 409 means the user already has one by that name. */
export function createWeft(name) {
  return apiFetch(WEFTS, jsonBody("POST", { name }));
}

export function renameWeft(id, name) {
  return apiFetch(weftPath(id), jsonBody("PATCH", { name }));
}

export function deleteWeft(id) {
  return apiFetch(weftPath(id), { method: "DELETE" });
}

/** Bind a weft to a provider and a model, or pass nulls to unbind it. */
export function setWeftEngine(id, { providerId, modelId }) {
  return apiFetch(weftPath(id, "/engine"), jsonBody("PUT", {
    provider_id: providerId ?? null,
    model_id: modelId ?? null,
  }));
}

/**
 * Give a weft a voice, or clear it. The three TTS fields move together: pass all three
 * to set, or none to silence it.
 */
export function setWeftVoice(id, { ttsProviderId, ttsModel, ttsVoice }) {
  return apiFetch(weftPath(id, "/voice"), jsonBody("PUT", {
    tts_provider_id: ttsProviderId ?? null,
    tts_model: ttsModel ?? null,
    tts_voice: ttsVoice ?? null,
  }));
}

/**
 * Choose a weft's ears: `mode` is "account", "own" or "off". `providerId` and `model`
 * are read only with "own"; the others keep whatever the weft had chosen before.
 */
export function setWeftEars(id, { mode, providerId, model }) {
  return apiFetch(weftPath(id, "/stt"), jsonBody("PUT", {
    mode,
    provider_id: providerId ?? null,
    model: model || null,
  }));
}

/**
 * Say where a weft's thread is kept, how much of it is sent upstream, and how much of
 * it the window holds. All of it together, because the server replaces the set.
 * `sendTurns` and `windowTurns` count TURNS: something you said and the answer to it.
 * `sessionId` is a typed id to join, or null for the weft's automatic one.
 */
export function setWeftHistory(id, {
  history, sendTurns, windowTurns, sessionProviderId = null, sessionId = null,
}) {
  return apiFetch(weftPath(id, "/history"), jsonBody("PUT", {
    history,
    send_turns: sendTurns,
    window_turns: windowTurns,
    session_provider_id: sessionProviderId,
    session_id: sessionId,
  }));
}

/** Dress a weft in a pair of colours ("#rrggbb"), or pass nulls for the theme's. */
export function setWeftColours(id, { colourA, colourB }) {
  return apiFetch(weftPath(id, "/colours"), jsonBody("PUT", {
    colour_a: colourA,
    colour_b: colourB,
  }));
}

/** Replace a weft's extra request headers: `[{name, value}]`, in order. */
export function setWeftHeaders(id, headers) {
  return apiFetch(weftPath(id, "/headers"), jsonBody("PUT", { headers }));
}

// ---- the MCP door ----

/** Whether a weft's door is open: `{open, hint, created_at, last_used_at, mcp_url}`. */
export function getDoor(id) {
  return apiFetch(weftPath(id, "/door"));
}

/** A one-time invite address for the weft's agent: `{url, expires_in}`. */
export function inviteToDoor(id) {
  return apiFetch(weftPath(id, "/door/invite"), { method: "POST" });
}

/** A key made by hand, with the guide it belongs in. The only time the key is shown. */
export function makeDoorKey(id) {
  return apiFetch(weftPath(id, "/door"), { method: "POST" });
}

export function closeDoor(id) {
  return apiFetch(weftPath(id, "/door"), { method: "DELETE" });
}

// ---- notifications: what the agents left through the door ----

const NOTIFICATIONS = "/api/notifications";

/** Waiting notifications by weft id: `{wefts: {id: n}}`, only wefts with any. */
export function notificationCounts() {
  return apiFetch(`${NOTIFICATIONS}/counts`);
}

/** Every weft's notifications, newest first: `[{id, weft_id, title, body, created_at}]`.
 *  With a weft id, only that weft's. */
export function listNotifications(weftId = null) {
  return apiFetch(weftId ? `${NOTIFICATIONS}?weft=${encodeURIComponent(weftId)}` : NOTIFICATIONS);
}

export function dismissNotification(id) {
  return apiFetch(`${NOTIFICATIONS}/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Dismiss every one waiting, or with a weft id only that weft's. */
export function dismissAllNotifications(weftId = null) {
  return apiFetch(`${NOTIFICATIONS}/dismiss`, jsonBody("POST", weftId ? { weft_id: weftId } : {}));
}

/**
 * Give a weft a background picture. The body is the file itself; what
 * comes back as `background` is the hash of what the server STORED after re-encoding.
 */
export function setWeftBackground(id, file) {
  return apiFetch(weftPath(id, "/background"), {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
}

export function clearWeftBackground(id) {
  return apiFetch(weftPath(id, "/background"), { method: "DELETE" });
}

/**
 * A weft's picture, as a Blob. Fetched with the credential like everything else — the
 * pictures are served only to their owner, so a plain <img src> could never load one.
 */
export function backgroundBlob(hash) {
  return apiBlob(`${WEFTS}/background/${encodeURIComponent(hash)}`);
}

/**
 * The tail of a weft's thread from its session store: `{session_key, messages,
 * has_more, last_seq, full}`. Only for a weft whose history is "session".
 *
 * With `afterSeq`, a catch-up: only what came after it, unless the service had to
 * answer with the whole tail instead (`full` says which).
 */
export function sessionThread(weftId, afterSeq = null) {
  const params = { weft_id: weftId };
  if (afterSeq !== null) params.after_seq = String(afterSeq);
  return apiFetch(`/api/session/thread?${new URLSearchParams(params)}`);
}

/** Where a weft's session ends: `{session_key, last_seq}`. The cheap question. */
export function sessionHead(weftId) {
  return apiFetch(`/api/session/head?${new URLSearchParams({ weft_id: weftId })}`);
}

/**
 * Upload one picture for a message that is about to be sent. Resolves
 * to `{hash}`: what the chat request names it by. As with a background, the hash is of
 * what the server STORED, after re-encoding, not of the file that was sent.
 */
export function uploadAttachment(file) {
  return apiFetch("/api/attachments", {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
}

/** One of this user's attached pictures, as a Blob, for a bubble to show. */
export function attachmentBlob(hash) {
  return apiBlob(`/api/attachments/${encodeURIComponent(hash)}`);
}

/**
 * Push to talk: the user's speech-to-text settings. One provider for
 * every pick, so this is the user's, not a pick's.
 */
export function sttSettings() {
  return apiFetch("/api/stt");
}

/** Replace the whole block: `{providerId, model, language, sendOnRelease, tellWeft}`. */
export function setSttSettings({ providerId, model, language, sendOnRelease, tellWeft }) {
  return apiFetch("/api/stt", jsonBody("PUT", {
    provider_id: providerId || null,
    model: model || null,
    language: language || null,
    send_on_release: Boolean(sendOnRelease),
    tell_weft: Boolean(tellWeft),
  }));
}

/** What a speech-to-text provider says it can run; empty when it will not say. */
export function sttModels(providerId) {
  return apiFetch(`/api/stt/models?${new URLSearchParams({ provider_id: providerId })}`);
}

/**
 * Turn one recording into words with the ears of `weftId`, or the account's without
 * one. The body is the recording itself, in whatever container the browser recorded it
 * in. The server chooses the provider; the browser only says which weft.
 */
export function transcribe(blob, weftId = null) {
  const query = weftId ? `?${new URLSearchParams({ weft_id: weftId })}` : "";
  return apiFetch(`/api/stt/transcribe${query}`, {
    method: "POST",
    headers: { "Content-Type": blob.type || "audio/webm" },
    body: blob,
  });
}
