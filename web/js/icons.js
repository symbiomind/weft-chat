/* Weft — line icons, and what a weft can do.
 *
 * Emoji were the first icons, and they are a different drawing on every platform: a
 * phone's 🎤 and a desktop's share a meaning and nothing else. These are one stroke
 * style in the text colour (shapes from Feather, MIT), so they sit with the type
 * rather than on top of it, and CSS sizes and colours them like text.
 *
 * Built with createElementNS from path data, never from a markup string, for the same
 * reason dom.js takes no innerHTML: nothing in this client parses HTML.
 */

const SVG = "http://www.w3.org/2000/svg";

/** Each icon is a list of [tag, attributes] on a 24×24 grid. */
const SHAPES = {
  voice: [
    ["path", { d: "M11 5 6 9H2v6h4l5 4V5z" }],
    ["path", { d: "M15.5 8.5a5 5 0 0 1 0 7" }],
    ["path", { d: "M19 5a10 10 0 0 1 0 14" }],
  ],
  "voice-off": [
    ["path", { d: "M11 5 6 9H2v6h4l5 4V5z" }],
    ["path", { d: "M23 9l-6 6M17 9l6 6" }],
  ],
  paperclip: [
    ["path", {
      d: "M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66"
        + "l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48",
    }],
  ],
  // Feather has no plug; this one is Lucide's (ISC), drawn in the same stroke style.
  plug: [
    ["path", { d: "M12 22v-5" }],
    ["path", { d: "M9 8V2" }],
    ["path", { d: "M15 8V2" }],
    ["path", { d: "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" }],
  ],
  mic: [
    ["path", { d: "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" }],
    ["path", { d: "M19 10v2a7 7 0 0 1-14 0v-2" }],
    ["path", { d: "M12 19v4M8 23h8" }],
  ],
  // Feather's "slash": a weft with no engine, so nothing it could do is true yet.
  "no-entry": [
    ["circle", { cx: "12", cy: "12", r: "10" }],
    ["path", { d: "M4.93 4.93l14.14 14.14" }],
  ],
  // Back and close on the settings pages and the drawer. Drawn, not typed: a ‹ and a ✕
  // sit at different heights in every font, so as glyphs they never lined up.
  // Notifications waiting: on the status light, and in place of a weft's
  // pea. Feather's bell, closed at the rim so it can be filled.
  bell: [
    ["path", { d: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9z" }],
    ["path", { d: "M13.73 21a2 2 0 0 1-3.46 0" }],
  ],
  back: [["path", { d: "M15 18l-6-6 6-6" }]],
  close: [["path", { d: "M18 6 6 18M6 6l12 12" }]],
  gear: [
    ["circle", { cx: "12", cy: "12", r: "3" }],
    ["path", {
      d: "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0"
        + "l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2"
        + " 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06"
        + "a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65"
        + " 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65"
        + " 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65"
        + " 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09"
        + "a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2"
        + " 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21"
        + "a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z",
    }],
  ],
};

/**
 * An icon as an <svg class="ic">. With a `label` it is announced (role img, a title
 * for the hover); without one it is decoration, and whatever holds it carries the name.
 */
export function icon(name, { label } = {}) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("viewBox", "0 0 24 24");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
    const title = document.createElementNS(SVG, "title");
    title.textContent = label;
    svg.append(title);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  for (const [tag, attributes] of SHAPES[name]) {
    const shape = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, value);
    svg.append(shape);
  }
  return svg;
}

/**
 * What a weft can do, in the order the Pick shows it: `[{icon, label}]`.
 *
 * A voice belongs to the weft — it is set on the weft's page, and so does its MCP
 * door: the plug says a key has been minted for its agent. Push to talk belongs to
 * the user, so it shows on every weft or on none; it is here because hearing you is
 * still something the window you are about to talk into can do.
 *
 * @param weft  a weft as the Pick menu lists it, or null
 * @param hears whether the user has push to talk set up
 */
export function capabilitiesOf(weft, { hears = false } = {}) {
  if (!weft) return [];
  const caps = [];
  if (weft.has_voice) caps.push({ icon: "voice", label: "speaks its replies" });
  if (weft.has_door) caps.push({ icon: "plug", label: "has a door: its agent can reach Weft" });
  if (hears) caps.push({ icon: "mic", label: "hears you: push to talk" });
  return caps;
}
