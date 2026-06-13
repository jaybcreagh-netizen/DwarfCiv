"""Phase 2 wiring tests — exercised without Dwarf Fortress or a live API.

These cover the agent loop, memory assembly, tool dispatch, transcript writing,
and the provider message translation, using fakes in place of DFHack and the
model SDKs. They guard the seams; the real acceptance test is a live reign
(see the README).

    python -m unittest tests.test_agent      # or: python tests/test_agent.py
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import tools as tools_mod
from agent.client import AnthropicClient, OpenAIClient, ToolCall, LLMResponse, Usage
from agent.memory import RollingMemory, TurnRecord
from agent.transcript import Transcript


# --------------------------------------------------------------------------
# Fakes


class FakeDF:
    """Stands in for DFHackClient: records the calls actions.py makes."""

    def __init__(self):
        self.commands = []
        self.lua_calls = []

    def run_command(self, *args, **kwargs):
        self.commands.append(args)
        return "ok"

    def lua(self, code, **kwargs):
        self.lua_calls.append(code)
        return "labor set"


class FakeLLM:
    """Returns scripted responses; records the messages it was handed."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.seen_messages = []
        self.total_usage = Usage()
        self.calls = 0

    def generate(self, messages, tools=None, max_tokens=16000):
        self.seen_messages.append(messages)
        self.calls += 1
        text, tool_calls = self.scripted.pop(0)
        return LLMResponse(text, tool_calls,
                           {"role": "assistant", "content": text,
                            "text": text, "tool_calls": tool_calls},
                           Usage(input_tokens=10, output_tokens=5))

    def cost_summary(self):
        return "fake"


# --------------------------------------------------------------------------
# Tool dispatch


class TestToolDispatch(unittest.TestCase):
    def test_set_order(self):
        df = FakeDF()
        result, err = tools_mod.dispatch(df, "set_order",
                                         {"job": "BrewDrink", "quantity": 20})
        self.assertFalse(err)
        self.assertIn("BrewDrink", result)
        self.assertEqual(df.commands[0], ("workorder", "BrewDrink", "20"))

    def test_assign_labor(self):
        df = FakeDF()
        result, err = tools_mod.dispatch(
            df, "assign_labor", {"dwarf_id": 42, "labor": "brewer", "enabled": True})
        self.assertFalse(err)
        self.assertIn("BREWER", df.lua_calls[0])  # uppercased

    def test_pass_turn(self):
        result, err = tools_mod.dispatch(FakeDF(), "pass_turn", {})
        self.assertFalse(err)

    def test_unknown_tool_is_error_not_raise(self):
        result, err = tools_mod.dispatch(FakeDF(), "nuke_everything", {})
        self.assertTrue(err)
        self.assertIn("Unknown order", result)

    def test_missing_blueprint_is_graceful(self):
        result, err = tools_mod.dispatch(FakeDF(), "dig_blueprint",
                                         {"blueprint": "nonesuch.csv"})
        self.assertTrue(err)
        self.assertIn("No blueprint", result)

    def test_bad_args_are_errors(self):
        result, err = tools_mod.dispatch(FakeDF(), "set_order",
                                         {"job": "BrewDrink"})  # missing qty
        self.assertTrue(err)


# --------------------------------------------------------------------------
# Memory policy + ground-truth isolation


class TestMemory(unittest.TestCase):
    def test_current_briefing_is_last_and_isolation_holds(self):
        mem = RollingMemory(charter="CHARTER")
        mem.record_briefing("Granite y1", "## briefing one\nstuff")
        mem.record_briefing("Slate y1", "## briefing two\nmore stuff")
        mem.record_turn(TurnRecord(1, "Granite y1", "I will brew.",
                                   ["set_order(BrewDrink, 10) -> ok"]))
        msgs = mem.build_messages("ACT NOW")

        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "CHARTER")
        user = msgs[1]["content"]
        # current (last) briefing present, framed as this month's
        self.assertIn("This month's briefing", user)
        self.assertIn("briefing two", user)
        # past briefing + action log present
        self.assertIn("briefing one", user)
        self.assertIn("set_order(BrewDrink, 10)", user)
        self.assertIn("ACT NOW", user)

    def test_diary_messages_see_full_chronicle(self):
        mem = RollingMemory(charter="C")
        mem.record_briefing("d1", "b1")
        mem.record_diary("d1", "Today we dug deep.")
        msgs = mem.build_diary_messages("WRITE THE ENTRY")
        user = msgs[1]["content"]
        self.assertIn("b1", user)
        self.assertIn("Today we dug deep.", user)
        self.assertIn("WRITE THE ENTRY", user)


# --------------------------------------------------------------------------
# Provider message translation (no SDK / no network)


class TestTranslation(unittest.TestCase):
    def _messages(self):
        return [
            {"role": "system", "content": "be a steward"},
            {"role": "user", "content": "briefing text"},
            {"role": "assistant", "content": ["<native blocks>"],
             "text": "thinking", "tool_calls": []},
            {"role": "tool", "tool_call_id": "t1", "name": "set_order",
             "content": "done", "is_error": False},
            {"role": "tool", "tool_call_id": "t2", "name": "set_order",
             "content": "bad", "is_error": True},
        ]

    def test_anthropic_split_and_merge(self):
        c = AnthropicClient.__new__(AnthropicClient)  # skip SDK init
        system, api = c._to_api(self._messages())
        self.assertEqual(system, "be a steward")
        # consecutive tool results merged into ONE user turn with two blocks
        tool_user = [m for m in api if m["role"] == "user"
                     and isinstance(m["content"], list)]
        self.assertEqual(len(tool_user), 1)
        blocks = tool_user[0]["content"]
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[1]["is_error"])
        self.assertEqual(blocks[0]["tool_use_id"], "t1")

    def test_openai_tool_role_and_error_prefix(self):
        c = OpenAIClient.__new__(OpenAIClient)
        # OpenAI assistant messages carry a provider-native message dict.
        msgs = self._messages()
        msgs[2] = {"role": "assistant",
                   "content": {"role": "assistant", "content": None,
                               "tool_calls": []},
                   "text": "thinking", "tool_calls": []}
        api = c._to_api(msgs)
        tool_msgs = [m for m in api if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertTrue(tool_msgs[1]["content"].startswith("ERROR:"))


# --------------------------------------------------------------------------
# Transcript


class TestTranscript(unittest.TestCase):
    def test_writes_md_and_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            t = Transcript(Path(d))
            t.header({"backend": "anthropic", "model": "x", "months": 1,
                      "started": "now", "run_dir": d})
            t.turn(1, "Granite y1", "## briefing", "I deliberate.",
                   [{"name": "set_order", "arguments": {"job": "BrewDrink",
                     "quantity": 5}, "result": "ok", "is_error": False}])
            t.diary(0, "Slate y1", "A chronicle entry.")
            t.footer("done")
            t.close()

            md = (Path(d) / "transcript.md").read_text()
            self.assertIn("Month 1", md)
            self.assertIn("set_order", md)
            self.assertIn("Chronicle entry", md)

            events = [json.loads(l) for l in
                      (Path(d) / "transcript.jsonl").read_text().splitlines()]
            kinds = [e["event"] for e in events]
            self.assertEqual(kinds, ["run_start", "turn", "diary", "run_end"])


# --------------------------------------------------------------------------
# The agent turn loop (StewardRun), with DF + SDK stubbed out


class TestAgentTurn(unittest.TestCase):
    def _make_run(self, tmp, scripted, max_actions=15):
        from harness.loop import Run
        from agent.steward import StewardRun

        run_dir = Path(tmp)
        # a minimal briefing-000 for the steward to read
        (run_dir / "briefing-000.md").write_text("## Founding briefing\n7 dwarves")
        (run_dir / "briefing-000.json").write_text(
            json.dumps({"date": {"pretty": "1 Granite, year 1 (spring)"}}))

        mem = RollingMemory(charter="C")
        t = Transcript(run_dir)
        llm = FakeLLM(scripted)

        def fake_run_init(self, df_dir, rd, months, ticks, resume_from,
                          export_legends_at_end=True):
            self.run_dir = rd
            self.client = FakeDF()

        with mock.patch.object(Run, "__init__", fake_run_init):
            run = StewardRun(Path(tmp), run_dir, 12, 33600, None,
                             llm=llm, memory=mem, transcript=t,
                             backend="anthropic", model="m",
                             max_actions=max_actions)
        return run, llm, mem, t

    def test_turn_issues_orders_then_passes(self):
        scripted = [
            ("Let me brew and pass.",
             [ToolCall("a", "set_order", {"job": "BrewDrink", "quantity": 20})]),
            ("Now I pass.", [ToolCall("b", "pass_turn", {})]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run, llm, mem, t = self._make_run(tmp, scripted)
            run.before_month(1)
            t.close()

            self.assertEqual(llm.calls, 2)
            self.assertEqual(len(mem.turns), 1)
            self.assertIn("BrewDrink", mem.turns[0].actions[0])
            # the order was actually dispatched to the (fake) fort
            self.assertEqual(run.client.commands[0],
                             ("workorder", "BrewDrink", "20"))

    def test_action_cap_stops_runaway_model(self):
        # model never passes — always issues one order
        scripted = [("again", [ToolCall(str(i), "set_order",
                    {"job": "BrewDrink", "quantity": 1})]) for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp:
            run, llm, mem, t = self._make_run(tmp, scripted, max_actions=3)
            run.before_month(1)
            t.close()
            # stopped at the cap rather than looping forever
            self.assertLessEqual(llm.calls, 4)

    def test_no_tool_calls_ends_turn(self):
        scripted = [("I will simply observe.", [])]
        with tempfile.TemporaryDirectory() as tmp:
            run, llm, mem, t = self._make_run(tmp, scripted)
            run.before_month(1)
            t.close()
            self.assertEqual(llm.calls, 1)
            self.assertEqual(len(mem.turns), 1)
            self.assertEqual(mem.turns[0].actions, [])


if __name__ == "__main__":
    unittest.main()
