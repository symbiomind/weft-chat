/**
 * Pictures a weft shows: Weft's own attachment references in a reply.
 *
 * A reply is plain text, and stays plain text. This module recognises one shape inside
 * it, a markdown image whose address is one of Weft's attachments:
 *
 *     ![dashboard](https://weft.example/api/attachments/<sha256>)
 *
 * and lifts it out so the bubble can show the picture with the same code that shows
 * the user's own 📎 pictures. Nothing else in the reply is interpreted. This is not a
 * markdown renderer, and an image pointing anywhere else stays as the words it is.
 *
 * **Only the hash is used.** The host in the address is ignored, so a reference written
 * against the LAN address still shows on the public one. The fetch always goes to this
 * origin, with the viewer's own credential, under the viewer's own directory
 * (app/attachments.py), so a reference can never show anyone a picture that is not
 * already theirs.
 *
 * **The reference is what history keeps.** The turn is stored, forwarded and synced
 * exactly as the weft wrote it. The bytes are never inlined, so a picture costs a
 * session store, or a weft's context, one line of text.
 */

/** A whole reference. Group 1 is the name, group 2 the hash. */
const PICTURE = /!\[([^\]\n]*)\]\([^()\s]*?\/api\/attachments\/([0-9a-f]{64})\)/g;

/**
 * The unfinished start of a markdown image at the very end of the text. While a reply
 * streams, a reference arrives a few characters at a time, and showing its address for
 * the fraction of a second before it completes is a flicker of noise. Held back only
 * while streaming: a finished reply shows every character it has.
 */
const PARTIAL = /!\[[^\]\n]*(?:\]\([^()\s]*)?$/;

/**
 * Split a reply into the words to show and the pictures it references.
 *
 * Returns `{ text, pictures }`, where `pictures` is `[{ hash, name }]` in the order
 * first referenced (a picture referenced twice is shown once), and `text` has the
 * references taken out and the blank lines they leave behind closed up.
 *
 * @param {string} reply
 * @param {{ streaming?: boolean }} [options]
 */
export function picturesIn(reply, { streaming = false } = {}) {
  const pictures = [];
  const seen = new Set();
  let text = (reply || "").replace(PICTURE, (_, name, hash) => {
    if (!seen.has(hash)) {
      seen.add(hash);
      pictures.push({ hash, name: name.trim() || "picture" });
    }
    return "";
  });
  if (streaming) text = text.replace(PARTIAL, "");
  if (pictures.length) {
    text = text
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  return { text, pictures };
}
