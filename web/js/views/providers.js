/* Weft — the Providers screen, and the Add/Edit form behind it.
 *
 * A provider is a URL, a name, and a bearer token this service seals and never gives
 * back. That last part shapes the whole screen:
 *
 * **The token field is blank on Edit, and that is not a bug to fix later.** There is no
 * endpoint that returns a provider's secret — not for the owner, not for an admin — so
 * there is nothing to prefill it with. What the server does return is `hint`, the last
 * four characters, which is enough to answer "is this the key I think it is" and not
 * enough to be one. Leaving the field empty means "keep what you have"; typing in it
 * replaces; there is a separate control to clear it. Three states, because "empty means
 * delete the token" would silently break every provider the moment somebody fixed a
 * typo in a name.
 *
 * **Four kinds live on this screen**: chat providers, speech providers, session stores
 * (a memory-mcp-ce server that keeps a weft's thread so every device sees it), and
 * speech-to-text providers for push to talk. All four are the same thing to the vault
 * — somebody else's URL and key — so they share the form and differ only in what the
 * hints say. Push to talk's settings live under its own section here too: it uses one
 * provider for every weft, so there is no weft's page to put them on.
 *
 * **Delete confirms inline rather than with window.confirm().** A native dialog is a
 * different visual language, it is not themeable, and on mobile it lands at the top of
 * the viewport nowhere near the row it is about. The row asking its own question is
 * both prettier and clearer about what exactly is being removed.
 */

import { el } from "../dom.js";
import { PURPOSE, panelHead } from "../panels.js";
import * as api from "../providers.js";

/** The line under an Add form's name: what this kind of provider is for. */
const KIND_PURPOSE = {
  llm: "an engine: what answers a weft",
  tts: "speech: how a weft sounds",
  session: "sessions: one thread on every device",
  stt: "push to talk: what hears you",
};

function errorBox() {
  const node = el("p", { class: "form-error", role: "alert", hidden: "" });
  return {
    node,
    show(message) {
      node.textContent = message;
      node.hidden = false;
    },
    clear() {
      node.hidden = true;
      node.textContent = "";
    },
  };
}

/**
 * The Add / Edit form. One function for both, because they are the same fields —
 * the only difference is whether the token field starts out meaning "set this" or
 * "leave it alone", and the hint line says which.
 */
function renderForm(panels, { provider, onSaved, kind = "llm" }) {
  const editing = Boolean(provider);
  const error = errorBox();
  //: Set at creation and never edited. Flipping an existing provider between the two
  //: would leave its model selections, or any voice using it, pointing at an endpoint
  //: that no longer answers what they need.
  const providerKind = editing ? provider.kind : kind;
  const speech = providerKind === "tts";
  const store = providerKind === "session";
  const hearing = providerKind === "stt";
  const noun = speech ? "speech provider" : store ? "session store"
    : hearing ? "speech-to-text provider" : "provider";

  const name = el("input", {
    type: "text", id: "provider-name", autocomplete: "off",
    autocapitalize: "none", spellcheck: "false", required: "",
    placeholder: speech ? "kokoro" : store ? "memory" : hearing ? "whisper" : "OpenRouter",
  });
  const url = el("input", {
    type: "text", id: "provider-url", autocomplete: "off", inputmode: "url",
    autocapitalize: "none", spellcheck: "false", required: "",
    placeholder: speech
      ? "http://kokoro.lan:8880/v1"
      : store ? "http://memory-mcp-ce.lan:5005"
        : hearing ? "http://whisper.lan:9000/v1" : "https://openrouter.ai/api/v1",
  });
  const secret = el("input", {
    type: "password", id: "provider-secret", autocomplete: "off",
    autocapitalize: "none", spellcheck: "false",
    placeholder: editing ? "leave blank to keep the current token"
      : store || hearing ? "" : "sk-…",
  });

  if (editing) {
    name.value = provider.name;
    url.value = provider.base_url;
  }

  //: Tracks the third state of the token field. Typing clears it: you cannot both
  //: clear a token and set a new one, and whichever the user did last is what they meant.
  let clearSecret = false;

  const clearRow = el("label", { class: "checkline" }, []);
  const clearBox = el("input", { type: "checkbox", id: "provider-clear" });
  clearBox.addEventListener("change", () => {
    clearSecret = clearBox.checked;
    secret.disabled = clearSecret;
    if (clearSecret) secret.value = "";
  });
  secret.addEventListener("input", () => {
    if (!secret.value) return;
    clearBox.checked = false;
    clearSecret = false;
  });
  clearRow.append(clearBox, document.createTextNode(" remove the stored token"));

  const submit = el("button", { type: "submit", text: editing ? "save" : "add provider" });

  const form = el("form", { class: "panel-form" }, [
    el("label", { for: "provider-name", text: "provider name" }),
    name,
    el("p", { class: "hint", text: "Whatever you call it. It is half of the Pick label." }),

    el("label", { for: "provider-url", text: "provider url" }),
    url,
    el("p", {
      class: "hint",
      text: speech
        ? "Include the API path — usually /v1. Weft asks it for /audio/voices and /audio/speech."
        : hearing
          ? "Include the API path — usually /v1. Weft sends recordings to /audio/transcriptions."
        : store
          ? "The memory-mcp-ce server itself, no path. Weft uses its /v1/sessions API."
          : "Include the API path — usually /v1. Weft asks it for /models exactly as typed.",
    }),

    el("label", { for: "provider-secret", text: "bearer token" }),
    secret,
    el("p", {
      class: "hint",
      text: editing
        ? provider.has_secret
          ? `Stored${provider.hint ? ` — ends ••••${provider.hint}` : ""}. It cannot be shown; leave this blank to keep it.`
          : "No token stored. A gateway on your own network often needs none."
        : store
          ? "The server's SESSION_BEARER_TOKEN. Sealed here and never sent back to the browser."
          : "Optional. Sealed on the server and never sent back to the browser.",
    }),
    error.node,
    submit,
  ]);

  if (editing) form.insertBefore(clearRow, error.node);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.clear();
    submit.disabled = true;

    const payload = { name: name.value.trim(), baseUrl: url.value.trim() };
    if (clearSecret) payload.secret = "";
    else if (secret.value) payload.secret = secret.value;

    try {
      if (editing) {
        await api.updateProvider(provider.id, payload);
      } else {
        // createProvider always sends a secret field; an absent one is only meaningful
        // on a patch, and "" is exactly right for "this provider has no token".
        await api.createProvider({
          ...payload, secret: payload.secret ?? "", kind: providerKind,
        });
      }
      onSaved();
      panels.pop();
    } catch (err) {
      error.show(err.detail || err.message || "Could not save that provider.");
      submit.disabled = false;
    }
  });

  return el("div", { class: "panel-inner" }, [
    panelHead(panels, {
      title: editing ? `Edit ${noun}` : `Add ${noun}`,
      sub: editing ? provider.name : KIND_PURPOSE[providerKind],
    }),
    el("div", { class: "panel-body" }, [form]),
  ]);
}

/**
 * Push to talk's settings: which provider hears the user, and how. Saved on every
 * change, as the whole block — there is no "save" button to forget.
 */
function renderSttSettings(hearing, settings, error) {
  const provider = el("select", { id: "stt-provider" }, [
    el("option", { value: "", text: "off" }),
    ...hearing.map((p) => el("option", { value: p.id, text: p.name })),
  ]);
  provider.value = settings.available ? settings.provider_id : "";

  const models = el("datalist", { id: "stt-model-list" });
  const model = el("input", {
    type: "text", id: "stt-model", list: "stt-model-list", autocomplete: "off",
    autocapitalize: "none", spellcheck: "false", placeholder: "whisper-1",
  });
  model.value = settings.model ?? "";
  const language = el("input", {
    type: "text", id: "stt-language", autocomplete: "off", autocapitalize: "none",
    spellcheck: "false", placeholder: "detect", maxlength: "16",
  });
  language.value = settings.language ?? "";

  const sendBox = el("input", { type: "checkbox", id: "stt-send" });
  sendBox.checked = settings.send_on_release;
  const tellBox = el("input", { type: "checkbox", id: "stt-tell" });
  tellBox.checked = settings.tell_weft;

  const details = el("div", {}, [
    el("label", { for: "stt-model", text: "model" }),
    model,
    models,
    el("p", { class: "hint", text: "Blank sends whisper-1. Many self-hosted servers ignore this and use whatever they loaded." }),
    el("label", { for: "stt-language", text: "language" }),
    language,
    el("p", { class: "hint", text: "A code such as en. Blank lets the provider work it out." }),
    el("label", { class: "checkline" }, [
      sendBox, document.createTextNode(" send as soon as I let go"),
    ]),
    el("p", { class: "hint", text: "Off: what you said lands in the text box first, to read and fix before it goes." }),
    el("label", { class: "checkline" }, [
      tellBox, document.createTextNode(" tell the weft it was spoken"),
    ]),
    el("p", { class: "hint", text: "Adds [spoken] in front of what they receive, so they can forgive what transcription got wrong. You never see it." }),
  ]);

  async function fillModels() {
    models.replaceChildren();
    if (!provider.value) return;
    try {
      const { models: ids } = await api.sttModels(provider.value);
      for (const id of ids) models.append(el("option", { value: id }));
    } catch {
      /* typing a model name still works */
    }
  }

  function showDetails() {
    details.hidden = !provider.value;
  }

  async function save() {
    error.clear();
    try {
      await api.setSttSettings({
        providerId: provider.value,
        model: model.value.trim(),
        language: language.value.trim(),
        sendOnRelease: sendBox.checked,
        tellWeft: tellBox.checked,
      });
      // The chat window shows or hides its 🎤 by these; it listens for this event
      // rather than this screen knowing anything about the window.
      window.dispatchEvent(new CustomEvent("weft:stt-changed"));
    } catch (err) {
      error.show(err.detail || err.message || "Could not save push to talk.");
    }
  }

  provider.addEventListener("change", () => {
    showDetails();
    fillModels();
    save();
  });
  for (const field of [model, language]) field.addEventListener("change", save);
  for (const box of [sendBox, tellBox]) box.addEventListener("change", save);

  showDetails();
  fillModels();

  return el("div", { class: "panel-form stt-settings" }, [
    el("label", { for: "stt-provider", text: "hear me with" }),
    provider,
    details,
  ]);
}

/**
 * The Providers list. `onChanged` fires whenever the set of providers might have moved
 * (deleting one unbinds its wefts), so the chat window can refresh its Pick menu — the
 * screen that changes the data tells the screen that displays it, rather than the
 * chat window polling.
 */
export function renderProviders(panels, { onChanged }) {
  const error = errorBox();
  const list = el("div", { class: "rows" });

  function addFor(kind) {
    return () => panels.push(
      (p) => renderForm(p, { provider: null, onSaved: refresh, kind }),
    );
  }

  function row(provider) {
    const title = el("div", { class: "row-main" }, [
      el("div", { class: "row-name", text: provider.name }),
      el("div", { class: "row-sub", text: provider.base_url }),
    ]);

    if (provider.kind === "llm") {
      const count = provider.weft_count;
      title.append(
        el("div", {
          class: "row-sub",
          text: count === 0
            ? "no wefts use it yet"
            : `answers ${count} weft${count === 1 ? "" : "s"}`,
        }),
      );
    }

    const actions = el("div", { class: "row-actions" }, [
      el("button", {
        class: "small",
        text: "Edit",
        onclick: () => panels.push((p) => renderForm(p, { provider, onSaved: refresh })),
      }),
      el("button", { class: "small danger", text: "Delete", onclick: askDelete }),
    ]);

    const node = el("div", { class: "row" }, [title, actions]);

    function askDelete() {
      const confirmRow = el("div", { class: "row-confirm" }, [
        el("span", {
          text: provider.kind === "llm" && provider.weft_count
            ? `Delete “${provider.name}”? Its wefts stay, with no engine until you give them one.`
            : `Delete “${provider.name}”?`,
        }),
        el("button", { class: "small danger", text: "delete", onclick: doDelete }),
        el("button", {
          class: "small",
          text: "cancel",
          onclick: () => confirmRow.replaceWith(actions),
        }),
      ]);
      actions.replaceWith(confirmRow);
    }

    async function doDelete() {
      try {
        await api.deleteProvider(provider.id);
        onChanged();
        refresh();
      } catch (err) {
        error.show(err.detail || err.message || "Could not delete that provider.");
      }
    }

    return node;
  }

  async function refresh() {
    error.clear();
    try {
      // Both reads before anything is drawn. Building the list around an await let two
      // overlapping refreshes (opening, then returning from a form) each append their
      // own settings block to the same list, and "hear me with" appeared twice.
      const [providers, settings] = await Promise.all([
        api.listProviders(),
        api.sttSettings().catch(() => null),
      ]);
      // "Engines", the word the Pick menu uses for a weft with none ("no engine"), so
      // it means one thing everywhere. Its + Add sits on its own heading, like the
      // other three: the head is for where you are, not for one section's button.
      list.replaceChildren(el("div", { class: "section-head", "data-section": "engines" }, [
        el("h2", { text: "Engines" }),
        el("button", { class: "small", text: "+ Add", onclick: addFor("llm") }),
      ]));

      const chat = providers.filter((p) => p.kind === "llm");
      const speech = providers.filter((p) => p.kind === "tts");
      const stores = providers.filter((p) => p.kind === "session");
      const hearing = providers.filter((p) => p.kind === "stt");

      if (chat.length === 0) {
        list.append(
          el("p", {
            class: "empty",
            text: "No providers yet. Add the gateway you talk to — a bridge, or any endpoint that speaks the OpenAI shape.",
          }),
        );
      } else {
        for (const provider of chat) list.append(row(provider));
      }

      // Speech providers are the same kind of thing — somebody else's URL and key — so
      // they live on the same screen rather than behind their own menu entry. A heading
      // is enough to keep them apart.
      list.append(
        el("div", { class: "section-head", "data-section": "speech" }, [
          el("h2", { text: "Speech" }),
          el("button", { class: "small", text: "+ Add", onclick: addFor("tts") }),
        ]),
      );

      if (speech.length === 0) {
        list.append(
          el("p", {
            class: "empty",
            text: "No speech providers. Add one — kokoro, or anything that answers the OpenAI /audio/speech shape — then give a weft its voice on that weft's page.",
          }),
        );
      } else {
        for (const provider of speech) list.append(row(provider));
      }

      list.append(
        el("div", { class: "section-head", "data-section": "sessions" }, [
          el("h2", { text: "Sessions" }),
          el("button", { class: "small", text: "+ Add", onclick: addFor("session") }),
        ]),
      );

      if (stores.length === 0) {
        list.append(
          el("p", {
            class: "empty",
            text: "No session stores. Add a memory-mcp-ce server here, then on a weft's page choose to keep its thread there — and every device you open it on sees the same conversation.",
          }),
        );
      } else {
        for (const provider of stores) list.append(row(provider));
      }

      list.append(
        el("div", { class: "section-head", "data-section": "talk" }, [
          el("h2", { text: "Push to talk" }),
          el("button", { class: "small", text: "+ Add", onclick: addFor("stt") }),
        ]),
      );

      if (hearing.length === 0) {
        list.append(
          el("p", {
            class: "empty",
            text: "No speech-to-text providers. Add one — a whisper server, or anything that answers the OpenAI /audio/transcriptions shape — and the send shelf grows a 🎤: hold it and talk.",
          }),
        );
      } else {
        for (const provider of hearing) list.append(row(provider));
        if (settings) list.append(renderSttSettings(hearing, settings, error));
      }
    } catch (err) {
      error.show(err.detail || err.message || "Could not load your providers.");
    }
  }

  const node = el("div", { class: "panel-inner" }, [
    panelHead(panels, { title: "Providers", sub: PURPOSE.providers }),
    el("div", { class: "panel-body" }, [error.node, list]),
  ]);

  refresh();

  // Re-read on the way back: an edit or an add from a form changes this list.
  return { node, onReveal: refresh };
}
