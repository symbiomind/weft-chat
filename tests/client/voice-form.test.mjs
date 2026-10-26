/* Tests for what the voice form starts with when it opens.
 *
 * Opening the form on the provider a weft already speaks through has to show what is
 * saved. Before, it showed the provider's first model and first voice, and one press of
 * save replaced a weft's voice with whatever sorted first.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { startingVoice } from "../../web/js/views/weft.js";

const KOKORO = {
  models: ["gpt-4o-mini-tts", "kokoro", "tts-1", "tts-1-hd"],
  voices: ["af_alloy", "af_heart", "bm_george"],
};

describe("startingVoice", () => {
  it("shows the saved voice when the provider is the current one", () => {
    assert.deepEqual(
      startingVoice(KOKORO, { model: "tts-1", voice: "af_heart" }),
      { model: "tts-1", voice: "af_heart" },
    );
  });

  it("keeps a saved voice the provider does not list", () => {
    const openrouter = { models: ["elevenlabs/eleven-v3"], voices: [] };
    assert.deepEqual(
      startingVoice(openrouter, { model: "elevenlabs/eleven-v3", voice: "george" }),
      { model: "elevenlabs/eleven-v3", voice: "george" },
    );
  });

  it("picks kokoro over the alias that sorts first", () => {
    assert.equal(startingVoice(KOKORO, null).model, "kokoro");
  });

  it("takes the only model there is", () => {
    assert.equal(startingVoice({ models: ["piper"], voices: [] }, null).model, "piper");
  });

  it("leaves the model blank rather than guess among many", () => {
    const openrouter = {
      models: ["elevenlabs/eleven-v3", "elevenlabs/eleven-v4", "mistralai/voxtral-mini-tts-3-0"],
      voices: [],
    };
    assert.deepEqual(startingVoice(openrouter, null), { model: "", voice: "" });
  });

  it("offers the first listed voice for a new provider", () => {
    assert.equal(startingVoice(KOKORO, null).voice, "af_alloy");
  });
});
