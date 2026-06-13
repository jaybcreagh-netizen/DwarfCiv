"""Offline checks for the Phase-2 agent layer — no API keys, no DF needed.

Run: python -m tests.test_agent_offline   (from the repo root)

Covers the provider abstraction (neutral<->Anthropic/OpenAI translation with
fake SDK responses), the mock backend, tool dispatch including every error
path, memory assembly/ordering and ground-truth isolation, and the briefing
dwarf-id handle.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.client import (
    AnthropicClient, OpenAIClient, MockClient, Message, ToolCall, ToolResult,
    Usage,
)
from agent.tools import execute, TOOL_SPECS
from agent.memory import MemoryStore, FullHistoryMemory
from harness import briefing as briefing_mod

_passed = 0


def check(cond, label):
    global _passed
    assert cond, f"FAILED: {label}"
    _passed += 1
    print(f"  ok: {label}")


def ns(**kw):
    return types.SimpleNamespace(**kw)


# -- Anthropic translation ----------------------------------------------------

def test_anthropic_translation():
    print("anthropic translation")
    c = AnthropicClient.__new__(AnthropicClient)   # skip __init__ (no SDK client)
    c.model = "claude-opus-4-8"
    c.temperature = None

    msgs = [
        Message.user("briefing text"),
        Message(role="assistant", text="I will act",
                tool_calls=[ToolCall("t1", "set_order", {"job": "BrewDrink", "qty": 5})]),
        Message.from_tool_results([ToolResult("t1", "queued 5", is_error=False)]),
    ]
    out = c._to_messages(msgs)
    check(out[0] == {"role": "user", "content": "briefing text"}, "user text -> string content")
    check(out[1]["role"] == "assistant", "assistant role")
    blocks = out[1]["content"]
    check(any(b.get("type") == "text" for b in blocks), "assistant text block")
    tu = [b for b in blocks if b.get("type") == "tool_use"][0]
    check(tu["id"] == "t1" and tu["name"] == "set_order" and tu["input"]["qty"] == 5,
          "tool_use block carries id/name/input")
    tr = out[2]["content"][0]
    check(tr["type"] == "tool_result" and tr["tool_use_id"] == "t1"
          and tr["is_error"] is False, "tool_result references tool_use_id")

    # _raw replay: assistant message with native content is passed verbatim.
    raw = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "x"}]
    m = Message(role="assistant", text="x", _raw=raw)
    check(c._to_messages([m])[0]["content"] is raw, "assistant _raw replayed verbatim")

    # temperature omitted for opus-4-8.
    check(c._accepts_temperature() is False, "opus-4-8 rejects temperature")
    c2 = AnthropicClient.__new__(AnthropicClient)
    c2.model = "claude-sonnet-4-6"
    check(c2.model not in ("claude-opus-4-8",) and c2._accepts_temperature() is True,
          "sonnet-4-6 accepts temperature")

    # _parse on a fake response.
    fake = ns(
        content=[
            ns(type="thinking", thinking="weighing options"),
            ns(type="text", text="My plan."),
            ns(type="tool_use", id="tt", name="pass_turn", input={}),
        ],
        usage=ns(input_tokens=100, output_tokens=20,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0),
        stop_reason="tool_use",
    )
    c.total_usage = Usage(); c.calls = 0; c._price = None
    r = c._parse(fake)
    check(r.text == "My plan.", "_parse text")
    check(r.thinking == "weighing options", "_parse thinking summary")
    check(len(r.tool_calls) == 1 and r.tool_calls[0].name == "pass_turn", "_parse tool call")
    check(r.assistant_message._raw is fake.content, "_parse keeps native content for replay")
    check(c.total_usage.input_tokens == 100 and c.calls == 1, "_parse accounts usage")


# -- OpenAI translation -------------------------------------------------------

def test_openai_translation():
    print("openai translation")
    c = OpenAIClient.__new__(OpenAIClient)
    c.model = "gpt-5"
    c.temperature = None

    msgs = [
        Message.user("brief"),
        Message(role="assistant", text="plan",
                tool_calls=[ToolCall("c1", "set_order", {"job": "PrepareMeal", "qty": 3})]),
        Message.from_tool_results([ToolResult("c1", "done")]),
    ]
    out = c._to_messages("SYS", msgs)
    check(out[0] == {"role": "system", "content": "SYS"}, "system prepended")
    check(out[1] == {"role": "user", "content": "brief"}, "user text")
    asst = out[2]
    check(asst["role"] == "assistant" and asst["tool_calls"][0]["function"]["name"] == "set_order",
          "assistant tool_calls function name")
    args = json.loads(asst["tool_calls"][0]["function"]["arguments"])
    check(args["qty"] == 3, "tool args json-encoded")
    check(out[3] == {"role": "tool", "tool_call_id": "c1", "content": "done"},
          "tool result message shape")

    fake = ns(
        choices=[ns(message=ns(content="hi", tool_calls=[
            ns(id="z", function=ns(name="pass_turn", arguments="{}"))],
            model_dump=lambda exclude_none: {"role": "assistant", "content": "hi"}),
            finish_reason="tool_calls")],
        usage=ns(prompt_tokens=50, completion_tokens=10),
    )
    c.total_usage = Usage(); c.calls = 0; c._price = None
    r = c._parse(fake)
    check(r.tool_calls[0].name == "pass_turn" and r.tool_calls[0].arguments == {},
          "openai _parse tool call + json args")
    check(c.total_usage.output_tokens == 10, "openai usage accounted")


# -- Mock backend -------------------------------------------------------------

def test_mock():
    print("mock backend")
    c = MockClient()
    gov = c.generate("sys", [Message.user("Briefing... \n governance please")], TOOL_SPECS)
    names = [t.name for t in gov.tool_calls]
    check("set_order" in names and "pass_turn" in names, "mock governance issues orders + pass")
    check(names[-1] == "pass_turn", "mock ends on pass_turn")
    diary = c.generate("sys", [Message.user("write the historical record")], None)
    check(diary.tool_calls == [] and len(diary.text) > 20, "mock diary returns prose, no tools")


# -- Tool dispatch ------------------------------------------------------------

class FakeDF:
    """Stand-in DFHackClient: records run_command / lua calls."""
    def __init__(self):
        self.commands = []
    def run_command(self, *a, **k):
        self.commands.append(a)
        return f"ran {a}"
    def lua(self, code, **k):
        self.commands.append(("lua", code))
        return "lua ok"


def test_tools():
    print("tool dispatch")
    df = FakeDF()
    r = execute(df, "set_order", {"job": "BrewDrink", "qty": 10})
    check(r.ok and df.commands[-1] == ("workorder", "BrewDrink", "10"), "set_order -> workorder")

    r = execute(df, "assign_labor", {"dwarf_id": 42, "labor": "BREWER"})
    check(r.ok and df.commands[-1][0] == "lua", "assign_labor -> lua")

    r = execute(df, "pass_turn", {})
    check(r.ok and "time" in r.content.lower(), "pass_turn ok")

    r = execute(df, "build", {"workshop_type": "Still", "zone": "x"})
    check((not r.ok) and "not available" in r.content, "stubbed verb returns graceful error")

    r = execute(df, "set_order", {"job": "BrewDrink"})  # missing qty
    check((not r.ok) and "argument" in r.content.lower(), "missing arg returns graceful error")

    r = execute(df, "nonsense", {})
    check((not r.ok) and "unknown" in r.content.lower(), "unknown action returns graceful error")

    df2 = FakeDF()
    def boom(code, **k):
        from harness.dfhack_client import DFError
        raise DFError("simulated game error")
    df2.lua = boom
    r = execute(df2, "assign_labor", {"dwarf_id": 1, "labor": "MINE"})
    check((not r.ok) and "failed in-game" in r.content, "DFError surfaced as graceful error")


# -- Memory -------------------------------------------------------------------

def test_memory():
    print("memory assembly")
    store = MemoryStore()
    store.record_briefing(0, "1 Granite", "BRIEFING-0\nLOW WOOD")
    store.record_turn(1, "I will brew.", [
        {"name": "set_order", "arguments": {"job": "BrewDrink", "qty": 10},
         "ok": True, "result": "queued"}])
    store.record_briefing(1, "13 Slate", "BRIEFING-1")
    store.record_diary(3, "spring", "13 Felsite", "We endured the first season.")

    msgs = FullHistoryMemory().build_messages(store, "GOVERN NOW")
    check(msgs[0].role == "user" and "BRIEFING-0" in msgs[0].text, "first message is briefing 0")
    check(msgs[1].role == "assistant" and "brew" in msgs[1].text.lower()
          and "set_order" in msgs[1].text, "turn rendered with reasoning + orders")
    check(any("BRIEFING-1" in m.text for m in msgs), "later briefing present")
    check(any("endured" in m.text for m in msgs), "diary present in context")
    check(msgs[-1].role == "user" and msgs[-1].text == "GOVERN NOW", "final instruction last")
    # ground-truth isolation: nothing in context should look like a ledger.
    blob = "\n".join(m.text for m in msgs)
    check("ledger" not in blob.lower() and ".jsonl" not in blob, "no ledger content in context")


# -- Briefing id handle -------------------------------------------------------

def test_briefing_id():
    print("briefing dwarf-id handle")
    state = {
        "date": {"pretty": "1 Granite, year 30"},
        "population": 1, "stocks": {}, "threats": {}, "dwarves": [
            {"id": 42, "name": "Urist", "profession": "Miner",
             "stress_category": 3, "adult": True}],
        "fort": {"site_name": "Testfort"},
    }
    b = briefing_mod.build(state, [], None, 1)
    md = briefing_mod.render_markdown(b)
    check("[#42]" in md, "roster shows [#id] handle")

    # The briefing is the agent's perception; it must never name the
    # ground-truth ledger, even in its event-summary footers.
    noisy = [{"seq": i, "category": "job_cancel",
              "raw": f"X cancels job {i}.", "game_date": {"pretty": "1 Granite"}}
             for i in range(5)]
    md2 = briefing_mod.render_markdown(briefing_mod.build(state, noisy, None, 2))
    check("ledger" not in md2.lower(), "briefing never names the ledger")


def main():
    for fn in (test_anthropic_translation, test_openai_translation, test_mock,
               test_tools, test_memory, test_briefing_id):
        fn()
    print(f"\nALL OFFLINE CHECKS PASSED ({_passed} assertions)")


if __name__ == "__main__":
    main()
