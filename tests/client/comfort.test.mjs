/* Tests for web/js/comfort.js — run under node.
 *
 * The rule that matters most is the daughter's law: the device is a ceiling. A user may
 * choose quieter than the operating system asks, never louder.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  GLASS, MOTION, aboveCeiling, ceilings, quieter, readChoice,
} from "../../web/js/comfort.js";

const storageWith = (value) => ({ getItem: () => value });

describe("the device's ceiling", () => {
  it("allows everything when the device asks for nothing", () => {
    assert.deepEqual(ceilings({}), { motion: "full", glass: "glass" });
  });

  it("caps motion at calm when the device asks for reduced motion", () => {
    assert.equal(ceilings({ reducedMotion: true }).motion, "calm");
  });

  it("caps glass at less for reduced transparency, and for more contrast", () => {
    assert.equal(ceilings({ reducedTransparency: true }).glass, "less");
    assert.equal(ceilings({ moreContrast: true }).glass, "less");
  });

  it("means calm and solid in high contrast mode", () => {
    assert.deepEqual(ceilings({ forcedColors: true }), { motion: "calm", glass: "solid" });
  });
});

describe("what applies", () => {
  it("is the choice when the device allows it", () => {
    assert.equal(quieter("still", "full", MOTION), "still");
    assert.equal(quieter("less", "glass", GLASS), "less");
  });

  it("is never louder than the device, whatever was chosen", () => {
    // THE ONE. Chosen full on a device that asks for reduced motion: calm, not full.
    assert.equal(quieter("full", "calm", MOTION), "calm");
    assert.equal(quieter("glass", "solid", GLASS), "solid");
  });

  it("still lets the user go quieter than the device asks", () => {
    assert.equal(quieter("still", "calm", MOTION), "still");
  });

  it("marks only the louder options as off", () => {
    assert.equal(aboveCeiling("full", "calm", MOTION), true);
    assert.equal(aboveCeiling("calm", "calm", MOTION), false);
    assert.equal(aboveCeiling("still", "calm", MOTION), false);
  });
});

describe("the stored choice", () => {
  it("defaults to everything on, right-handed", () => {
    assert.deepEqual(readChoice(storageWith(null)), { motion: "full", glass: "glass", hand: "right" });
  });

  it("keeps what is valid and defaults the rest on its own", () => {
    const stored = JSON.stringify({ motion: "still", glass: "frosted", hand: "left" });
    assert.deepEqual(readChoice(storageWith(stored)), { motion: "still", glass: "glass", hand: "left" });
  });

  it("survives storage that throws or holds rubbish", () => {
    const throwing = { getItem() { throw new Error("blocked"); } };
    assert.equal(readChoice(throwing).motion, "full");
    assert.equal(readChoice(storageWith("{not json")).glass, "glass");
  });
});
