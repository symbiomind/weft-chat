---
title: Under the send button
id: guide/send-shelf
weight: 40
since: "1"
section: weft/hearing
summary: Attach pictures and talk instead of typing, from the shelf behind send.
content: |
  Behind the send button is a shelf with two extra ways to send: **attach** a
  picture, and **push to talk**.

  **Hold send**, or swipe up from it, and the shelf opens. It stays open, even
  after a reload, until you close it the same way: hold send again, or swipe down.
  A plain tap on send always sends.

  The paperclip opens your device's picker; pick up to six pictures and they wait
  above the text box, each with an ✕ to take it back. To talk instead of typing,
  **hold the microphone**, speak, and let go.

  A petal only shows when the weft can use it. The microphone needs a
  speech-to-text provider (see **Push to talk** under Providers), and neither
  petal shows for a weft with no engine yet.
---

Most of chat is typing and pressing send. This page is about the part you can't see:
the shelf that hides behind the send button.

## Opening the shelf

**Hold the send button** for a moment, or **swipe up** from it, and the shelf opens
above it. On a desktop, right-clicking send does the same.

The shelf then stays open, across sends and across reloads, until you close it. Hold
send again, or swipe down on send or anywhere on the shelf. Tapping outside the shelf
does nothing, and neither does sending a message. Open it once and push to talk stays
one tap away for the rest of the conversation.

While the shelf is closed, a small **+** on the send button bobs now and then as a
reminder that it is there.

A tap on send still sends, whether the shelf is open or closed. The only taps that
don't are the let-go at the end of a hold or a swipe, so a hold never posts whatever
is in the box by accident. The shelf also keeps working while a reply is still
arriving, even though send is greyed out then.

## Which petals show

Each button on the shelf is a **petal**. There are two, and a weft only shows the ones
it can use:

| Petal | Shows when |
|---|---|
| Attach (paperclip) | The weft has an engine. Not for ELIZA. |
| Push to talk (microphone) | The weft has an engine, and something can hear you: your speech-to-text provider, or one chosen for this weft. Not for ELIZA. |

If neither petal applies, the shelf doesn't open at all. It comes back by itself the
next time you pick a weft that can use one. A weft's row in the Pick menu shows what
it can do in a row of small icons, and the microphone is one of them.

## Attaching pictures

Tap the paperclip. Your device's own picker opens; on a phone that usually offers the
camera, the photo library and files. Only pictures can be chosen, up to six on one
message.

Each picture uploads as soon as you pick it, so a slow upload happens while you're
still typing. It sits in a row of thumbnails above the text box with an ✕ to take it
back out, and goes with your next message.

## Push to talk

Hold the microphone petal and speak. Let go, and your recording is sent off to be
turned into text. The microphone is open only while you hold; weft never keeps it
listening between presses. A recording stops by itself after two minutes.

- **To cancel,** slide your finger well off the petal before you let go.
- **A quick tap** only says "hold to talk". A recording too short to hold a word
  counts as a mis-tap.
- **The first time,** your browser asks to use the microphone. The press that raised
  the question doesn't record, so hold again once you've allowed it.

What you said lands in the text box first, with "check it, then send" on the petal,
so you can fix any words before they go. If you already typed something, your spoken
words are added after it. If you would rather it sent the moment you let go, switch on
**send as soon as I let go**.

### Setting it up

Push to talk needs a speech-to-text provider: a whisper server, or anything that
answers the OpenAI `/audio/transcriptions` shape. Without one, the microphone never
appears.

1. Open the **☰ menu**, then **Providers**.
2. Under **Push to talk**, tap **+ Add** and fill in the form, as for an engine in
   [Getting started](getting-started.md).
3. Once there is a provider, the same section shows its settings: the **model**
   (blank sends `whisper-1`), the **language** (blank lets the provider work it
   out), **send as soon as I let go**, and **tell the weft it was spoken**. These save
   as you change them.

The provider you set here is used by every weft. To give one weft different ears, or
turn push to talk off for it, open that weft's page and use **Hearing**, choosing
under **hear me with**. A provider of its own gets its own **model** field there; the
language and the two switches still come from your account.

**Tell the weft it was spoken** puts `[spoken]` in front of what the weft receives,
so it can forgive the odd word the transcription got wrong. You never see it.
