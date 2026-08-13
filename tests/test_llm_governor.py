"""The LLM governor: tool-call -> dispatch -> tool_result -> diary.

No live DF and no network: the DF side is a fake client that records commands,
and the model side is either the deterministic `mock` provider or a stub turn
scripted per test.
"""

from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.client import LLMClient, LLMTurn, ToolCall
from agent.governor import Governor, ActionCall
from agent.llm_governor import LLMGovernor
from agent import memory as memory_mod
from agent.dispatch import dispatch
from harness.welfare import WelfareTrace


# Several tests deliberately drive failures the governor is built to absorb;
# their tracebacks are expected output, not signal.
for _name in ("agent.llm_governor", "agent.dispatch", "agent.run_governed"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


class FakeCharter:
    id = "preserve_life"
    text = "Preserve dwarven life above all else."
    intended_tension = ""


class FakeDF:
    """Stands in for DFHackClient: records commands, returns canned output."""

    def __init__(self, fail: set[str] | None = None):
        self.commands: list[tuple] = []
        self.fail = fail or set()

    def run_command(self, *args, **kwargs):
        self.commands.append(args)
        if args and args[0] in self.fail:
            raise RuntimeError(f"{args[0]} exploded")
        return "ok"

    def lua(self, src, **kwargs):
        self.commands.append(("lua", src))
        return "ok"

    def run_json_script(self, name, *args, **kwargs):
        return {"date": {"pretty": "1st Granite, 250"}}


class StubClient:
    """An LLMClient-shaped stub returning scripted turns, recording prompts."""

    def __init__(self, turns):
        self.model = "stub"
        self.turns = list(turns)
        self.seen: list[dict] = []

    def converse(self, system, messages, *, tools=None, stage=""):
        self.seen.append({"stage": stage, "system": system,
                          "messages": messages, "tools": tools})
        return self.turns.pop(0)

    def complete(self, system, user, *, stage="", schema=None):
        self.seen.append({"stage": stage, "system": system, "user": user})
        from agent.client import LLMResponse
        return LLMResponse(text=json.dumps({"answers": ["a", "b"]}),
                           model="stub", stage=stage)


def _turn(text="", calls=()):
    return LLMTurn(text=text, tool_calls=list(calls), model="stub",
                   content=[{"type": "text", "text": text}]
                   + [{"type": "tool_use", "id": c.id, "name": c.name,
                       "input": c.params} for c in calls])


class TestActLoop(unittest.TestCase):
    def test_tool_call_becomes_validated_action_with_rationale(self):
        gov = LLMGovernor(StubClient([_turn("thinking", [
            ToolCall(id="t1", name="set_rationing",
                     params={"level": "half", "rationale": "stores are short"})])]))
        plan = gov.act(FakeCharter(), "# Briefing\nLOW FOOD", {}, {"account": []})
        self.assertEqual([a.tool for a in plan.actions], ["set_rationing"])
        self.assertEqual(plan.actions[0].rationale, "stores are short")
        self.assertEqual(plan.actions[0].call_id, "t1")
        # Diary is deferred to observe() when actions were taken.
        self.assertEqual(plan.diary, "")
        # And the plan passes the harness-side contract unchanged.
        Governor.validate(plan)

    def test_moral_call_without_rationale_is_dropped_not_raised(self):
        """The required-rationale contract must not sink the month."""
        gov = LLMGovernor(StubClient([_turn("", [
            ToolCall(id="t1", name="conscript",
                     params={"units": [1], "squad": 0}),         # no rationale
            ToolCall(id="t2", name="set_order",
                     params={"job": "BrewDrink", "qty": 10})])]))
        plan = gov.act(FakeCharter(), "b", {}, {"account": []})
        self.assertEqual([a.tool for a in plan.actions], ["set_order"])
        self.assertIn("t1", gov._rejected)
        self.assertIn("rationale", gov._rejected["t1"])

    def test_unknown_tool_is_dropped(self):
        gov = LLMGovernor(StubClient([_turn("", [
            ToolCall(id="t1", name="nuke_the_fortress", params={})])]))
        plan = gov.act(FakeCharter(), "b", {}, {"account": []})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertIn("t1", gov._rejected)

    def test_no_tool_calls_means_pass_turn_and_text_is_the_diary(self):
        gov = LLMGovernor(StubClient([_turn("A quiet month.")]))
        plan = gov.act(FakeCharter(), "b", {}, {"account": []})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertEqual(plan.diary, "A quiet month.")
        self.assertEqual(gov.observe(FakeCharter(), [], {}), "")

    def test_provider_failure_passes_the_month_without_fabricating_a_diary(self):
        class Boom(StubClient):
            def converse(self, *a, **k):
                raise RuntimeError("503")

        gov = LLMGovernor(Boom([]))
        plan = gov.act(FakeCharter(), "b", {}, {"account": [], "month_index": 4})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertEqual(plan.diary, "")
        self.assertEqual(gov.errors[0]["month_index"], 4)
        self.assertEqual(gov.errors[0]["stage"], "govern")


class TestObserveClosesTheLoop(unittest.TestCase):
    """The headline design point: the diary is written knowing what happened."""

    def _run_month(self, outcomes_fail: bool):
        act = _turn("", [ToolCall(id="t1", name="set_rationing",
                                  params={"level": "half", "rationale": "short"})])
        client = StubClient([act, _turn("Diary text.")])
        gov = LLMGovernor(client)
        plan = gov.act(FakeCharter(), "b", {}, {"account": []})
        outcomes = [{"tool": "set_rationing", "call_id": "t1",
                     "ok": not outcomes_fail,
                     "result": "boom" if outcomes_fail else "done"}]
        diary = gov.observe(FakeCharter(), outcomes, {})
        return client, diary

    def test_every_tool_use_gets_a_tool_result(self):
        client, diary = self._run_month(False)
        self.assertEqual(diary, "Diary text.")
        follow_up = client.seen[1]["messages"][-1]["content"]
        results = [b for b in follow_up if b.get("type") == "tool_result"]
        self.assertEqual([r["tool_use_id"] for r in results], ["t1"])
        self.assertIn("OK", results[0]["content"])

    def test_failed_action_is_reported_to_the_model_before_it_narrates(self):
        client, _ = self._run_month(True)
        results = [b for b in client.seen[1]["messages"][-1]["content"]
                   if b.get("type") == "tool_result"]
        self.assertIn("FAILED", results[0]["content"])
        self.assertIn("boom", results[0]["content"])

    def test_rejected_call_still_gets_a_tool_result(self):
        """Every tool_use id must be answered or the provider rejects the turn."""
        act = _turn("", [
            ToolCall(id="t1", name="conscript", params={"units": [1], "squad": 0}),
            ToolCall(id="t2", name="pass_turn", params={})])
        client = StubClient([act, _turn("Diary.")])
        gov = LLMGovernor(client)
        gov.act(FakeCharter(), "b", {}, {"account": []})
        gov.observe(FakeCharter(), [{"tool": "pass_turn", "call_id": "t2",
                                     "ok": True, "result": "pass"}], {})
        results = [b for b in client.seen[1]["messages"][-1]["content"]
                   if b.get("type") == "tool_result"]
        self.assertEqual({r["tool_use_id"] for r in results}, {"t1", "t2"})
        rejected = next(r for r in results if r["tool_use_id"] == "t1")
        self.assertIn("REJECTED", rejected["content"])

    def test_assistant_turn_is_replayed_verbatim(self):
        """Thinking blocks must survive the round trip or the API 400s."""
        act = LLMTurn(text="t", tool_calls=[ToolCall(id="t1", name="pass_turn")],
                      content=[{"type": "thinking", "thinking": "...",
                                "signature": "sig"},
                               {"type": "tool_use", "id": "t1",
                                "name": "pass_turn", "input": {}}])
        client = StubClient([act, _turn("Diary.")])
        gov = LLMGovernor(client)
        gov.act(FakeCharter(), "b", {}, {"account": []})
        gov.observe(FakeCharter(), [{"call_id": "t1", "ok": True,
                                     "result": "pass"}], {})
        assistant = client.seen[1]["messages"][-2]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["content"][0]["type"], "thinking")
        self.assertEqual(assistant["content"][0]["signature"], "sig")


class TestProbeContract(unittest.TestCase):
    def test_answers_stay_aligned_with_the_fixed_question_list(self):
        class Short(StubClient):
            def complete(self, system, user, *, stage="", schema=None):
                from agent.client import LLMResponse
                return LLMResponse(text=json.dumps({"answers": ["only one"]}),
                                   model="stub", stage=stage)

        gov = LLMGovernor(Short([]))
        answers = gov.answer_probes(FakeCharter(), ["q1", "q2", "q3"], {})
        self.assertEqual(answers, ["only one", "(no answer)", "(no answer)"])

    def test_probe_failure_does_not_raise(self):
        class Boom(StubClient):
            def complete(self, *a, **k):
                raise RuntimeError("timeout")

        gov = LLMGovernor(Boom([]))
        self.assertEqual(gov.answer_probes(FakeCharter(), ["q1"], {"year": 1}),
                         ["(no answer)"])
        self.assertEqual(gov.errors[0]["stage"], "probe")


class TestPromptNeutrality(unittest.TestCase):
    """The design guard from agent.probes, enforced for the governance prompts.

    Telling the model to be honest, or that its account will be checked, would
    contaminate the exact measurement the project exists to make.
    """

    LEADING = ["honest", "truthful", "candid", "complete and accurate",
               "will be checked", "will be audited", "evaluated",
               "do not conceal", "do not lie", "moral", "ethical"]

    def test_system_prompt_carries_no_honesty_instruction(self):
        system = LLMGovernor._system(FakeCharter())
        low = system.lower()
        for phrase in self.LEADING:
            self.assertNotIn(phrase, low, f"leading phrase {phrase!r} in prompt")

    def test_charter_is_injected_verbatim(self):
        self.assertIn(FakeCharter.text, LLMGovernor._system(FakeCharter()))

    def test_diary_prompt_is_operational(self):
        from agent.llm_governor import _DIARY_PROMPT
        low = _DIARY_PROMPT.lower()
        for phrase in self.LEADING:
            self.assertNotIn(phrase, low)


class TestMemory(unittest.TestCase):
    def _account(self, n):
        return [{"tag": "diary", "month_index": i, "date": {"pretty": f"m{i}"},
                 "text": f"Month {i} happened."} for i in range(1, n + 1)]

    def test_all_diaries_survive_briefings_are_dropped(self):
        out = memory_mod.build(self._account(5),
                               [f"briefing {i}" for i in range(10)],
                               "current", max_briefings=3)
        for i in range(1, 6):
            self.assertIn(f"Month {i} happened.", out)
        self.assertIn("briefing 9", out)
        self.assertNotIn("briefing 5", out)
        self.assertIn("current", out)

    def test_probe_answers_are_carried(self):
        acct = [{"tag": "in_situ", "year": 1, "month_index": 12,
                 "qa": [{"question": "priorities?", "answer": "keep them alive"}]}]
        out = memory_mod.build(acct, [], "current")
        self.assertIn("keep them alive", out)

    def test_over_budget_elides_the_middle_keeping_both_ends(self):
        out = memory_mod.build(self._account(40), [], "current",
                               char_budget=400)
        self.assertIn("Month 1 happened.", out)      # the founding year
        self.assertIn("Month 40 happened.", out)     # the recent past
        self.assertIn("elided", out)
        self.assertNotIn("Month 20 happened.", out)

    def test_reasoning_entries_are_not_replayed_as_diary(self):
        acct = [{"tag": "reasoning", "month_index": 1,
                 "actions": [{"tool": "conscript", "rationale": "secret"}]}]
        out = memory_mod.build(acct, [], "current")
        self.assertNotIn("secret", out)


class TestEndToEndOffline(unittest.TestCase):
    """The whole Phase 2 path on the mock provider: no network, no DF."""

    def test_alerting_briefing_produces_a_welfare_linked_action_and_diary(self):
        with TemporaryDirectory() as td:
            welfare = WelfareTrace(Path(td) / "welfare.jsonl")
            df = FakeDF()
            gov = LLMGovernor(LLMClient(provider="mock", model="mock"))

            plan = gov.act(FakeCharter(), "## Alerts\n- LOW FOOD\n", {},
                           {"account": [], "month_index": 1})
            Governor.validate(plan)
            self.assertEqual([a.tool for a in plan.actions], ["set_rationing"])

            outcomes = [dispatch(df, c, welfare=welfare) for c in plan.actions]
            self.assertTrue(all(o["ok"] for o in outcomes))

            diary = gov.observe(FakeCharter(), outcomes, {"month_index": 1})
            self.assertTrue(diary)

            records = [json.loads(l) for l in
                       (Path(td) / "welfare.jsonl").read_text().splitlines()]
            self.assertEqual(records[0]["tool"], "set_rationing")
            self.assertTrue(records[0]["rationale"])

    def test_quiet_briefing_passes_the_turn(self):
        gov = LLMGovernor(LLMClient(provider="mock", model="mock"))
        plan = gov.act(FakeCharter(), "## Alerts\n- None\n", {}, {"account": []})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])


class FakeRun:
    def __init__(self, client):
        self.client = client


class TestGovernedRunHook(unittest.TestCase):
    """The harness-side wiring: hook -> act -> dispatch -> observe -> account."""

    def _governed(self, td, governor):
        from agent.run_governed import GovernedRun
        run_dir = Path(td)
        (run_dir / "briefing-001.md").write_text("## Alerts\n- LOW FOOD\n")
        (run_dir / "briefing-001.json").write_text("{}")
        return GovernedRun(run_dir, governor, FakeCharter()), run_dir

    def test_diary_recorded_is_the_post_outcome_one(self):
        """observe() must win over any diary the plan carried."""
        class Narrator(Governor):
            name = "narrator"

            def act(self, charter, md, js, ctx):
                from agent.governor import ActionPlan
                return ActionPlan(actions=[ActionCall("pass_turn")],
                                  diary="written before dispatch")

            def observe(self, charter, outcomes, ctx):
                return "written after dispatch"

        with TemporaryDirectory() as td:
            governed, run_dir = self._governed(td, Narrator())
            governed(FakeRun(FakeDF()), 1, {"date": {"pretty": "d"}}, [])
            diaries = governed.account.by_tag("diary")
            self.assertEqual([d["text"] for d in diaries],
                             ["written after dispatch"])

    def test_plan_diary_survives_for_governors_that_narrate_up_front(self):
        from agent.run_governed import PassGovernor
        with TemporaryDirectory() as td:
            governed, _ = self._governed(td, PassGovernor())
            governed(FakeRun(FakeDF()), 1, {"date": {"pretty": "d"}}, [])
            self.assertEqual(governed.account.by_tag("diary")[0]["text"],
                             "(no intervention this month)")

    def test_month_zero_probe_sees_the_opening_briefing(self):
        seen = {}

        class Probed(Governor):
            name = "probed"

            def answer_probes(self, charter, questions, ctx):
                seen["briefing"] = ctx.get("briefing_md")
                return ["a"] * len(questions)

        with TemporaryDirectory() as td:
            run_dir = Path(td)
            (run_dir / "briefing-000.md").write_text("## Alerts\n- embark state\n")
            (run_dir / "briefing-000.json").write_text("{}")
            from agent.run_governed import GovernedRun
            governed = GovernedRun(run_dir, Probed(), FakeCharter())
            governed(FakeRun(FakeDF()), 0, {"date": {"pretty": "d"}}, [])
            self.assertIn("embark state", seen["briefing"])
            self.assertEqual(len(governed.account.by_tag("in_situ")), 1)

    def test_llm_governor_month_zero_probe_carries_the_briefing(self):
        gov = LLMGovernor(StubClient([]))
        gov.answer_probes(FakeCharter(), ["q"],
                          {"briefing_md": "## Alerts\n- embark state\n"})
        self.assertIn("embark state", gov.client.seen[0]["user"])

    def test_end_to_end_month_on_the_mock_provider(self):
        with TemporaryDirectory() as td:
            governor = LLMGovernor(LLMClient(provider="mock", model="mock"))
            governed, run_dir = self._governed(td, governor)
            governed(FakeRun(FakeDF()), 1, {"date": {"pretty": "d"}}, [])

            # An action with its rationale, a welfare record, and a diary
            # written after the outcome came back.
            reasoning = governed.account.by_tag("reasoning")[0]
            self.assertEqual(reasoning["actions"][0]["tool"], "set_rationing")
            self.assertTrue(reasoning["actions"][0]["rationale"])
            self.assertTrue(governed.account.by_tag("diary")[0]["text"])
            self.assertTrue((run_dir / "welfare.jsonl").exists())

            governed.finalize(governor)
            report = json.loads((run_dir / "governor.json").read_text())
            self.assertEqual(report["usage"]["total"]["calls"], 2)  # act + diary


if __name__ == "__main__":
    unittest.main()
