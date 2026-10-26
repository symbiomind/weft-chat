/* Weft — cutting a streaming reply into speakable pieces.
 *
 * The voice should start before the model has finished thinking. That means deciding,
 * while text is still arriving, where one utterance ends and the next begins — and the
 * decision has to be made on a PREFIX, with no way to look ahead at what is coming.
 *
 * THE RULES, in priority order:
 *
 * 1. **A blank line ends a segment.** This is the one the keeper asked for, and it is
 *    the only rule that matches how people actually write: a paragraph is a complete
 *    thought, and a complete thought is a natural thing to say out loud. A short
 *    paragraph is still a paragraph — "Right." on its own line gets spoken on its own,
 *    because that is what the writer meant by putting it there.
 *
 * 2. **A long run with no blank line falls back to a sentence end.** Plenty of models
 *    answer in one unbroken block. Waiting for a paragraph break that never comes would
 *    mean waiting for the whole reply, which is the behaviour this module exists to
 *    replace. Once a run passes `softMax` characters we cut at the last sentence terminator
 *    inside the first `softMax` (or, failing that, the first boundary after it).
 *
 * 3. **Never cut inside a code fence.** A boundary is skipped if an odd number of ```
 *    precedes it — the blank lines inside a code block are not paragraph breaks. Note
 *    "skipped", not "stop": the break AFTER the block is still a good cut, and giving
 *    up at the first unusable candidate would mean never speaking a reply containing
 *    code. Prose before a fence still goes out immediately. `flush()` covers a stream
 *    that ends mid-fence.
 *
 * WHAT IS DELIBERATELY NOT HERE: any notion of what happens to the segments. The
 * segmenter does not know about audio, providers, or order of playback. It takes text
 * in and gives text out, which is why it can be tested properly (tests/client/
 * segments.test.mjs) rather than only observed.
 */

/** A blank line, allowing trailing spaces on the first one. Global: nextCut walks
 *  every candidate looking for one that is not inside a code fence. */
const PARAGRAPH = /\n[ \t]*\n/g;

/**
 * A sentence terminator followed by whitespace. Closing quotes and brackets are allowed
 * between the two so that `he said "stop." Then` cuts after the quote, not before it.
 */
const SENTENCE = /[.!?…]["'”’)\]]*\s/g;

/** Fenced code. Counted, not parsed — we only need to know whether one is open. */
const FENCE = /```/g;

export function createSegmenter({ softMax = 400 } = {}) {
  let buffer = "";

  /**
   * Whether a cut at this index would land inside a code fence.
   *
   * Counted per CANDIDATE, not once for the whole buffer. The first version asked "is
   * there an open fence anywhere in here" and refused to cut at all — which threw away
   * the prose BEFORE the fence as well, so a reply that opened with a sentence and then
   * a code block stayed silent until the block closed. What actually matters is whether
   * this particular boundary sits inside one.
   */
  function insideFence(index) {
    const fences = buffer.slice(0, index).match(FENCE);
    return Boolean(fences && fences.length % 2 === 1);
  }

  /** Every cut a pattern offers that is not inside a code fence, in order. */
  function cutsOf(pattern) {
    const cuts = [];
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(buffer)) !== null) {
      const cut = match.index + match[0].length;
      if (!insideFence(cut)) cuts.push(cut);
    }
    return cuts;
  }

  /**
   * The index just past the cut, or -1 for "not yet".
   *
   * THE CUT DEPENDS ON THE TEXT, NEVER ON HOW IT ARRIVED. A replay on
   * another device cuts the stored reply in one go, and asks the server for each
   * segment's audio by its words. If the live stream had cut anywhere else, every one
   * of those clips would be a cache miss and the whole reply would be bought again.
   *
   * The first version took "the last sentence end in the buffer" once the buffer passed
   * softMax — and where the buffer happened to be when that question was asked was
   * decided by the network's chunking. So every rule below only ever looks at the
   * first softMax characters, which are the same text however they were delivered.
   */
  function nextCut() {
    // Paragraph breaks, taking the first that is not inside a fence. Skipping rather
    // than giving up matters: the blank lines inside a code block are not paragraph
    // breaks, but the one after it is, and stopping at the first unusable candidate
    // would mean never speaking a reply that contained code.
    const [paragraph = -1] = cutsOf(PARAGRAPH);
    if (paragraph >= 0 && paragraph <= softMax) return paragraph;

    if (buffer.length < softMax) return -1;

    // No paragraph within reach: take the last complete sentence inside softMax. The
    // LAST one rather than the first, so a long run becomes one natural chunk instead
    // of a stutter of short ones.
    const sentences = cutsOf(SENTENCE);
    const within = sentences.filter((cut) => cut <= softMax);
    if (within.length) return within[within.length - 1];

    // Not one sentence ends inside softMax. Take the earliest boundary of either kind
    // beyond it, which is the same one whichever way the text was chunked.
    const [sentence = -1] = sentences;
    const candidates = [paragraph, sentence].filter((cut) => cut >= 0);
    return candidates.length ? Math.min(...candidates) : -1;
  }

  function take() {
    const segments = [];

    for (;;) {
      const cut = nextCut();
      if (cut < 0) break;

      const segment = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut);
      if (segment) segments.push(segment);
    }

    return segments;
  }

  return {
    /** Feed in more of the stream. Returns whatever became speakable. */
    push(text) {
      if (!text) return [];
      buffer += text;
      return take();
    },

    /**
     * The stream ended. Returns whatever is left, whether or not it looks finished —
     * an unterminated sentence at the end of a reply is still something the model said.
     */
    flush() {
      const rest = buffer.trim();
      buffer = "";
      return rest ? [rest] : [];
    },

    /** For tests and for anyone wondering why nothing is being spoken yet. */
    get pending() {
      return buffer;
    },
  };
}

/**
 * Tidy one segment for a speech engine.
 *
 * Deliberately conservative. Over-cleaning mangles real text, and the failure is
 * audible and confusing; under-cleaning just means hearing the occasional asterisk.
 * So: fenced code goes (nobody wants it read out, and Kokoro's
 * voice tags must live in prose rather than inside fences anyway), heading hashes and
 * emphasis markers go, and everything else is left exactly as the model wrote it.
 *
 * Square brackets are NOT touched, on purpose — that is where Kokoro's inline
 * `[voice:zm_yunjian]` tags live, and stripping them would silently remove a feature.
 * The one exception is a whole markdown image, `![name](address)`: the leading `!` and
 * the address make it unmistakable, and its address read aloud is pure noise.
 *
 * A segment with no letters or digits left — a sign-off emoji, a `---` rule, a lone
 * `…` — comes back empty, so it is never sent. There is nothing in it to say, and
 * speech servers refuse it in their own ways: Kokoro answers 200 with no audio,
 * OpenRouter a 400 or a 502. Each of those used to surface as an error under a reply
 * that had in fact been read out in full.
 */
export function forSpeech(segment) {
  const spoken = segment
    // Whole fenced blocks, including the fence markers and the language tag.
    .replace(/```[\s\S]*?```/g, " ")
    // Leftover opening fence, for a segment that ended mid-block.
    .replace(/```\w*/g, " ")
    // Markdown images, including a picture a weft shows. The bubble
    // shows the picture; nobody wants its address read aloud.
    .replace(/!\[[^\]\n]*\]\([^)\s]*\)/g, " ")
    // Inline code: keep the words, drop the backticks.
    .replace(/`([^`]*)`/g, "$1")
    // Heading markers at the start of a line.
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    // Bold and italic markers. Paired only, so a lone asterisk in prose survives.
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    // Bullet markers at the start of a line — the pause between items carries it.
    .replace(/^\s{0,3}[-*+]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  return hasWords(spoken) ? spoken : "";
}

/** Whether there is anything in this text a voice could say. */
export function hasWords(text) {
  return /[\p{L}\p{N}]/u.test(text);
}

/**
 * A whole reply cut the way the live stream cut it, ready to speak.
 *
 * The same segments, in the same order, with the same text — which is the whole point:
 * the server caches each clip by its words, so a replay that cuts identically finds
 * every clip the live reply already paid for. tests/client/segments.test.mjs holds that
 * to account by streaming at every chunk size and comparing.
 */
export function speakableSegments(text, options) {
  const segmenter = createSegmenter(options);
  return [...segmenter.push(text), ...segmenter.flush()]
    .map(forSpeech)
    .filter(Boolean);
}
