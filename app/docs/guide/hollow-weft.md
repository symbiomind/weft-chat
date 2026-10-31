---
title: A weft with no engine
id: guide/hollow-weft
weight: 40
since: "1"
summary: A weft that only rings your doorbell, for an agent that lives somewhere else.
content: |
  A weft doesn't need an engine. Make one, leave its engine empty, and give its
  agent the key to the door: that agent can now leave you notifications under
  its own name and colours.

  On the weft's page, under **Agent door**, tap **invite the agent**, then
  **copy** and paste the invite into your agent wherever you talk to it. It holds
  no key, works once, and lapses after fifteen minutes, so paste it soon.

  There is no chat to read. Your agent answers you wherever it already does.
---

A weft is usually a conversation: you write, an engine answers. But the door that
lets an agent reach you does not care whether there is an engine behind it. Make a
weft, give it no engine, open its door, and hand the key to any agent that speaks
MCP. That agent now has a name, a pair of colours and its own icon on your lock
screen, and a way to tap you on the shoulder. Nothing else: it cannot see a chat,
because there isn't one.

We call this a **hollow weft**. It is the doorbell, not the conversation. Your
agent keeps talking to you wherever it lives now, in a terminal or a chat app of
its own, and uses the weft to say *come and look*.

## What it is for

A notification channel with a face, on a server you run yourself. Anything that
can call an MCP tool can report in:

- a coding agent ringing you when a long job finishes or needs a decision,
- a backup, a CI run, a cron script or a home-automation hook,
- an agent whose own home only offers a messaging app, and no way to reach your
  phone as itself.

If you want to talk to the agent *in* Weft, give the weft an engine instead, from its page.

## About the key

Opening the door makes a **key**. Whoever holds it can leave notifications under
this weft's name, so treat it like a password.

The invite route below keeps the key off your screen: the agent reads the invite
once, collects its own key, and the invite is dead afterwards. It also works when
you have no access to the agent's configuration, only to its conversation: if you
can talk to it, you can connect it. Prefer it.

If your agent can't be given an invite (it can't fetch a web address, say), there is
a manual route under **Advanced**. It shows you a guide with the key inside it, once.
**Save that guide outside any project folder.** A file saved into a project under
version control is one `git add` from being published. Keep it somewhere that
belongs to no project, or paste the key straight into your agent's MCP
configuration and discard the rest.

If a key does leak, act on the weft's page straight away. **Make a key** under
**Advanced** replaces the old one at once, and the old one stops working. **Close
the door** shuts it altogether. Inviting the agent again also replaces the key, but
only when the agent reads the new invite: until then the old key still works, so it
is not the quick fix.

## Setting it up

1. Make a new weft and leave its engine empty. In the list it reads *no engine
   yet*, and that is fine.
2. Open the weft's page (the ⚙ beside its name on the bar) and find **Agent
   door**. Dress the weft there too: its colours are what your agent wears when it
   rings you.
3. Tap **invite the agent**, then **copy**. Ignore **put it in the chat**: a hollow
   weft has no chat to put it in. The invite lapses after fifteen minutes, so copy
   it when you are ready to paste it. If it lapses, tap the button again.
4. Paste the invite to your agent, in the terminal or app where you already talk to
   it. It fetches the page, which tells it how to add Weft's MCP server.
5. Many agents read their MCP servers only at startup, so restart yours if the
   tools don't show up. Ask it to call `status`: it should name your weft.
6. To get the ring on your phone, switch on **Notify me on this device**. It is one
   switch per device and covers every weft, so if it is already on, there is
   nothing more to do. If the server has push turned on, the phone rings even with
   Weft closed; otherwise it rings only while Weft is open in a tab, and a hidden
   tab can be a minute late. The hint under the switch says which applies. Either
   way, the lock screen shows which weft rang and how many are waiting, never what
   the note says.

For an agent that can't take an invite, use **Advanced**, then **make a key**, and
follow the guide it shows.

## What your agent can do

It gets the same four tools as any weft's agent:

- `status` says which weft it is and how many of its notifications are still
  waiting for you. It is a good first call to prove the connection works.
- `notify` leaves you a note: a short title, and a body saying what happened.
- `upload` gives a one-time address to send a picture to, so a note can carry a
  screenshot.
- `view` looks again at a picture shown in a conversation. A hollow weft has no
  conversation, so there is nothing for it to look at.

None of them can read or write a chat thread. A hollow weft's agent therefore has
fewer powers than a full one, not more, and there is nothing extra to lock down.

## Three things that surprise people

**The guide still talks about a chat.** It is written for wefts that have one, and
tells the agent to show you pictures "in your replies". Your agent has no replies
in Weft. Tell it plainly that this weft is a doorbell, and that you read it through
notifications.

**There is nowhere to answer.** A note can ask a question, but the answer goes back
the way you already talk to the agent. The weft's own chat window will look
unfinished, because it has nothing to talk to.

**Notes are limited.** A weft can send thirty an hour, and only the newest hundred
wait for you, so many small reports bury each other. Ask your agent to say what you
would want to read when you get back, not every step.

## A note on the name

In weaving, a *supplementary weft* is the extra thread worked in for pattern and
colour. It carries none of the cloth's structure. A weft with no engine is that
thread: it adds the colour and holds nothing up.
