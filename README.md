# Morrow AI

Your own assistant, on your own cloud: ask it about anything you can see
here, and have it build Quills of your own. An administrator connects
Claude once, for everybody.

A [Quill](https://github.com/Cloudmorrow/cloudmorrow/blob/main/docs/QUILLS.md)
for Cloudmorrow: install it from the Quill Catalog, or from this repository
with `cm quill add --source https://github.com/Cloudmorrow/quill-morrow --ref v1.2.0`.
It needs Cloudmorrow 0.81.0 or later on the server (an assistant's tools
for a Quill's code arrived in 0.76.0; the conversation, the switch and the
list it is drawn with, in 0.80.0 and 0.81.0).

## What it is

Morrow is a conversation, drawn as a chat: your conversations in a list
down the left (as Notes has its pages), the open one on the right with the
hedgehog faintly behind it, what you said in a bubble on your side and
Morrow's answer on the other, the box to write in on the floor. Enter
sends; what you said shows at once while Morrow thinks. The pen beside the
title clears the screen for a new conversation; the old one stays in the
list. What you say is
written down, Morrow looks at what it needs and answers, and the answer is
written down beside yours with what it looked at — *Looked at:
list_records task where lane=doing* — so every answer says where it came
from. Conversations are yours alone, sealed at rest like every record, kept
until you delete them (a conversation's row renames or deletes it, from
its menu or its sheet).

**It reads what you can read, as you.** Morrow has what an assistant of
yours over MCP has: the record tools over every datamodel your circles
give you — tasks, notes, contacts, events, files, whatever the installed
Quills add — never a secret, never anybody else's, each read as you,
through the same gate as everything else. Nothing is declared per
datamodel: the install sheet says *has an assistant's tools, as you*, and
that is what is said yes to.

**Reading only, until you say otherwise.** Behind the gear beside the title
is Settings: a switch, *Morrow may change things for me* — records made,
changed and deleted, and a Quill's actions pressed, as you would, off by
default — and under it a switch per kind of data your circles give you.
Switch one off and Morrow neither lists nor reads it for you, and says so
when asked. Each person's own choices, and nowhere on the conversation
itself.

**It builds Quills of your own.** Ask for a place to track the car's
services, a reading list, a log of the hives, and Morrow writes the Quill —
the manifest, its datamodels, Python only where something cannot be
declared — checks it the way `cm quill check` does, fixes what that says,
and installs it on your shelf: a tab, on the phone, the web and the
terminal at once, over your own data, run as you. The same loop an
assistant over MCP has (`quill_schema`, `quill_check`, `quill_dev_install`),
and the same rules: a Quill of one person's own, which the administrator's
policy may allow, ask about, or refuse. An administrator's Morrow builds
them one of their own too, which they may promote for everybody under
Administration › Quills.

**One key for the whole server.** An administrator connects Claude on
Morrow's sheet under Administration › Quills — an API key from
console.anthropic.com, tried once with a short question — and picks the
model: Claude Opus 5.5 (the default, the most capable), Claude Sonnet 5.5
(quicker and cheaper) or Claude Haiku 5.5 (the quickest and cheapest). The
key is `secret = "quill"`: read by Morrow's own code alone, never listed,
never drawn, never given through the record API, an assistant included.
Everybody asks Claude with it, as themselves; the bill is the key's.

## How it talks to Claude

Through `ctx.fetch` to `api.anthropic.com` and nothing else, with the
Messages API as the standard library speaks it: there is no SDK in the
sandbox. Thinking is the model's own and adaptive; the answer is asked
for at medium effort, which keeps a turn short. Between turns only what
was said is replayed, never the model's thinking, so a conversation's
prefix stays what it was and the server-side cache holds (the system
prompt is marked for it). A classifier's refusal falls back to the model
Anthropic recommends inside the same call (`fallbacks = "default"`), so a
false positive is an answer rather than an outage; a refusal that stands
is said in the conversation, as is Anthropic being unreachable, overloaded
or out of patience with the key.

A turn is one press of Send: Morrow may look things up twelve times before
it answers, within two and a half minutes, and says so when it ran out of
room or time. The two asking actions have `timeout = "3m"` rather than a
call's fifteen seconds. While one person's turn runs, nobody else's Morrow
runs on that server: the sandbox is one interpreter per Quill, one call at
a time. For a household that is nothing; a busy server would want a pool,
which the core does not have yet.

## What it adds to your Cloudmorrow

`cm quill check` prints this; it is kept here for the catalog page.

| | |
| --- | --- |
| Datamodels | introduces `morrow.settings` (the server's key and model, one record), `morrow.conversation`, `morrow.message` and `morrow.preference` (each person's own: whether it may change things, and what it keeps out of) |
| Screens | Morrow AI (the conversations as a list, the open one as a chat), Settings (behind the gear: what Morrow may do and may read), Claude (an administrator's, on Morrow's sheet under Administration › Quills) |
| Actions | New conversation (the pen); Ask Morrow; Send; Rename; Delete; What Morrow may do and What Morrow may read (the switches on Settings); and an administrator's Connect Claude, Use this model, Try it, Disconnect |
| Code | `quill.py`, in the sandbox, as whoever uses it; reaches `api.anthropic.com`; has an assistant's tools, as you |

## Working on it

```
uv sync                  # .venv with Cloudmorrow and pytest
cm quill check           # the manifest, as a server would install it
cm quill test            # tests/, against the real record store and gate; Claude is a stub
cm quill test --sandbox  # the same, with quill.py in the sandbox
cm quill dev --local     # a throwaway server here, reinstalled as you save
```
