---
title: Turning on push
id: service/push
weight: 10
since: "1"
summary: Let a phone be rung while Weft is closed, and what that costs in privacy.
---

Without push, a device is told about a notification only while Weft is open in a tab.
With it, a phone rings with Weft closed and the screen off. Push is **off by default**,
because it goes through the browser vendor's push service, and turning it on is your
choice as the person running the server. This page covers what that choice means and
how to make it.

For what your users see, and how they switch a device on, see
[Notifications and your phone](../guide/notifications.md).

## What it costs in privacy

A push goes through the vendor's service: Google's for Chrome, Mozilla's for Firefox,
Apple's for Safari and for an iPhone's home-screen app, Microsoft's for Edge on
Windows. They cannot read it. The body is encrypted to the device that subscribed
(RFC 8291), and it carries only the weft's id and name, how many notes are waiting and
the name of its icon, never the text of a note.

What they can see is that a push was sent, to which device, and when. If that is more
than you want a vendor to know about your server, leave push off. Devices are still
told while Weft is open in a tab.

## What you need

- **Weft reached over `https`.** Browsers only allow push from a secure address, and
  Weft already assumes a reverse proxy that terminates TLS.
- **A key.** A VAPID private key, which proves to the push services that a push is from
  your server.

## Turning it on

1. Make a key, in the running container:

   ```
   docker exec weft-chat python3 scripts/make_push_key.py
   ```

   It prints a `WEFT_PUSH_KEY=` line and, as a comment, the public key that goes with
   it. The public key is only there so you can recognise the pair; the server works it
   out from the private one and hands it to browsers itself.
2. Add the `WEFT_PUSH_KEY=` line to your `.env`. Keep the key in the environment and
   out of `config.yml`.
3. In `config.yml`, under `push`, set `enabled: true`. The example config reads the key
   from `${WEFT_PUSH_KEY}`.
4. Optionally set `subject` to a `mailto:` or `https:` address the push services can
   contact about your server. Left empty, it is your `server.public_origin`.
5. Recreate the container so the new `.env` is read.

## Checking it works

Open Weft on a device and look at the hint under **Notify me on this device**. When
push is working it says the device is rung "even with Weft closed". Without push, it
says the device is told only while Weft is open in a tab.

If you set `enabled: true` and the hint still says that, look in the log. A missing key
logs "push.enabled is true but push.key is empty", and a key that is not valid logs
that too. A bad key turns off push and nothing else.

## Things that surprise people

**A new key cuts off every device.** Each subscribed device was told which key to
expect. If you replace the key, they stop ringing until each one visits Weft again and
subscribes with the new one. Keep the key, and back it up with the rest of your
environment.

**Only some push services are allowed.** A browser hands the server the address of its
push service, and the server posts to it, so a signed-in user could otherwise aim the
server at any host it can reach. Weft therefore only talks to the services on
`push.allowed_hosts`, which by default covers Chrome, Chromium builds, Firefox, Safari
and Edge. A browser that uses another service is refused until its host is added.

**The icons must be reachable without a sign-in.** A device fetches a weft's icon from
`/api/push/icons/` with no login, so a reverse proxy that puts basic auth or another
gate in front of all of Weft will stop icons from showing. Leave that path open. The
icon's name is not guessable, and it is a small square from the middle of the weft's
background, which shows on a lock screen anyway.

**A shared browser doesn't ring for the last person.** A subscription belongs to the
account signed in on that device, and a device that signs out or lapses is skipped.
