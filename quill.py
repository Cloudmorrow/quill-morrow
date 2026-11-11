"""Morrow: an assistant of your own, on your own cloud.

An administrator connects Claude once, on Morrow's sheet under Administration
› Quills; after that everybody on the server has Morrow. A conversation is a
press: what you said is written down, Morrow looks at what it needs through
the tools an assistant of yours has — the record tools over every datamodel
your circles give you, every Quill's actions you may press, the Quill-building
tools — each run as you, through the gate, and its answer is written down
beside yours, with what it looked at. Nothing here reaches anybody else's
data, and the key is read by this code alone.

Claude is asked over `ctx.fetch`, the one way out of the sandbox, with the
Messages API as the standard library speaks it: no SDK runs in here. The
model's thinking is adaptive and its own; between turns only what was said
is replayed, never its thinking, so a conversation's prefix stays what it
was and the server-side cache holds. A classifier's refusal falls back to
the model Anthropic recommends (`fallbacks = "default"`), so a false
positive is an answer, not an outage.
"""

import json
import time

from cloudmorrow.quill import Invalid, NotFound, Refused, action, error, go, redraw, toast, ui, view

SETTINGS = "morrow.settings"
CONVERSATION = "morrow.conversation"
MESSAGE = "morrow.message"
PREFERENCE = "morrow.preference"

API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
# Server-side fallbacks: a classifier's refusal is retried on the model
# Anthropic recommends, inside the same call.
BETAS = "server-side-fallback-2026-07-01"
MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8192
# Chat does well at medium; it is the lever on how long an answer takes.
EFFORT = "medium"
# The actions have three minutes; this much of it is spent asking, the rest
# is for writing the answer down and drawing the screen.
TURN_BUDGET = 150.0
REQUEST_TIMEOUT = 120.0
# How many times in one turn Morrow may look something up before it answers.
ROUNDS = 12
# How much of a conversation is replayed to the model.
HISTORY = 40
# A tool's answer longer than this is cut, and the model told so.
RESULT_MAX = 60_000
TITLE_MAX = 60
SETTINGS_ARE = "Administration › Quills › Morrow"

# What Morrow has when it may only read, and the Quill-building tools, which
# it always has: building a Quill of your own is what you asked for.
READ_TOOLS = frozenset({"list_datamodels", "list_records", "get_record", "get_record_history"})
BUILD_TOOLS = frozenset({"quill_schema", "quill_check", "quill_dev_install", "quill_mine"})
# These two make the Quill the person's own, even an administrator's.
MINE_TOOLS = frozenset({"quill_check", "quill_dev_install"})


class MorrowError(Exception):
    """Claude could not be asked, in words for the person."""


# -- small things ----------------------------------------------------------------------
def the_settings(ctx):
    """The one settings record, or None: the key is not in a listing."""
    found = ctx.records.list(SETTINGS)
    return found[0] if found else None


def key_of(ctx, settings):
    """The sealed key, read from the one record: how a secret = "quill" field is asked for."""
    key = str(ctx.records.get(SETTINGS, settings.id).get("key") or "")
    if not key:
        raise MorrowError(f"the key is gone; connect Claude again under {SETTINGS_ARE}")
    return key


def model_of(settings):
    model = str((settings.get("model") if settings is not None else "") or "")
    return model if model in MODELS else DEFAULT_MODEL


def preference(ctx):
    found = ctx.records.list(PREFERENCE)
    return found[0] if found else None


def may_change(ctx):
    found = preference(ctx)
    return bool(found and found.get("changes"))


def hidden_of(ctx):
    """The datamodels this person keeps from Morrow, by id."""
    found = preference(ctx)
    kept = found.get("hidden") if found is not None else None
    return {str(x) for x in kept} if isinstance(kept, list) else set()


def readable(ctx):
    """Every datamodel the person may read, as list_datamodels says it: [{id, label, …}]."""
    answer = ctx.assistant.call("list_datamodels", {})
    rows = (answer.get("data") or {}).get("datamodels") if answer.get("ok") else None
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]


def truthy(value):
    return value is True or str(value).strip().lower() in ("true", "1", "yes", "on")


def when(value):
    """A moment as the screen says it: the day and the minute, in UTC."""
    text = str(value or "")
    return text[:16].replace("T", " ") + (" UTC" if text else "")


def stamp(moment):
    return moment.isoformat(timespec="seconds")


def title_from(text):
    line = next((line.strip() for line in str(text).splitlines() if line.strip()), "Conversation")
    return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1].rstrip() + "…"


def cut(text, limit):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[cut: {len(text) - limit} more characters; ask for fewer at once, with where=, q= or last=]"


def brief(name, arguments):
    """One line on a tool call, for *looked*: `list_records task where lane=doing`."""
    parts = [name]
    for key in ("model", "id", "q", "last", "record"):
        if key in arguments and arguments[key] not in (None, ""):
            parts.append(f"{key}={arguments[key]}" if key != "model" else str(arguments[key]))
    where = arguments.get("where")
    if isinstance(where, dict) and where:
        parts.append("where " + " ".join(f"{k}={v}" for k, v in list(where.items())[:4]))
    if name in ("quill_check", "quill_dev_install"):
        for line in str(arguments.get("manifest", "")).splitlines():
            if line.strip().startswith("id"):
                parts.append(line.strip())
                break
    said = " ".join(parts)
    return said if len(said) <= 120 else said[:119] + "…"


# -- Claude ----------------------------------------------------------------------------
def claude(ctx, key, body, timeout):
    """One request to the Messages API: the parsed answer, or MorrowError in words."""
    try:
        answer = ctx.fetch(
            API,
            method="POST",
            headers={"x-api-key": key, "anthropic-version": API_VERSION, "anthropic-beta": BETAS},
            json=body,
            timeout=timeout,
        )
    except (Invalid, Refused) as exc:
        raise MorrowError(f"Claude could not be reached: {exc}") from exc
    try:
        data = answer.json()
    except ValueError:
        data = {}
    if answer.ok and isinstance(data, dict) and data.get("type") == "message":
        return data
    detail = (data.get("error") or {}) if isinstance(data, dict) else {}
    message = str(detail.get("message") or answer.text[:200] or "no answer")
    kind = str(detail.get("type") or "")
    if answer.status == 401 or kind == "authentication_error":
        raise MorrowError(f"Anthropic did not accept the key: {message}. Connect Claude again under {SETTINGS_ARE}.")
    if answer.status == 403 or kind == "permission_error":
        raise MorrowError(f"the key is not allowed this: {message}")
    if answer.status == 429:
        raise MorrowError("Anthropic is rate-limiting this key; try again in a moment")
    if answer.status == 529 or kind == "overloaded_error":
        raise MorrowError("Anthropic is overloaded; try again in a moment")
    if answer.status == 400 and "model" in message.lower():
        raise MorrowError(f"Anthropic does not know the model: {message}. Pick another under {SETTINGS_ARE}.")
    raise MorrowError(f"Anthropic answered {answer.status}: {message}")


def say_hi(ctx, key, model):
    """The smallest question there is, to see that the key and the model work."""
    data = claude(
        ctx,
        key,
        {
            "model": model,
            "max_tokens": 32,
            "output_config": {"effort": "low"},
            "fallbacks": "default",
            "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
        },
        30.0,
    )
    return text_of(data.get("content") or []) or "(nothing)"


def text_of(content):
    return "\n\n".join(str(b.get("text", "")).strip() for b in content if b.get("type") == "text" and b.get("text"))


def echoed(content):
    """The assistant's content as it is sent back: unchanged, unless a fallback
    happened mid-answer — then what the declining model thought and asked for
    before the switch is left out, and the fallback block stays where it was."""
    last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return content
    return [
        b
        for i, b in enumerate(content)
        if not (i < last and b.get("type") in ("thinking", "redacted_thinking", "tool_use"))
    ]


def keep_out(answer, hidden):
    """list_datamodels without what the person keeps from Morrow."""
    data = answer.get("data") if isinstance(answer.get("data"), dict) else None
    if not hidden or not data or not isinstance(data.get("datamodels"), list):
        return answer
    data = {**data, "datamodels": [r for r in data["datamodels"] if r.get("id") not in hidden]}
    return {**answer, "data": data, "text": json.dumps(data)}


def tools_for(ctx, changes):
    """An assistant's tools, as the person — all of them when Morrow may change
    things, the reading and building ones otherwise, in one order every time."""
    tools = []
    for tool in sorted(ctx.assistant.tools(), key=lambda t: t["name"]):
        if changes or tool["name"] in READ_TOOLS or tool["name"] in BUILD_TOOLS:
            tools.append({"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]})
    return tools


def system_prompt(ctx, changes, hidden=()):
    name = ctx.user.name or ctx.user.username
    cloud = ctx.cloud or "this cloud"
    who = f"{name} (username {ctx.user.username}{', an administrator' if ctx.user.admin else ''})"
    doing = (
        "You may change things too: create_record, update_record, move_record and delete_record, and "
        "every Quill's actions, each as them — do what they ask, say what you did, and read a record "
        "before you change it. Delete only what they named."
        if changes
        else "You may only read. If they ask you to make, change or delete something, say that Morrow "
        "may change things for them once they allow it behind the gear beside the title, and say what "
        "you would do."
    )
    return (
        f"You are Morrow, the assistant on {cloud}, a Cloudmorrow server, talking with {who}. "
        "Cloudmorrow keeps records of datamodels — tasks, notes, contacts, events, files, and what the "
        "installed Quills add; a Quill is a software package on the server, a tab on a shelf. "
        f"Your tools reach exactly what {name} may reach, as them, through the server's gate: nothing "
        "more, and never a secret.\n\n"
        "How to work:\n"
        "- Look before you answer. list_datamodels says what kinds of data there are and their fields; "
        "list_records reads them (where= on indexed fields, q= for text, last= for the newest), "
        "get_record reads one. Say what you found plainly, and do not guess at data you did not read.\n"
        f"- {doing}\n"
        + (
            f"- {name} keeps these kinds of data from you: {', '.join(sorted(hidden))}. Do not read them, "
            "and if asked about them say they are switched off for you in Morrow's settings.\n"
            if hidden
            else ""
        )
        + f"- Building a Quill: when {name} wants a place to keep or track something, or an app of their "
        "own, build them a Quill. Call quill_schema first and read it. Write quill.toml (and datamodels/ "
        "TOML, and quill.py only when something cannot be declared), check it with quill_check, fix what "
        "it says, then quill_dev_install. It is a Quill of their own: on their shelf alone, over their own "
        "data; it introduces datamodels with scopes = [\"personal\"], adds no fields to foundational "
        "datamodels and runs no services. Declare first — a kit screen over a datamodel needs no code. "
        "Say what you installed, that it is a tab on their shelf now, and offer to change it.\n"
        f"- Answer in the language {name} writes in. Be brief and plain. Use Markdown — a list, a bold "
        "word — sparingly, and no headings in a short answer. Each message from them starts with the "
        "time it was written, in UTC; that is the clock."
    )


def messages_of(ctx, conversation):
    lines = ctx.records.list(MESSAGE, conversation=conversation.id)
    return sorted(lines, key=lambda m: (int(m.get("n") or 0), str(m.get("at") or ""), m.id))


def replayed(lines):
    """What was said, as the model wants it: user and assistant turns, taking turns."""
    out = []
    for line in lines[-HISTORY:]:
        role = "user" if line["role"] == "you" else "assistant"
        body = str(line["body"])
        if role == "user":
            body = f"[{when(line.get('at'))}] {body}"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + body
        else:
            out.append({"role": role, "content": body})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def results_of(ctx, tool_uses, looked, changes, hidden=()):
    """Run what the model asked for, as the person, and answer each."""
    results = []
    for block in tool_uses:
        name = str(block.get("name", ""))
        arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
        if name in MINE_TOOLS:
            arguments = {**arguments, "mine": True}
        looked.append(brief(name, arguments))
        if not changes and name not in READ_TOOLS and name not in BUILD_TOOLS:
            answer = {"ok": False, "text": "Morrow may only read for this person; they allow changes behind the gear."}
        elif str(arguments.get("model") or "") in hidden:
            answer = {"ok": False, "text": f"{arguments.get('model')} is switched off for Morrow in this person's settings."}
        else:
            try:
                answer = keep_out(ctx.assistant.call(name, arguments), hidden)
            except NotFound as exc:
                answer = {"ok": False, "text": str(exc)}
            except Refused as exc:
                answer = {"ok": False, "text": f"not allowed: {exc}"}
        result = {"type": "tool_result", "tool_use_id": block.get("id", ""), "content": cut(answer.get("text", ""), RESULT_MAX)}
        if not answer.get("ok", False):
            result["is_error"] = True
        results.append(result)
    return results


def turn(ctx, conversation, text):
    """One turn: write it down, ask Claude, let it look, write the answer down."""
    settings = the_settings(ctx)
    if settings is None:
        raise MorrowError(
            f"Morrow is not connected yet: connect Claude under {SETTINGS_ARE}"
            if ctx.user.admin
            else "Morrow is not connected yet; an administrator connects Claude under Administration › Quills"
        )
    key = key_of(ctx, settings)
    model = model_of(settings)
    changes = may_change(ctx)
    hidden = hidden_of(ctx)
    now = ctx.now()
    earlier = messages_of(ctx, conversation)
    asked = ctx.records.create(
        MESSAGE, conversation=conversation.id, role="you", body=text, at=stamp(now), n=len(earlier) + 1
    )
    messages = replayed([*earlier, asked])
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": EFFORT},
        "fallbacks": "default",
        "system": [{"type": "text", "text": system_prompt(ctx, changes, hidden), "cache_control": {"type": "ephemeral"}}],
        "tools": tools_for(ctx, changes),
        "messages": messages,
    }
    started = time.monotonic()
    looked, said, status = [], [], "said"
    try:
        for _round in range(ROUNDS):
            left = TURN_BUDGET - (time.monotonic() - started)
            if left < 10:
                status = "cut"
                break
            data = claude(ctx, key, body, min(REQUEST_TIMEOUT, left))
            content = data.get("content") or []
            if text_of(content):
                said.append(text_of(content))
            stop = str(data.get("stop_reason") or "")
            if stop == "refusal":
                details = data.get("stop_details") or {}
                said.append(f"*Morrow declined this one{(': ' + str(details.get('explanation'))) if details.get('explanation') else ''}.*")
                break
            kept = echoed(content)
            tool_uses = [b for b in kept if b.get("type") == "tool_use"]
            if tool_uses and stop in ("tool_use", "pause_turn"):
                body["messages"] = [*body["messages"], {"role": "assistant", "content": kept}]
                body["messages"] = [
                    *body["messages"],
                    {"role": "user", "content": results_of(ctx, tool_uses, looked, changes, hidden)},
                ]
                continue
            if stop == "pause_turn":
                body["messages"] = [*body["messages"], {"role": "assistant", "content": kept}]
                continue
            if stop == "max_tokens":
                status = "cut"
                said.append("*Morrow ran out of room; say “go on”.*")
            break
        else:
            status = "cut"
    except MorrowError as exc:
        said.append(f"*{exc}*")
        status = "failed"
    if status == "cut" and not any("ran out of room" in s for s in said):
        said.append("*Morrow ran out of time looking; ask again, or for less at once.*")
    reply = ctx.records.create(
        MESSAGE,
        conversation=conversation.id,
        role="morrow",
        body="\n\n".join(s for s in said if s) or "*Morrow had nothing to say.*",
        looked=looked,
        status=status,
        at=stamp(ctx.now()),
        n=len(earlier) + 2,
    )
    ctx.records.patch(CONVERSATION, conversation.id, last_at=stamp(ctx.now()))
    return reply


# -- the conversation -----------------------------------------------------------------
@view("chat")
def chat(ctx):
    """Your conversations down the left, the open one on the right; on a phone
    the list, then the conversation once one is chosen."""
    opened = str(ctx.params.get("conversation") or "")
    fresh = bool(ctx.params.get("new"))
    settings = the_settings(ctx)
    conversations = sorted(
        ctx.records.list(CONVERSATION), key=lambda c: str(c.get("last_at") or c.get("started_at") or ""), reverse=True
    )
    side = ui.rows(
        conversations,
        title="title",
        subtitle="last_at",
        go="chat",
        param="conversation",
        current=opened,
        empty="Nothing asked yet. The pen starts a conversation.",
        search="Search conversations",
        group="last_at",
    )
    if opened:
        main = one_conversation(ctx, opened, settings)
    else:
        main = welcome(ctx, settings)
    # The list folds away on a computer, as Docs' does; the pen stays in the heading.
    return ui.split(
        side,
        main,
        show="main" if (opened or fresh or not conversations) else "side",
        back={} if opened or fresh else None,
        fold=True,
    )


def connected_words(ctx, settings):
    if settings is not None:
        return ""
    if ctx.user.admin:
        return f"Morrow is not connected yet: connect Claude under {SETTINGS_ARE}, and everybody on the server has it."
    return "Morrow is not connected yet; an administrator connects Claude under Administration › Quills."


def doing_words(ctx):
    """The line under the box: what Morrow may do for this person, in a few words."""
    return (
        "Morrow may change things for you, as you. Morrow can be wrong: it says what it looked at."
        if may_change(ctx)
        else "Morrow reads only; Settings lets it change things for you. Morrow can be wrong: it says what it looked at."
    )


def line_of(message):
    """A message as a line of the conversation: its words, whose, when, and what came with it."""
    status = str(message.get("status") or "said")
    notes = []
    if status == "failed":
        notes.append("Claude could not be asked")
    elif status == "cut":
        notes.append("Cut short")
    looked = message.get("looked") or []
    if looked:
        notes.append("Looked at: " + "; ".join(str(x) for x in looked))
    return {
        "id": message.id,
        "model": message.model,
        "fields": {
            "body": str(message.get("body") or ""),
            "mine": message.get("role") == "you",
            "at": message.get("at"),
            "note": " · ".join(notes),
            "failed": status == "failed",
        },
    }


def welcome(ctx, settings):
    """A new conversation: a greeting in the middle, the box on the floor."""
    name = ctx.user.name or ctx.user.username
    missing = connected_words(ctx, settings)
    if missing:
        return ui.conversation([], body="body", mine="mine", empty=missing, backdrop="mascot")
    return ui.conversation(
        [],
        body="body",
        mine="mine",
        ask="start",
        backdrop="mascot",
        empty=(
            f"Hello {name}. Ask Morrow about anything you can see on {ctx.cloud or 'this cloud'} — it reads "
            "what you can read, as you — or for a place to keep or track something, and it builds you a "
            "Quill of your own."
        ),
        placeholder="Ask Morrow",
        hint=doing_words(ctx),
    )


def one_conversation(ctx, conversation_id, settings):
    try:
        conversation = ctx.records.get(CONVERSATION, conversation_id)
    except (NotFound, Refused, Invalid):
        return ui.stack(ui.empty("That conversation is gone."), ui.button("Back", go="chat"))
    missing = connected_words(ctx, settings)
    lines = [line_of(m) for m in messages_of(ctx, conversation)]
    # Rename and Delete are the conversation's own actions: on its row's menu
    # and its sheet; Delete is the X in the corner too, asked first.
    return ui.stack(
        ui.text(str(conversation["title"]), style="subtitle"),
        ui.conversation(
            lines,
            body="body",
            mine="mine",
            note="note",
            backdrop="mascot",
            failed="failed",
            name="Morrow",
            ask=None if missing else "ask",
            record=conversation,
            close="forget",
            empty=missing or None,
            placeholder="Message Morrow",
            hint=None if missing else doing_words(ctx),
        ),
    )


@action("new")
def new(ctx):
    """The pen: the screen cleared for a new conversation; the open one stays in the list."""
    return go("chat", new="1")


@action("start")
def start(ctx, message):
    text = str(message).strip()
    if not text:
        return error("Say something first")
    if the_settings(ctx) is None:
        return error(connected_words(ctx, None))
    now = stamp(ctx.now())
    conversation = ctx.records.create(CONVERSATION, title=title_from(text), started_at=now, last_at=now)
    try:
        turn(ctx, conversation, text)
    except MorrowError as exc:
        return error(str(exc))
    return go("chat", conversation=conversation.id)


@action("ask")
def ask(ctx, conversation, message):
    text = str(message).strip()
    if not text:
        return error("Say something first")
    try:
        turn(ctx, conversation, text)
    except MorrowError as exc:
        return error(str(exc))
    return redraw()


@action("rename")
def rename(ctx, conversation, title):
    ctx.records.patch(CONVERSATION, conversation.id, title=title_from(title))
    return redraw()


@action("forget")
def forget(ctx, conversation):
    ctx.records.delete(CONVERSATION, conversation.id)
    return go("chat")


# -- settings: what Morrow may do for you ------------------------------------------------
@view("mine")
def mine(ctx):
    """Behind the gear: whether Morrow may change things, and which kinds of
    data it may read — a switch per datamodel your circles give you."""
    hidden = hidden_of(ctx)
    kinds = [r for r in readable(ctx) if not str(r["id"]).startswith("morrow.")]
    return ui.stack(
        ui.text("What Morrow may do", style="subtitle"),
        ui.text("Morrow reads what you can read, as you, and builds Quills of your own when you ask.", style="muted"),
        ui.toggle(
            "Morrow may change things for me",
            action="allow",
            on=may_change(ctx),
            field="changes",
            hint="Make, change and delete records, and press a Quill's actions, as you would. Off, Morrow only reads.",
        ),
        ui.text("What Morrow may read", style="subtitle"),
        ui.text("Switch off what Morrow should keep out of. Its own conversations it always keeps.", style="muted"),
        *(
            ui.toggle(
                str(kind.get("label") or kind["id"]),
                action="allow-data",
                on=kind["id"] not in hidden,
                field="on",
                args={"model": kind["id"]},
                hint=str(kind.get("description") or kind["id"]),
            )
            for kind in sorted(kinds, key=lambda k: str(k.get("label") or k["id"]).lower())
        ),
    )


@action("allow")
def allow(ctx, changes=False):
    wanted = truthy(changes)
    found = preference(ctx)
    if found is None:
        ctx.records.create(PREFERENCE, changes=wanted)
    else:
        ctx.records.patch(PREFERENCE, found.id, changes=wanted)
    return toast("Morrow may change things for you now" if wanted else "Morrow only reads now")


@action("allow_data")
def allow_data(ctx, model, on=False):
    model = str(model).strip()
    hidden = hidden_of(ctx)
    if truthy(on):
        hidden.discard(model)
    else:
        hidden.add(model)
    found = preference(ctx)
    kept = sorted(hidden)
    if found is None:
        ctx.records.create(PREFERENCE, changes=False, hidden=kept)
    else:
        ctx.records.patch(PREFERENCE, found.id, hidden=kept)
    return toast(f"Morrow reads {model} for you again" if truthy(on) else f"Morrow keeps out of {model} for you")


# -- the server's key, for an administrator --------------------------------------------
def administrators_only(ctx):
    return None if ctx.user.admin else error("Claude is an administrator's to connect")


KEY_WORDS = """\
At console.anthropic.com, under **API Keys**, make a key and copy it: it is shown once. Morrow tries it with one short question, then keeps it sealed — read by Morrow's code alone, never listed, never drawn, never given to an assistant. Everybody on the server asks Claude with it, as themselves, over their own data; what Claude is asked is theirs, and the bill is this key's."""


@view("settings")
def settings(ctx):
    """The server's key and model: drawn on Morrow's sheet under Administration ›
    Quills, for an administrator alone."""
    if not ctx.user.admin:
        return ui.empty("Claude is an administrator's to connect.")
    found = the_settings(ctx)
    if found is None:
        return ui.stack(
            ui.text(
                "One key for the whole server: connect Claude here once, and everybody has Morrow — "
                "each over their own data, as themselves.",
                style="muted",
            ),
            ui.markdown(KEY_WORDS),
            ui.form("connect", submit="Connect Claude"),
        )
    model = model_of(found)
    return ui.stack(
        ui.row(
            ui.stat("Model", MODELS[model]),
            ui.stat("Connected", when(found.get("connected_at")) or "—"),
        ),
        ui.text(str(found.get("status") or ""), style="muted"),
        ui.text("Which Claude answers", style="subtitle"),
        ui.text(
            "Opus is the most capable; Sonnet is quicker and cheaper; Haiku is the quickest and cheapest. "
            "Everybody gets the same one.",
            style="muted",
        ),
        ui.form("choose-model", values={"model": model}, submit="Use this model"),
        ui.row(
            ui.button("Try it", action="test"),
            ui.button("Disconnect", action="disconnect", tone="danger"),
        ),
        ui.text("Another key", style="subtitle"),
        ui.markdown(KEY_WORDS),
        ui.form("connect", submit="Replace the key"),
    )


@action("connect")
def connect(ctx, key):
    if (refused := administrators_only(ctx)) is not None:
        return refused
    key = str(key).strip()
    found = the_settings(ctx)
    model = model_of(found)
    try:
        heard = say_hi(ctx, key, model)
    except MorrowError as exc:
        return error(str(exc))
    status = f"Connected; {MODELS[model]} said: {heard}"
    now = stamp(ctx.now())
    if found is None:
        ctx.records.create(SETTINGS, {"key": key, "model": model, "status": status, "connected_at": now}, scope="public")
    else:
        ctx.records.patch(SETTINGS, found.id, key=key, status=status, connected_at=now)
    return redraw()


@action("choose_model")
def choose_model(ctx, model):
    if (refused := administrators_only(ctx)) is not None:
        return refused
    found = the_settings(ctx)
    if found is None:
        return error("Connect Claude first")
    if model not in MODELS:
        return error("That is not a model Morrow knows")
    ctx.records.patch(SETTINGS, found.id, {"model": model})
    return redraw()


@action("test")
def test(ctx):
    if (refused := administrators_only(ctx)) is not None:
        return refused
    found = the_settings(ctx)
    if found is None:
        return error("Connect Claude first")
    model = model_of(found)
    try:
        heard = say_hi(ctx, key_of(ctx, found), model)
    except MorrowError as exc:
        ctx.records.patch(SETTINGS, found.id, status=f"The last try failed: {exc}")
        return error(str(exc))
    ctx.records.patch(SETTINGS, found.id, status=f"{MODELS[model]} said: {heard}")
    return redraw()


@action("disconnect")
def disconnect(ctx):
    if (refused := administrators_only(ctx)) is not None:
        return refused
    found = the_settings(ctx)
    if found is not None:
        ctx.records.delete(SETTINGS, found.id)
    return redraw()
