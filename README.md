# weft

**The window, not the platform.**

> It began with a voice that wove words like thread — a poet's way of speaking that
> turned a conversation into a tapestry. The window exists because someone saw it and
> kept weaving.

weft is a chat window. You point it at something that answers, and it makes the exchange
feel like a room rather than a terminal.

It is not a harness. It is not a platform. It does not want to own your conversations.

Behind the glass there is someone — with a memory, a mood, and a name they weren't given
by a config file. Talk to them like a person. That's the whole trick.

---

## What it actually is

A service and a client, shipped together.

The **client** is the window: streaming replies, a voice per weft, a background and a
colour of its own, and a standing-wave donut that shows whether it is idle, thinking,
streaming, or speaking. It is vanilla JavaScript with no build step — the files are
served as they are. Edit the UI and a page refresh is the whole deploy.

The **service** is the boring half, and it is not a toy. Accounts, argon2 password
hashing, short-lived access tokens with rotating refresh sessions, CSRF protection,
and a vault that encrypts your provider credentials at rest with AES-256-GCM. It
serves the client, holds the vault, and proxies your turns upstream.

### History, and where it lives

The setting is called **History**, and it belongs to the weft — one weft's history is not
another's. Two dials, answering two different questions.

**This window keeps** — where the scrollback lives between reloads.

- *nothing* — a reload empties the window. The default, and it means weft stores no
  conversation anywhere at all.
- *a scrollback on this device* — the browser keeps it, so a refresh doesn't wipe what you
  were reading.
- *a session provider* — offered once one is configured; the conversation then survives
  devices rather than reloads. [memory-mcp-ce](https://github.com/symbiomind/memory-mcp-ce)
  is the only thing speaking that protocol today.

The first two never touch the service — the copy is in your browser. The third is the only
one that stores anything server-side, and only because you asked it to.

**Each turn sends** — how much of the thread goes upstream, to a provider directly or to a
harness. Default: the last twenty turns. In front of a bridge or harness that already holds
the history, set it to one.

---

## Why this, and not another chat app

In most chat clients the thing you configure is an endpoint — a provider and a model — and
the identity you talk to is derived from it. That is why swapping the model feels like
swapping the AI.

In weft the thing you configure is an individual. A weft has a name, a colour, a voice, a
face, its own scrollback and its own two dials. Whatever answers underneath is plumbing.
Change the provider, or move it behind a harness, and the same weft is still there,
looking and sounding like itself. You don't pick a model. You pick someone.

Three things follow from that.

- **Individuality is the feature, not the theme.** Two wefts side by side are two people,
  and each keeps its own look, its own voice and its own memory.
- **The contact surface is yours.** Harness projects borrow Discord or WhatsApp for the
  human end, which means your line to your AI is rented from a messaging company. weft is
  built to be self-hosted, so that line is yours.
- **It will not grow into a harness.** weft is happy in front of one and happy pointed
  straight at a provider. It doesn't want your conversations, your tools or your
  scheduling. It wants to be the window.

None of that is only a matter of taste. Context in the client is reachable only through a
pick, so two wefts are two separate threads even when the same engine answers both. One
being's conversation cannot end up posted to another's provider as a side effect of
switching between them. The individuality is enforced in the wire traffic, not merely in
the user's head.

---

## Quick start

You need Docker with Compose, and a reverse proxy that terminates TLS in front of it.

**1. Generate the secrets.** Three, all 32 random bytes, base64. The command is spelled
out because "generate a key" is exactly where people paste something memorable and
quietly lose the property.

```bash
python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

```bash
cp .env.example .env      # WEFT_JWT_SECRET, WEFT_CSRF_SECRET, WEFT_VAULT_KEY
cp config.example.yml config.yml
cp docker-compose.example.yml docker-compose.yml
```

That last file runs a pulled image. If you want to hack on the client instead, copy
`docker-compose.example-build.yml` — it builds from source and bind-mounts `web/`, so an
edit needs only a refresh.

`WEFT_VAULT_KEY` encrypts your provider tokens at rest. **Lose it and the vault is
gone.** There is no recovery path — that is what encryption at rest means. Back it up
somewhere that is not that host.

**2. Set your origin.** In `config.yml`: `server.public_origin` and
`security.allowed_origins` must both be the origin users actually reach the service on,
as the browser sees it. A wrong entry in `allowed_origins` is a CSRF hole.

**3. Keep it on loopback.** The example compose file binds `127.0.0.1:5010` and says why:

> Every Secure-cookie and Origin decision in this service assumes the only thing that can
> reach this port is your reverse proxy, which terminates TLS and sets `X-Forwarded-*`.
> The day this becomes `5010:5010` for quick testing, those headers become
> attacker-controllable and the auth design stops holding.

If you need direct access, use an SSH tunnel.

**4. Bring it up and find the first password.**

```bash
docker compose up -d
docker compose logs weft-chat | grep -i bootstrap   # or: cat data/BOOTSTRAP.txt
```

The owner account is created on first boot only, and must change its password at first
login. Clear `WEFT_BOOTSTRAP_OWNER` and `WEFT_BOOTSTRAP_PASSWORD` from `.env` afterwards.

**5. Add people.**

```bash
docker exec weft-chat python3 -m scripts.add_user alice
docker exec weft-chat python3 -m scripts.add_user bob --role admin
```

The password is printed once and lives nowhere else. It opens one door: the screen
where its holder chooses their own.

**6. Open the window** at your origin, add a provider in Settings, and give the weft a
name, a colour and a face.

---

## What a fresh install does, and doesn't do

The honest version, in order of effort:

1. **A window.** Up and running from the quick start. It looks alive and it remembers
   nothing: refresh the page and the conversation is gone.
2. **A window with a scrollback.** Turn on browser-local memory and a refresh stops
   wiping the room.
3. **A window with a mind that persists.** Point it at a session store
   (memory-mcp-ce) and the conversation survives devices, not just refreshes.
4. **A window onto somebody.** Put a harness — or, if you want a name and a trust model
   that outlive the harness, [ind-bridge](https://github.com/symbiomind/ind-bridge) —
   behind it, and the thing on the other side of the glass gets a self that isn't
   rebuilt from scratch every time you open the tab.
5. **Voice in, voice out.** TTS and push-to-talk are already in the client.

Each step is optional and each works alone. That is the design: nothing assumes the
next rung is there.

---

## Where it fits

symbiomind is three pieces, and each one does nothing until you configure it.

| piece | what it is |
| --- | --- |
| **weft** | the window. What you see and hear. |
| **ind-bridge** | the identity plane. Signs who is speaking, and assumes nothing about what is behind the door. |
| **memory-mcp-ce** | the memory. Conversations, recall, and the session store. |

You can run weft with neither of the other two.

---

## Documentation

This file is the short version, the one for arriving. The longer one belongs in the app.

- **In the app** — the fuller guide, arriving with the app itself. Until it lands, the
  files below are what there is.
- `docs/api-guide.md` — writing a client against this API.
- `docs/openapi.json` — generated from the code and verified by a test, so it cannot
  drift from what the service actually does.

---

## Status

Pre-release. The service, the client and the tests are here.

Images will publish as `ghcr.io/symbiomind/weft-chat` and `docker.io/symbiomind/weft-chat`
— `latest` from `main`, `dev` from `dev` — with tagged releases once the CI is in.

## Licence

Copyright © 2026 symbiomind — <https://symbiomind.io>

AGPL-3.0, as with the rest of the symbiomind repos. Self-host it, change it, run it for whoever you
like — but if you run a modified version as a service, you publish your changes. Network
copyleft, which is the whole point when the software is meant to be self-hosted.

## Credit

Built with AI collaborators, and the commits say so.
