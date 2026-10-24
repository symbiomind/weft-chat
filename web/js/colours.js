/* Weft — the pair of colours a pick wears.
 *
 * Every painted thing that belongs to a weft reads two CSS custom properties,
 * `--weft-a` and `--weft-b`: the assistant's bubbles and the donut's rings wear A,
 * the user's bubbles wear B, and the pea in the Pick menu is a gradient from one to the
 * other. :root defaults both to the theme's own pair (fern and twilight).
 *
 * Custom properties inherit, which is what makes per-pick colour cheap. The window sets
 * the CURRENT pick's pair on :root (through donut.setPair, which also tints the
 * canvas); a menu row or a preview sets its own pair on itself and everything inside it
 * follows, without a second stylesheet.
 *
 * Set through element.style rather than a style attribute, because the CSP has no
 * 'unsafe-inline' for styles and the CSSOM is not subject to it.
 */

/** What the server stores, and the only shape anything here will paint. */
const COLOUR = /^#[0-9a-f]{6}$/i;

/** If the stylesheet cannot be read (or is not hex), these are what it says. */
const FALLBACK = { a: "#7fb069", b: "#5d5d81" };

/** A pick's pair as `{a, b}`, or null when it wears the theme. Both or neither. */
export function pairOf(pick) {
  const a = pick?.colour_a;
  const b = pick?.colour_b;
  return COLOUR.test(a ?? "") && COLOUR.test(b ?? "") ? { a, b } : null;
}

/**
 * Dress one element (and so everything inside it) in a pair, or in the theme's.
 *
 * Undressed is set EXPLICITLY to the theme's variables rather than removed: removing
 * them would let the element inherit from :root, which is wearing whichever pick is
 * current — and a menu row for an undressed weft would show somebody else's colours.
 */
export function dress(node, pair) {
  node.style.setProperty("--weft-a", pair ? pair.a : "var(--fern)");
  node.style.setProperty("--weft-b", pair ? pair.b : "var(--twilight)");
}

/** The theme's own pair, as hex — the starting point for a colour picker. */
export function themePair() {
  try {
    const style = getComputedStyle(document.documentElement);
    const a = style.getPropertyValue("--fern").trim();
    const b = style.getPropertyValue("--twilight").trim();
    return {
      a: COLOUR.test(a) ? a.toLowerCase() : FALLBACK.a,
      b: COLOUR.test(b) ? b.toLowerCase() : FALLBACK.b,
    };
  } catch {
    return { ...FALLBACK };
  }
}
