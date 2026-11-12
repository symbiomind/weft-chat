/* Tests for web/js/links.js — bare web addresses in a message, made clickable.
 *
 * Most cases are about where an address ends: what must stay inside the link (a query
 * string, a balanced bracket) and what must not (a full stop, a sign-off emoji).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { linkParts } from "../../web/js/links.js";
import { parseFrame } from "../../web/js/session.js";
import { forSpeech } from "../../web/js/segments.js";

const SONG = "https://suno.com/song/a27dd0c7-6097-49a5-b0ef-b17b190bb2d2?sh=fhsI1ojIGA2D42XL";

/** Only the links, as their addresses. */
const hrefs = (message, options) =>
  linkParts(message, options).filter((p) => p.href).map((p) => p.href);

describe("linkParts", () => {
  it("links a bare address with its query string whole", () => {
    assert.deepEqual(linkParts(`Here's the song: ${SONG}`), [
      { text: "Here's the song: " },
      { text: SONG, href: SONG },
    ]);
  });

  it("gives the message back exactly when the parts are joined", () => {
    const message = `One ${SONG}. Two (https://a.example/x), and \`https://b.example\` 🎶`;
    assert.equal(linkParts(message).map((p) => p.text).join(""), message);
  });

  it("leaves a message with no address as one piece of words", () => {
    assert.deepEqual(linkParts("just words, no http here"), [{ text: "just words, no http here" }]);
    assert.deepEqual(linkParts(""), []);
  });

  it("does not swallow the punctuation that ends a sentence", () => {
    for (const end of [".", ",", "!", "?", ";", ":", "...", "'", "\"", "!)"]) {
      assert.deepEqual(hrefs(`listen: ${SONG}${end}`), [SONG], `ending ${end}`);
    }
  });

  it("stops at an emoji right after the address", () => {
    assert.deepEqual(hrefs(`${SONG}🎶✨`), [SONG]);
    assert.deepEqual(hrefs(`${SONG} 🎶`), [SONG]);
  });

  it("drops an unpartnered closing bracket and keeps a partnered one", () => {
    assert.deepEqual(hrefs("(see https://a.example/page)"), ["https://a.example/page"]);
    assert.deepEqual(
      hrefs("https://en.wikipedia.org/wiki/Epoxy_(disambiguation)"),
      ["https://en.wikipedia.org/wiki/Epoxy_(disambiguation)"],
    );
  });

  it("takes the address out of a markdown link without its bracket", () => {
    assert.deepEqual(hrefs("[the song](https://a.example/s)"), ["https://a.example/s"]);
  });

  it("stops at angle brackets and quotes around an address", () => {
    assert.deepEqual(hrefs("<https://a.example/x>"), ["https://a.example/x"]);
    assert.deepEqual(hrefs('"https://a.example/x"'), ["https://a.example/x"]);
  });

  it("finds several addresses, and plain http", () => {
    assert.deepEqual(
      hrefs("http://a.example and https://b.example/y?z=1#top"),
      ["http://a.example", "https://b.example/y?z=1#top"],
    );
  });

  it("does not link inside a code span or a fenced block", () => {
    assert.deepEqual(hrefs("run `curl https://a.example/x` now"), []);
    assert.deepEqual(hrefs("```\ncurl https://a.example/x\n```\nthen https://b.example"), [
      "https://b.example",
    ]);
  });

  it("treats a fence still open as code to the end", () => {
    assert.deepEqual(hrefs("```sh\ncurl https://a.example/x", { streaming: true }), []);
  });

  it("treats an unpaired backtick as a backtick", () => {
    assert.deepEqual(hrefs("it's ` https://a.example"), ["https://a.example"]);
  });

  it("needs a host, and a scheme that starts a word", () => {
    assert.deepEqual(hrefs("https:// is a scheme"), []);
    assert.deepEqual(hrefs("xhttps://a.example"), []);
    assert.deepEqual(hrefs("ftp://a.example javascript:alert(1)"), []);
  });

  it("holds back an address still arriving at the end of a stream", () => {
    assert.deepEqual(hrefs("listen: https://suno.com/so", { streaming: true }), []);
    assert.deepEqual(hrefs(`listen: ${SONG} `, { streaming: true }), [SONG]);
    assert.deepEqual(hrefs(`listen: ${SONG}`), [SONG]);
  });

  it("links the same address however the stream was chunked", () => {
    const reply = `Done! ${SONG} 🎶\n\nEnjoy.`;
    for (let size = 1; size <= reply.length; size += 1) {
      let seen = "";
      for (let at = 0; at < reply.length; at += size) {
        seen += reply.slice(at, at + size);
        for (const href of hrefs(seen, { streaming: true })) assert.equal(href, SONG);
      }
      assert.deepEqual(hrefs(seen), [SONG], `chunks of ${size}`);
    }
  });
});

describe("a keep-alive on the stream", () => {
  it("is a comment the frame parser drops, so it never reaches a bubble", () => {
    assert.equal(parseFrame(": keep-alive"), null);
  });
});

describe("forSpeech and addresses", () => {
  it("reads an address as its host", () => {
    assert.equal(forSpeech(`Here's the song: ${SONG} enjoy`), "Here's the song: suno.com enjoy");
    assert.equal(forSpeech("see https://www.example.com/a/b."), "see example.com.");
  });
});
