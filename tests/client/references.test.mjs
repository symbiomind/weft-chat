/* Tests for web/js/references.js — finding Weft's own pictures in a weft's reply.
 *
 * Only the hash of a reference is ever used, and only one shape is recognised, so most
 * of these cases are about what must NOT be lifted out of the text.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { picturesIn } from "../../web/js/references.js";
import { forSpeech } from "../../web/js/segments.js";

const A = "a".repeat(64);
const B = "b".repeat(64);

describe("picturesIn", () => {
  it("lifts out a reference and keeps the words around it", () => {
    const reply = `Here it is:\n\n![dashboard](https://weft.example/api/attachments/${A})\n\nLooks good?`;

    assert.deepEqual(picturesIn(reply), {
      text: "Here it is:\n\nLooks good?",
      pictures: [{ hash: A, name: "dashboard" }],
      files: [],
    });
  });

  it("ignores the host, and accepts a relative address", () => {
    const lan = picturesIn(`![x](http://10.0.0.5:8080/api/attachments/${A})`);
    const relative = picturesIn(`![x](/api/attachments/${A})`);

    assert.deepEqual(lan.pictures, [{ hash: A, name: "x" }]);
    assert.deepEqual(relative.pictures, [{ hash: A, name: "x" }]);
  });

  it("shows a picture referenced twice once, in first-referenced order", () => {
    const reply = `![two](/api/attachments/${B}) ![one](/api/attachments/${A}) ![again](/api/attachments/${B})`;

    assert.deepEqual(picturesIn(reply).pictures, [
      { hash: B, name: "two" },
      { hash: A, name: "one" },
    ]);
  });

  it("names a picture with no alt text", () => {
    assert.deepEqual(picturesIn(`![](/api/attachments/${A})`).pictures, [
      { hash: A, name: "picture" },
    ]);
  });

  it("leaves every other image and link exactly as written", () => {
    for (const reply of [
      "![cat](https://example.com/cat.png)",
      `[a link](https://example.com/api/attach/${A})`,
      "![short](/api/attachments/abc123)",
      "[short](/api/attachments/abc123)",
      `![upper](/api/attachments/${A.toUpperCase()})`,
      `![other](/api/backgrounds/${A})`,
    ]) {
      assert.deepEqual(picturesIn(reply), { text: reply, pictures: [], files: [] }, reply);
    }
  });

  it("does not touch a reply with no references at all", () => {
    const reply = "  Line one.\n\n\n\nLine two.  ";

    assert.deepEqual(picturesIn(reply), { text: reply, pictures: [], files: [] });
  });

  it("holds back an unfinished reference while streaming, and only then", () => {
    const partial = "Here it is: ![dashboard](https://weft.example/api/attach";

    assert.equal(picturesIn(partial, { streaming: true }).text, "Here it is: ");
    assert.equal(picturesIn(partial).text, partial);
  });

  it("does not hold back an ordinary exclamation mark", () => {
    assert.equal(picturesIn("Done!", { streaming: true }).text, "Done!");
  });

  it("survives empty input", () => {
    assert.deepEqual(picturesIn(""), { text: "", pictures: [], files: [] });
    assert.deepEqual(picturesIn(undefined), { text: "", pictures: [], files: [] });
  });

  it("lifts out a file, a link rather than an image, as a file", () => {
    const reply = `Done!\n\n[Night Drive.mp3](https://weft.example/api/attachments/${B})\n\n🎶`;

    assert.deepEqual(picturesIn(reply), {
      text: "Done!\n\n🎶",
      pictures: [],
      files: [{ hash: B, name: "Night Drive.mp3", kind: "file" }],
    });
  });

  it("keeps pictures and files apart in one reply", () => {
    const reply = `![cover](/api/attachments/${A}) and [song](/api/attachments/${B})`;
    const shown = picturesIn(reply);

    assert.deepEqual(shown.pictures, [{ hash: A, name: "cover" }]);
    assert.deepEqual(shown.files, [{ hash: B, name: "song", kind: "file" }]);
    assert.equal(shown.text, "and");
  });

  it("holds back an unfinished file reference while streaming", () => {
    const partial = "Here: [song.mp3](https://weft.example/api/attach";

    assert.equal(picturesIn(partial, { streaming: true }).text, "Here: ");
  });
});

describe("forSpeech and files", () => {
  it("reads neither a file's name nor its address", () => {
    assert.equal(
      forSpeech(`Here it is: [song.mp3](https://weft.example/api/attachments/${A}) enjoy.`),
      "Here it is: enjoy.",
    );
  });
});

describe("forSpeech and pictures", () => {
  it("does not read a picture's address aloud", () => {
    assert.equal(
      forSpeech(`Here it is: ![dashboard](https://weft.example/api/attachments/${A}) nice.`),
      "Here it is: nice.",
    );
  });

  it("still leaves voice tags alone", () => {
    assert.equal(forSpeech("[voice:zm_yunjian] Hello."), "[voice:zm_yunjian] Hello.");
  });
});
