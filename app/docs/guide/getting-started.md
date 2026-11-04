---
title: Getting started
id: guide/getting-started
weight: 20
since: "1"
summary: From signing in to your first conversation.
content: |
  Everyone you talk to in Weft is a **weft**: a name, a look, and an engine that
  answers for it. You set up one engine, then a weft to use it.

  1. Sign in. The first time, you choose your own password.
  2. Open the **☰ menu**, then **Providers**, and add an engine.
  3. Open **Wefts**, type a name and tap **add**. On its page, choose the engine
     and a model.
  4. Go back to the chat and say hello.

  Weft keeps nothing between reloads until you ask it to. To keep a scrollback,
  open the weft's page and change **History**.
---

Weft is a window for talking with an AI. This page takes you from a sign-in to a first
conversation. It is written for someone holding the window, on a phone or a desktop.
Whoever runs your server does the rest; if that is you, start with
[Running weft](../service/_index.md).

## What a weft is

Everyone you talk to here is a **weft**. A weft is a name, a pair of colours, a voice
and an engine. The engine is the server that supplies the words, and it can change
without the weft changing: same name, same look, a different engine behind it.

That is why setting up takes two steps, in this order. First an **engine**, which is
where the words come from. Then a **weft**, which is who you are talking to.

## Sign in

Whoever runs your server gives you a username and a first password. Type them in and
tap **sign in**.

The first time, Weft asks you to choose a password of your own before it lets you go
any further. It must be at least 12 characters unless your server's owner has set a
different minimum, and you type it twice. Your first password stops working once you
have done this.

## Add an engine

Tap the **☰ menu** at the top right and choose **Providers**. Under **Engines**, tap
**+ Add** and fill in three things:

- **provider name**: whatever you call it. It is half of the label you will see on the
  Pick menu.
- **provider url**: its address, such as `http://localhost:11434/v1`.
- **bearer token**: its key, if it needs one. Leave it blank if it doesn't.

An engine is any server that answers in the OpenAI chat shape: a model you run
yourself, a gateway, or a hosted service. One engine can answer for as many wefts as
you like.

Your token is stored encrypted on the server, and Weft doesn't show it back to you.
If you edit the engine later, leave the field blank to keep the token you have.

## Make your first weft

Open the menu again and choose **Wefts**. Type a name in the box and tap **add**. A
weft needs nothing else to exist, and its page opens straight away.

On that page, **Engine** is the part to do now. Pick your engine under **provider**,
then tap the model field and choose a model from the list the engine offers. It is
saved as soon as you tap, and you can change it whenever you like. Changing the engine
changes nothing else about the weft.

Further down are the parts that make it yours: **Colours**, a **Background**, a
**Voice**, and the rest. None of them is needed to start, and you can come back to
them whenever you like.

A weft that has no engine yet says so on its row in the Wefts list, and in the chat
bar: *no engine yet: give it one with ⚙*.

## Say hello

Go back to the chat. The bar at the top shows who you are talking to. Tap the name
to switch between wefts, and tap the **⚙** on its right to open that weft's page.

Type in the box and tap send. **Hold send**, or swipe up from it, and a shelf opens
with whatever this weft can use, such as attaching a picture, or talking instead of
typing once you have added a speech provider. A plain tap still sends.

The light at the top shows whether the Weft service is reachable. **Green** means it
is answering. **Amber** means it answers, but some features are off. **Red** means it
doesn't, and nothing you send will arrive until it is back.

## Keeping the conversation

By default, **a reload empties the window**. Weft stores no conversation anywhere
until you tell it to. This is deliberate, and it is the first thing most people
notice.

To keep a scrollback, open the weft's page and find **History**. Under **this window
keeps**, choose **a scrollback on this device**. The conversation then stays in your
browser, and the server doesn't store it. History belongs to each weft, so you set it
for each one.

The other dial on the same page, **each turn sends**, is how much of the conversation
goes to the engine with each message. It starts at the last twenty turns.

## Tips and the menu

The first time through, Weft shows short tips. They point at the next thing to do,
and you can switch them off with **Show tips** in the menu. The menu is also where to
find your **Account** (change your password, see the devices you are signed in on, sign
out), **Comfort** (motion, transparency and the send button), and this **Guide**.

## Where to go next

- [Notifications and your phone](notifications.md): how an agent leaves you a note, and
  how to have your phone ring when it does.
- [A weft with no engine](hollow-weft.md): a weft for an agent that lives somewhere
  else and only needs to reach you.
