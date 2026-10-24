/* Tests for web/js/colours.js — run under node.
 *
 *   node --test "tests/client/*.test.mjs"
 *
 * The one mistake worth pinning: an undressed menu row inheriting the CURRENT pick's
 * colours from :root, so a weft nobody has dressed shows up wearing somebody else's.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dress, pairOf, themePair } from "../../web/js/colours.js";

function fakeNode() {
  const props = new Map();
  return { props, style: { setProperty: (k, v) => props.set(k, v) } };
}

describe("pairOf", () => {
  it("reads a stored pair", () => {
    assert.deepEqual(pairOf({ colour_a: "#c77dff", colour_b: "#3a0ca3" }), {
      a: "#c77dff",
      b: "#3a0ca3",
    });
  });

  it("is null for a pick wearing the theme, or no pick at all", () => {
    assert.equal(pairOf({ colour_a: null, colour_b: null }), null);
    assert.equal(pairOf({}), null);
    assert.equal(pairOf(null), null);
  });

  it("is null for half a pair, or anything that is not six hex digits", () => {
    assert.equal(pairOf({ colour_a: "#c77dff", colour_b: null }), null);
    assert.equal(pairOf({ colour_a: "red", colour_b: "#3a0ca3" }), null);
    assert.equal(pairOf({ colour_a: "#fff", colour_b: "#3a0ca3" }), null);
  });
});

describe("dress", () => {
  it("puts a pair on the element", () => {
    const node = fakeNode();
    dress(node, { a: "#112233", b: "#445566" });
    assert.equal(node.props.get("--weft-a"), "#112233");
    assert.equal(node.props.get("--weft-b"), "#445566");
  });

  it("dresses an undressed element in the THEME, not in whatever :root is wearing", () => {
    const node = fakeNode();
    dress(node, null);
    assert.equal(node.props.get("--weft-a"), "var(--fern)");
    assert.equal(node.props.get("--weft-b"), "var(--twilight)");
  });
});

describe("themePair", () => {
  it("falls back to hex when there is no stylesheet to read", () => {
    const pair = themePair();
    assert.match(pair.a, /^#[0-9a-f]{6}$/);
    assert.match(pair.b, /^#[0-9a-f]{6}$/);
  });
});
