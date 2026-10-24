/* The comfort inventory, held against web/css/app.css — run under node.
 *
 * The accessibility issue asked for one thing above all: anything that moves, added
 * later, must join the list. Rather than a list somebody has to remember, these read
 * the stylesheet and fail when something new would slip past the calm and still rules
 * or past solid glass.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const css = readFileSync(new URL("../../web/css/app.css", import.meta.url), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/** Every rule as [selector, body], @media blocks flattened. */
function rules(text) {
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let match;
  while ((match = re.exec(text))) out.push([match[1].trim(), match[2]]);
  return out;
}

const all = rules(css);
const comfortStart = css.indexOf(":root:is([data-motion=calm]");

describe("motion", () => {
  it("is decided in one place: no scattered prefers-reduced-motion queries", () => {
    // comfort.js folds the device's setting into data-motion, and the user's choice
    // with it. A media query of its own would obey the device but not the choice.
    assert.ok(!css.includes("prefers-reduced-motion"));
  });

  it("stops every keyframe animation when calm or still", () => {
    const blanket = all.find(([sel]) => sel.startsWith(":root:is([data-motion=calm], [data-motion=still]) :is(*"));
    assert.ok(blanket, "the calm/still blanket rule is missing");
    assert.match(blanket[1], /animation:\s*none\s*!important/);
  });

  it("stops every transition when still", () => {
    const blanket = all.find(([sel]) => sel.startsWith(":root[data-motion=still] :is(*"));
    assert.ok(blanket, "the still blanket rule is missing");
    assert.match(blanket[1], /transition:\s*none\s*!important/);
  });

  it("has no !important animation that could outrank the blanket rule", () => {
    // `!important` on the blanket beats any later normal declaration; this only checks
    // nobody answered it with an !important animation of their own.
    for (const [sel, body] of all) {
      assert.ok(!/animation[^;]*!important/.test(body) || sel.includes("data-motion"),
        `${sel} has an !important animation the comfort rules cannot stop`);
    }
  });
});

describe("transparency", () => {
  /** Selectors in the solid-background rule at the end of the file. */
  const solid = all.find(([sel]) => sel.startsWith(":root[data-glass=solid] :is(.bubble"));

  it("has a solid rule", () => {
    assert.ok(solid, "the solid surfaces rule is missing");
  });

  it("makes every blurred surface opaque under solid", () => {
    // A surface blurred over the floor and NOT listed would stay see-through, now
    // without the blur that made it readable: worse than either setting.
    const listed = solid[0];
    const blurred = all
      .slice(0, all.findIndex(([sel]) => sel.includes("data-motion")))
      .filter(([, body]) => /backdrop-filter:\s*blur/.test(body))
      .flatMap(([sel]) => sel.split(",").map((s) => s.trim()));
    for (const sel of blurred) {
      assert.ok(listed.includes(sel), `${sel} is blurred but not made solid`);
    }
  });

  it("turns every backdrop blur off under solid", () => {
    const off = all.find(([sel]) => sel.startsWith(":root[data-glass=solid] :is(*"));
    assert.ok(off);
    assert.match(off[1], /backdrop-filter:\s*none\s*!important/);
  });

  it("starts after everything it overrides", () => {
    assert.ok(comfortStart > 0 && css.indexOf("backdrop-filter", comfortStart) > comfortStart);
  });
});
