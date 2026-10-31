---
title: Notifications and your phone
id: guide/notifications
weight: 30
since: "1"
summary: Letters from your agents, and how to have them ring your phone.
content: |
  An agent connected through a weft's door can leave you a note: a job finished,
  a question, a picture. Notes wait in Weft until you dismiss them. A bell shows
  while anything waits, and the count is in the menu.

  To have your phone ring when one arrives, switch on **Notify me on this
  device**. You find it under **Agent door** on a weft's page, and at the bottom
  of the notifications page. It is one switch per device, for every weft, and
  Weft only asks your browser's permission when you tap it.

  The lock screen shows which weft rang and how many are waiting, never what the
  note says. On an iPhone, add Weft to the home screen first.

  Your agent rings your phone because you told it to. That is the whole
  permission model.
---

An agent that is connected to a weft through its door can leave you a note. A long
job finished, a question only you can answer, a screenshot of what went wrong. The
notes wait in Weft, every weft's together, newest first, until you dismiss them.
This page covers how they reach you and how to have your phone ring when one does.

For setting up the agent that sends them, see
[A weft with no engine](hollow-weft.md): any weft's agent can send notes, but that
page walks through it from the start.

## Your agent rings your phone because you told it to

That is the whole permission model, and it is why a ring can be urgent without being
a nuisance. Only an agent you connected to one of your own wefts, through a door
you opened, can leave you a note, and only because you asked it to report in. There
is no stranger on the other end, no advertiser and no priority setting to argue
with. The one thing to guard is the door's key, which is why the
[weft with no engine](hollow-weft.md) page treats it like a password.

On your side it is just as plain: nothing asks for permission when Weft loads. Your
phone asks only when you turn the switch on, and you can turn it off again.

## Where notes show up

A **bell** appears at the top while anything waits, and a weft with news wears a
ring around its colour. The count is in the menu.

Open the notifications page to read them. Tap **Dismiss** on one to clear it, or
**Dismiss all**. A dismissed note is gone for good, on every device.

## Having your phone ring

1. Find the switch **Notify me on this device**. It is under **Agent door** on a
   weft's page, and at the bottom of the notifications page.
2. Turn it on. Your browser asks whether Weft may send notifications; say yes.

The switch is per device and covers every weft, so you turn it on once on your
phone and once on your desktop, not once per weft. It shows one of four states:

- **On.** This device will tell you.
- **Off.** It won't.
- **Blocked.** The browser was told no. Allow Weft's notifications in the site's
  settings (the icon beside the address), then come back and turn it on.
- **Unsupported.** This browser can't do it here. See the next section.

## What your phone needs

Notifications need a secure address: Weft has to be reached over `https`. On an
iPhone, Weft also has to be added to the home screen first (iOS 16.4 or later), and
the switch is turned on from there.

## When it rings

- **Weft is open on this device.** The bell shows, and the device also rings, unless
  you are already looking at the notifications page. A hidden tab can be up to a
  minute late.
- **Weft is closed, the screen is off, the phone is in your pocket.** This takes
  *push*, which the server has to have turned on. If it has, the phone rings
  anyway. If it hasn't, the device is only told while Weft is open in a tab. The
  hint under the switch says which applies to you.

Push rings for a weft at most once a minute. If several notes arrive in that minute,
one ring follows at the end of it, with the count as it is then.

## What the lock screen shows

Who, never what. The ring names the weft and how many of its notes are waiting. The
text of a note stays inside Weft, because a lock screen is read by whoever picks the
phone up.

The icon is the weft's own: a square from the middle of its background picture, with
its ring in its two colours. A weft with no picture wears the ring over the
theme's plain background, and one with no colours wears the theme's pair. On an iPhone you may see
Weft's own icon instead of the weft's. That is the phone's choice, not a fault.

Tap a ring to open Weft. If Weft is already open, it comes to the front and opens
the notifications page.

## If you run the server

Push is **off by default**, because it goes through your browser vendor's push
service: Google's for Chrome, Mozilla's for Firefox, Apple's for Safari. They can't
read the note, which is encrypted to the device, but they do see that one was sent,
to which device, and when. Turning it on is a knowing choice for the person who runs
the server. [Turning on push](../service/push.md) covers what it costs in privacy and
how to switch it on.

## Three things that surprise people

**A shared browser doesn't ring for the last person.** A device's ring belongs to the
account signed in on it. Sign out, or let the sign-in lapse, and that device stops
ringing.

**The icon can be fetched without signing in.** Your phone fetches it that way, so
anyone holding its exact address could see it. The address can't be guessed from
outside, and the icon is a small square from the middle of the picture, which shows
on your lock screen anyway.

**One ring can mean several notes.** Push folds many small notes from an agent into
one ring a minute, and a ring only says how many are waiting. Open Weft to read
them.
