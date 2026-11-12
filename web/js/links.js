/**
 * Web addresses in a message, made clickable.
 *
 * A reply is plain text and stays plain text (web/js/references.js). This module
 * recognises one more shape inside it, a bare `http://` or `https://` address, and
 * hands it back as a link. Nothing else is interpreted. A link is built as an element
 * with its text set as text, so no part of a message is ever parsed as HTML.
 *
 * **Streaming needs no second pass.** The bubble is redrawn from the whole reply on
 * every delta (setBubbleText in views/chat.js), so an address split across chunks is
 * whole again by the next redraw. Only the very end of the text can be unfinished,
 * and an address that runs right up to it stays as words until something follows it
 * or the reply ends. A finished reply links every address it has.
 *
 * **Where an address ends.** At whitespace, or at the first character outside printable
 * ASCII: a weft's sign-off emoji right after an address is not part of it. Then
 * trailing punctuation comes off (`.`, `,`, `!`, a closing quote...), and a closing
 * bracket only when it has no partner inside the address, so the `)` of a sentence
 * comes off and the `)` of a Wikipedia address stays. The query string is kept whole.
 *
 * **Code is left alone.** An address inside a `code span` or a fenced block is shown
 * as written. A fence still open is code to the end, so a block that is still
 * streaming does not grow links and then lose them.
 */

/** A fenced block (closed, or open to the end), or a code span on one line. */
const CODE = /```[\s\S]*?(?:```|$)|`[^`\n]+`/g;

/** An address: a scheme, then printable ASCII up to whitespace or `<`, `>`, `"`, a backtick. */
const ADDRESS = /https?:\/\/[A-Za-z0-9[][!#-;=?-_a-~]*/g;

/** Punctuation that ends a sentence rather than an address. */
const TRAILING = new Set([".", ",", ";", ":", "!", "?", "'", "*", "_", "~"]);

const PAIRS = { ")": "(", "]": "[", "}": "{" };

const count = (text, char) => text.split(char).length - 1;

/** The address with what ends the sentence around it taken off. */
function trimmed(address) {
  let url = address;
  for (;;) {
    const last = url.at(-1);
    if (TRAILING.has(last)) url = url.slice(0, -1);
    else if (PAIRS[last] && count(url, last) > count(url, PAIRS[last])) url = url.slice(0, -1);
    else return url;
  }
}

/**
 * Cut a message into words and links.
 *
 * Returns `[{ text }, { text, href }, ...]` in order, with neighbouring words merged,
 * so joining every `text` gives the message back exactly.
 *
 * @param {string} message
 * @param {{ streaming?: boolean }} [options]
 */
export function linkParts(message, { streaming = false } = {}) {
  const text = message || "";
  const parts = [];
  const words = (chunk) => {
    if (!chunk) return;
    if (parts.length && !parts.at(-1).href) parts.at(-1).text += chunk;
    else parts.push({ text: chunk });
  };

  const prose = (from, to) => {
    const chunk = text.slice(from, to);
    let at = 0;
    for (const match of chunk.matchAll(ADDRESS)) {
      const start = match.index;
      // "xhttps://" is a word that happens to contain a scheme, not an address.
      if (start > 0 && /[A-Za-z0-9]/.test(chunk[start - 1])) continue;
      // Still arriving: it may not be finished, so it is not a link yet.
      if (streaming && from + start + match[0].length === text.length) continue;
      const url = trimmed(match[0]);
      if (!/^https?:\/\/[^/?#]+/.test(url)) continue;
      words(chunk.slice(at, start));
      parts.push({ text: url, href: url });
      at = start + url.length;
    }
    words(chunk.slice(at));
  };

  let at = 0;
  for (const match of text.matchAll(CODE)) {
    prose(at, match.index);
    words(match[0]);
    at = match.index + match[0].length;
  }
  prose(at, text.length);
  return parts;
}

/**
 * Fill an element with a message, its addresses as links that open in a new tab.
 *
 * Replaces whatever the element held. Safe on every delta of a stream.
 *
 * @param {Element} node
 * @param {string} message
 * @param {{ streaming?: boolean }} [options]
 */
export function fillLinked(node, message, options) {
  const parts = linkParts(message, options);
  if (!parts.some((part) => part.href)) {
    node.textContent = message || "";
    return;
  }
  node.replaceChildren(...parts.map((part) => {
    if (!part.href) return document.createTextNode(part.text);
    const link = document.createElement("a");
    link.href = part.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = part.text;
    return link;
  }));
}
