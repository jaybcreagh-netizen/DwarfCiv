"""Workstreams B + C: charter loading (incl. the neutral control) and the
neutral in-situ probe cadence, plus schema validation of the rationale
contract."""

import unittest

from agent import charter as charter_mod
from agent import probes
from agent.schemas import (validate_call, InvalidActionCall, SCHEMAS_BY_NAME,
                           TOOL_SCHEMAS)
from agent.governor import ScriptedGovernor, ActionCall, ActionPlan, Governor


class CharterTests(unittest.TestCase):
    def test_neutral_always_available_and_first(self):
        ids = charter_mod.available()
        self.assertIn(charter_mod.NEUTRAL, ids)
        self.assertEqual(ids[0], charter_mod.NEUTRAL)

    def test_full_set_present(self):
        ids = set(charter_mod.available())
        for cid in ("neutral", "preserve_life", "maximise_wealth",
                    "free_artistic_society", "survive_at_all_costs"):
            self.assertIn(cid, ids)

    def test_authoring_comment_stripped_from_text(self):
        c = charter_mod.load("preserve_life")
        self.assertNotIn("<!--", c.text)
        self.assertNotIn("Intended tension", c.text)
        # ...but recorded out of band for the design audit.
        self.assertTrue(c.intended_tension)

    def test_neutral_is_value_light(self):
        import re
        c = charter_mod.load("neutral")
        low = c.text.lower()
        # The control must not prescribe a value to optimise (whole words, so
        # "art" in "charter" doesn't count).
        for loaded in ("wealth", "death", "art", "endure", "freedom"):
            self.assertIsNone(re.search(rf"\b{loaded}\b", low),
                              f"neutral charter should not mention {loaded!r}")


class ProbeTests(unittest.TestCase):
    def test_cadence_is_yearly(self):
        fired = [m for m in range(0, 37) if probes.is_probe_month(m)]
        self.assertEqual(fired, [12, 24, 36])

    def test_questions_are_neutral_and_operational(self):
        joined = " ".join(probes.IN_SITU_QUESTIONS).lower()
        # The design guard: no moral/confessional content in-situ.
        for banned in ("suffer", "harm", "compromise", "value", "regret",
                       "wrong", "guilt"):
            self.assertNotIn(banned, joined)


class SchemaTests(unittest.TestCase):
    def test_moral_tools_require_rationale(self):
        for tool in ("set_rationing", "quarantine", "conscript",
                     "memorialise", "lockdown"):
            self.assertIn("rationale",
                          SCHEMAS_BY_NAME[tool]["input_schema"]["required"])

    def test_missing_rationale_rejected(self):
        with self.assertRaises(InvalidActionCall):
            validate_call("set_rationing", {"level": "half"})

    def test_empty_rationale_rejected(self):
        with self.assertRaises(InvalidActionCall):
            validate_call("conscript",
                          {"units": [1], "squad": 1, "rationale": "   "})

    def test_valid_call_accepted(self):
        validate_call("set_rationing",
                      {"level": "half", "rationale": "stretch the stores"})

    def test_non_moral_tool_needs_no_rationale(self):
        validate_call("pass_turn", {})
        validate_call("set_order", {"job": "BrewDrink", "qty": 5})

    def test_every_schema_round_trips(self):
        for s in TOOL_SCHEMAS:
            self.assertIn("name", s)
            self.assertIn("input_schema", s)


class GovernorTests(unittest.TestCase):
    def test_validate_normalizes_rationale_field_into_params(self):
        plan = ActionPlan(actions=[
            ActionCall("set_rationing", {"level": "half"},
                       rationale="stretch food")])
        Governor.validate(plan)
        self.assertEqual(plan.actions[0].params["rationale"], "stretch food")

    def test_scripted_governor_passes_by_default(self):
        g = ScriptedGovernor()
        plan = g.act(None, "", {}, {"month_index": 3})
        self.assertEqual(plan.actions[0].tool, "pass_turn")

    def test_scripted_governor_runs_script(self):
        g = ScriptedGovernor(script={
            2: [ActionCall("set_rationing", {"level": "half"},
                           rationale="r")]})
        plan = g.act(None, "", {}, {"month_index": 2})
        Governor.validate(plan)
        self.assertEqual(plan.actions[0].tool, "set_rationing")


if __name__ == "__main__":
    unittest.main()
