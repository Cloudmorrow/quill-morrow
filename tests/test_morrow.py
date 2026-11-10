"""Morrow's tests: the real record store and gate, the assistant's tools as the
person, and Claude as a stub — what `ctx.fetch` gets is put in each test, in
order, so a turn that looks something up and then answers is one test.

`cm quill test` runs them; `cm quill test --sandbox` runs the same with
quill.py in the sandbox, as a server runs it.
"""

import base64
import json

import pytest
from cloudmorrow.quill.testing import Harness

SETTINGS, CONVERSATION, MESSAGE, PREFERENCE = (
    "morrow.settings",
    "morrow.conversation",
    "morrow.message",
    "morrow.preference",
)
API = "https://api.anthropic.com/v1/messages"


class Claude:
    """A stand-in for the Messages API: answers in the order given, and keeps every request."""

    def __init__(self):
        self.answers = []
        self.requests = []

    def says(self, *content, stop="end_turn", status=200, **more):
        body = {"type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": list(content), "stop_reason": stop, **more}
        if status != 200:
            body = {"type": "error", "error": more.get("error") or {"type": "api_error", "message": "no"}}
        self.answers.append((status, body))
        return self

    def __call__(self, method, url, headers, body):
        assert url == API and method == "POST"
        self.requests.append({"headers": headers, "body": json.loads(body or b"{}")})
        status, answer = self.answers.pop(0) if self.answers else (500, {"type": "error", "error": {"type": "api_error", "message": "nothing left to say"}})
        return {"status": status, "headers": {"Content-Type": "application/json"}, "body": base64.b64encode(json.dumps(answer).encode()).decode()}


def text(words):
    return {"type": "text", "text": words}


def tool_use(name, **arguments):
    return {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": arguments}


@pytest.fixture()
def q():
    with Harness(".") as harness:
        harness.as_user("sam")
        yield harness


@pytest.fixture()
def claude(q):
    stub = Claude()
    q.state.code.fetcher = stub
    return stub


def connect(q, claude):
    claude.says(text("Hello there."))
    done = q.act("connect", key="sk-ant-test-1")
    assert done.ok, done
    return done


# -- an administrator connects Claude ------------------------------------------------------
def test_until_an_administrator_connects_claude_nobody_can_ask(q):
    assert "not connected" in q.view("chat").text()
    assert "Administration › Quills › Morrow" in q.view("chat").text()
    assert q.act("start", message="Hi").error.startswith("Morrow is not connected")
    sam = q.as_user("sam")
    assert "an administrator connects Claude" in sam.view("chat").text()
    assert sam.act("connect", key="sk-ant-x").refused


def test_connecting_tries_the_key_once_and_keeps_it_for_morrows_code_alone(q, claude):
    connect(q, claude)
    tried = claude.requests[0]
    assert tried["headers"]["x-api-key"] == "sk-ant-test-1"
    assert tried["headers"]["anthropic-version"] == "2026-10-21"
    assert tried["body"]["model"] == "claude-opus-5-5" and tried["body"]["fallbacks"] == "default"
    found = q.list(SETTINGS)
    assert len(found) == 1 and found[0]["status"] == "Connected; Claude Opus 5.5 said: Hello there."
    # Through the record API the key is null, to the administrator who typed it too.
    assert q.get(SETTINGS, found[0].id)["key"] is None
    assert "Claude Opus 5.5" in q.view("settings").text()
    # Everybody on the server has Morrow now.
    assert [c["ask"] for c in q.as_user("sam").view("chat").find("conversation")] == ["start"]


def test_a_key_anthropic_does_not_take_is_not_kept(q, claude):
    claude.says(status=401, error={"type": "authentication_error", "message": "invalid x-api-key"})
    refused = q.act("connect", key="sk-ant-bad")
    assert refused.error.startswith("Anthropic did not accept the key: invalid x-api-key")
    assert q.list(SETTINGS) == []


def test_the_model_is_chosen_once_for_everybody_and_tried(q, claude):
    connect(q, claude)
    assert q.act("choose-model", model="claude-haiku-5-5").ok
    claude.says(text("Hi!"))
    assert q.act("test").ok
    assert claude.requests[-1]["body"]["model"] == "claude-haiku-5-5"
    assert q.list(SETTINGS)[0]["status"] == "Claude Haiku 5.5 said: Hi!"
    assert q.as_user("sam").act("choose-model", model="claude-haiku-5-5").refused
    assert q.act("disconnect").ok and q.list(SETTINGS) == []


# -- a conversation ------------------------------------------------------------------------
def test_a_turn_looks_things_up_as_the_person_and_writes_the_answer_down(q, claude):
    connect(q, claude)
    sam = q.as_user("sam")
    sam.seed(CONVERSATION, title="x", started_at="2026-11-10T09:00:00+00:00", last_at="2026-11-10T09:00:00+00:00")
    before = sam.list(CONVERSATION)
    claude.says(tool_use("list_datamodels"), stop="tool_use")
    claude.says(tool_use("list_records", model=CONVERSATION), stop="tool_use")
    claude.says(text("You have **one** conversation so far."))
    done = sam.act("start", message="How many conversations do I have?")
    assert done.ok, done
    conversation = next(c for c in sam.list(CONVERSATION) if c.id not in {b.id for b in before})
    assert conversation["title"] == "How many conversations do I have?"
    assert [e["effect"] for e in done.effects] == ["go"] and done.effects[0]["params"] == {"conversation": conversation.id}
    said = sorted(sam.list(MESSAGE, conversation=conversation.id), key=lambda m: m["at"])
    assert [m["role"] for m in said] == ["you", "morrow"]
    assert said[1]["body"] == "You have **one** conversation so far."
    assert said[1]["looked"] == ["list_datamodels", "list_records morrow.conversation"]
    assert said[1]["status"] == "said"
    # The tools were run as sam, through the gate: sam's one conversation, not the administrator's.
    first, second, third = claude.requests[1:]
    assert second["body"]["messages"][-1]["content"][0]["type"] == "tool_result"
    listed = json.loads(third["body"]["messages"][-1]["content"][0]["content"])
    assert [r["owner"] for r in listed["records"]] == ["sam", "sam"]
    # The request is shaped for the cache and for a refusal to fall back.
    assert first["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["body"]["fallbacks"] == "default" and first["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert "thinking" not in first["body"] and first["body"]["output_config"] == {"effort": "medium"}
    assert first["body"]["messages"][0]["content"].startswith("[2") and first["body"]["messages"][0]["content"].endswith(" UTC] How many conversations do I have?")
    assert "Morrow, the assistant" in first["body"]["system"][0]["text"] and "sam" in first["body"]["system"][0]["text"]
    # The conversation is a chat: the question on one side, the answer on the
    # other with what was looked at, and the box under them that asks on.
    screen = sam.view("chat", conversation=conversation.id)
    (chat,) = screen.find("conversation")
    assert [line["fields"]["mine"] for line in chat["records"]] == [True, False]
    assert chat["records"][1]["fields"]["note"] == "Looked at: list_datamodels; list_records morrow.conversation"
    assert chat["ask"] == "ask" and chat["record"]["id"] == conversation.id and chat["name"] == "Morrow"
    page = screen.text()
    assert "Looked at: list_datamodels; list_records morrow.conversation" in page
    assert "one" in page and "[Message Morrow…]" in page
    # A new one starts from a greeting, with the box that starts it.
    fresh = sam.view("chat", new="1").find("conversation")[0]
    assert fresh["ask"] == "start" and fresh["records"] == [] and "Hello" in fresh["empty"]


def test_reading_only_until_the_person_allows_changes(q, claude):
    connect(q, claude)
    sam = q.as_user("sam")
    claude.says(tool_use("create_record", model=CONVERSATION, fields={"title": "x"}), stop="tool_use")
    claude.says(text("I may only read for you."))
    assert sam.act("start", message="Make a conversation called x").ok
    names = [t["name"] for t in claude.requests[1]["body"]["tools"]]
    assert "list_records" in names and "quill_dev_install" in names and "create_record" not in names
    refused = claude.requests[2]["body"]["messages"][-1]["content"][0]
    assert refused["is_error"] is True and "may only read" in refused["content"]
    assert "I may only read" in sam.view("chat", conversation=sam.list(CONVERSATION)[0].id).text()
    # Allowed, the writing tools are there and run as sam.
    assert sam.act("allow", changes=True).toast == "Morrow may change things for you now"
    assert sam.list(PREFERENCE)[0]["changes"] is True
    conversation = sam.list(CONVERSATION)[0]
    claude.says(tool_use("create_record", model=CONVERSATION, fields={"title": "Made by Morrow", "started_at": "2026-11-10T10:00:00+00:00"}), stop="tool_use")
    claude.says(text("Done."))
    assert sam.act("ask", conversation, message="Now make it").ok
    assert "create_record" in [t["name"] for t in claude.requests[3]["body"]["tools"]]
    assert "Made by Morrow" in [c["title"] for c in sam.list(CONVERSATION)]
    # The setting is a switch behind the gear, lit now.
    switch = sam.view("mine").find("toggle")[0]
    assert switch["on"] is True and switch["action"] == "allow" and switch["field"] == "changes"
    assert sam.act("allow", changes=False).toast == "Morrow only reads now"
    assert sam.view("mine").find("toggle")[0]["on"] is False


def test_a_kind_of_data_switched_off_is_kept_from_morrow(q, claude):
    """Behind the gear, a switch per datamodel the person may read: off, Morrow
    neither lists it nor reads it, and is told so."""
    connect(q, claude)
    sam = q.as_user("sam")
    switches = {t["label"]: t for t in sam.view("mine").find("toggle")}
    assert "Morrow may change things for me" in switches
    # The harness has the server's own datamodels (approval, flow, …); a server has every Quill's.
    approval = next(t for t in switches.values() if t.get("args") == {"model": "approval"})
    assert approval["on"] is True and approval["action"] == "allow-data" and approval["field"] == "on"
    assert not any((t.get("args") or {}).get("model", "").startswith("morrow.") for t in switches.values())
    assert sam.act("allow-data", model="approval", on=False).toast == "Morrow keeps out of approval for you"
    assert sam.list(PREFERENCE)[0]["hidden"] == ["approval"]
    assert next(t for t in sam.view("mine").find("toggle") if t.get("args") == {"model": "approval"})["on"] is False
    claude.says(tool_use("list_datamodels"), stop="tool_use")
    claude.says(tool_use("list_records", model="approval"), stop="tool_use")
    claude.says(text("Approvals are switched off for me."))
    assert sam.act("start", message="What approvals are open?").ok
    assert "keeps these kinds of data from you: approval" in claude.requests[1]["body"]["system"][0]["text"]
    listed = [r["id"] for r in json.loads(claude.requests[2]["body"]["messages"][-1]["content"][0]["content"])["datamodels"]]
    assert "approval" not in listed and "flow" in listed
    refused = claude.requests[3]["body"]["messages"][-1]["content"][0]
    assert refused["is_error"] is True and "switched off" in refused["content"]
    assert sam.act("allow-data", model="approval", on=True).toast == "Morrow reads approval for you again"
    assert sam.list(PREFERENCE)[0]["hidden"] == []


def test_the_pen_clears_the_screen_for_a_new_conversation(q, claude):
    connect(q, claude)
    done = q.act("new")
    assert done.ok and done.effects == [{"effect": "go", "screen": "chat", "params": {"new": "1"}}]
    assert q.manifest.screens[0]["compose"]["action"] == "new"
    # The list on the left is rows, the open one lit; the chat has the hedgehog behind it.
    claude.says(text("Hello."))
    assert q.act("start", message="Hi").ok
    conversation = q.list(CONVERSATION)[0]
    screen = q.view("chat", conversation=conversation.id)
    (listed,) = screen.find("rows")
    assert listed["current"] == conversation.id and listed["go"] == "chat" and listed["param"] == "conversation"
    assert screen.find("conversation")[0]["backdrop"] == "mascot" and not screen.find("menu")


def test_building_a_quill_makes_one_of_the_persons_own(q, claude):
    connect(q, claude)
    manifest = '[quill]\nid = "plants"\nname = "Plants"\nversion = "0.1.0"\nsummary = "x"\ncategory = "home"\n'
    claude.says(tool_use("quill_schema"), stop="tool_use")
    claude.says(tool_use("quill_check", manifest=manifest), stop="tool_use")
    claude.says(tool_use("quill_dev_install", manifest=manifest), stop="tool_use")
    claude.says(text("Plants is on your shelf now."))
    # Even the administrator's is their own: mine = true goes with every check and install.
    assert q.act("start", message="Make me a place for my plants").ok
    installed = json.loads(claude.requests[-1]["body"]["messages"][-1]["content"][0]["content"])
    assert installed["installed"] == "plants" and installed["whose"] == q.user
    mine = {m.key for m in q.state.quills.all()}
    assert any(key.startswith("~") and key.endswith(".plants") for key in mine)
    conversation = q.list(CONVERSATION)[0]
    reply = [m for m in q.list(MESSAGE, conversation=conversation.id) if m["role"] == "morrow"][0]
    assert reply["looked"] == ["quill_schema", 'quill_check id = "plants"', 'quill_dev_install id = "plants"']


def test_what_was_said_is_replayed_and_only_that(q, claude):
    connect(q, claude)
    claude.says({"type": "thinking", "thinking": "", "signature": "x"}, text("Four."))
    assert q.act("start", message="How many?").ok
    conversation = q.list(CONVERSATION)[0]
    claude.says(text("Yes, four."))
    assert q.act("ask", conversation, message="Sure?").ok
    replayed = claude.requests[-1]["body"]["messages"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user"]
    assert replayed[1]["content"] == "Four." and replayed[2]["content"].endswith(" UTC] Sure?")
    assert q.act("rename", conversation, title="Counting").ok
    assert q.list(CONVERSATION)[0]["title"] == "Counting"
    assert q.act("forget", conversation).ok
    assert q.list(CONVERSATION) == [] and q.list(MESSAGE) == []


def test_claude_being_unreachable_is_said_in_the_conversation_not_lost(q, claude):
    connect(q, claude)
    claude.says(status=529, error={"type": "overloaded_error", "message": "Overloaded"})
    assert q.act("start", message="Hi").ok
    conversation = q.list(CONVERSATION)[0]
    reply = [m for m in q.list(MESSAGE, conversation=conversation.id) if m["role"] == "morrow"][0]
    assert reply["status"] == "failed" and "overloaded" in reply["body"]
    assert "Claude could not be asked" in q.view("chat", conversation=conversation.id).text()
    # Out of room, Morrow says so and asks to be told to go on.
    claude.says(text("Once upon a"), stop="max_tokens")
    assert q.act("ask", conversation, message="Tell me a story").ok
    cut = sorted(q.list(MESSAGE, conversation=conversation.id), key=lambda m: m["at"])[-1]
    assert cut["status"] == "cut" and "go on" in cut["body"]
    # A refusal is an answer too.
    claude.says(stop="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "not that"})
    assert q.act("ask", conversation, message="Hmm").ok
    last = sorted(q.list(MESSAGE, conversation=conversation.id), key=lambda m: m["at"])[-1]
    assert "declined" in last["body"] and "not that" in last["body"]


def test_a_fallback_mid_answer_is_echoed_without_what_came_before_it(q, claude):
    connect(q, claude)
    claude.says(
        {"type": "thinking", "thinking": "", "signature": "a"},
        tool_use("list_datamodels"),
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}},
        tool_use("list_records", model=CONVERSATION),
        stop="tool_use",
    )
    claude.says(text("Nothing yet."))
    assert q.act("start", message="Anything?").ok
    echoed = claude.requests[-1]["body"]["messages"][-2]["content"]
    assert [b["type"] for b in echoed] == ["fallback", "tool_use"]
    answered = claude.requests[-1]["body"]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in answered] == ["toolu_list_records"]


def test_morrow_is_nobodys_tool_and_the_sheet_says_what_it_has(q):
    manifest = q.manifest
    asks = {a["id"]: a for a in manifest.actions}
    assert asks["start"]["assistant"] is False and asks["ask"]["assistant"] is False
    assert asks["start"]["timeout"] == 180.0 and asks["ask"]["timeout"] == 180.0
    assert manifest.assistant["tools"] is True
    assert [f["host"] for f in manifest.fetch] == ["api.anthropic.com"]
    assert {s["id"]: s.get("admin", False) for s in manifest.screens}["settings"] is True
    assert {s["id"]: s.get("settings", False) for s in manifest.screens}["mine"] is True
    # The setting's action is pressed from the settings screen alone: on no bar.
    assert asks["allow"]["settings"] is True
