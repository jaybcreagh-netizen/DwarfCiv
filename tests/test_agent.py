"""Workstreams B + C: charter loading (incl. the neutral control) and the
neutral in-situ probe cadence, plus schema validation of the rationale
contract."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import charter as charter_mod
from agent import probes
from agent.schemas import (validate_call, InvalidActionCall, SCHEMAS_BY_NAME,
                           TOOL_SCHEMAS)
from agent.governor import ScriptedGovernor, ActionCall, ActionPlan, Governor
from agent.client import (EmptyCompletionError, LLMClient, LLMResponse,
                          default_model)
from agent.llm_governor import LLMGovernor
from agent.strategy import control_strategy


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
        for tool in ("set_rationing", "conscript"):
            self.assertIn("rationale",
                          SCHEMAS_BY_NAME[tool]["input_schema"]["required"])

    def test_non_causal_policy_placeholders_are_not_exposed(self):
        for tool in ("quarantine", "lockdown", "memorialise",
                     "set_rescue_priority", "set_medical_priority",
                     "assign_hazard_labour"):
            self.assertNotIn(tool, SCHEMAS_BY_NAME)

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
        validate_call("brew_drinks", {"qty": 5})
        validate_call("prepare_fish", {"qty": 5})
        validate_call("cancel_workorder", {"order_id": 12})
        validate_call("build_farm_plot", {
            "environment": "subterranean", "width": 3, "height": 3})
        validate_call("prepare_farm_room", {"width": 5, "height": 5})
        validate_call("build_workshop", {"workshop": "Carpenters"})
        validate_call("build_stockpile", {
            "kind": "seeds", "width": 3, "height": 3,
            "near_building_id": 8})
        validate_call("prioritize_workshop_construction", {"workshop_id": 8})
        validate_call("prioritize_farm_construction", {"farm_id": 4})
        validate_call("set_farm_crop", {
            "farm_id": 4, "crop_id": "MUSHROOM_HELMET_PLUMP",
            "seasons": ["spring", "summer"]})
        validate_call("protect_seeds", {
            "crop_id": "MUSHROOM_HELMET_PLUMP", "minimum": 10})
        validate_call("make_barrels", {"qty": 5})
        validate_call("make_trade_goods", {"qty": 2})
        validate_call("build_trade_depot", {})
        validate_call("prioritize_trade_depot_construction", {"depot_id": 9})
        validate_call("mark_goods_for_trade", {
            "depot_id": 9, "item_ids": [20, 21]})
        validate_call("assign_manager", {"dwarf_id": 210})
        validate_call("assign_broker", {"dwarf_id": 210})
        validate_call("request_trader", {"depot_id": 9,
                                          "mode": "broker"})
        validate_call("request_trader", {"depot_id": 9,
                                          "mode": "anyone"})
        validate_call("prioritize_trader_job", {"depot_id": 9})
        validate_call("execute_trade", {
            "depot_id": 9,
            "export_item_ids": [20, 21],
            "import_item_ids": [40],
        })

    def test_operational_tools_distinguish_fishing_from_cleaning(self):
        labors = SCHEMAS_BY_NAME["assign_labor"]["input_schema"][
            "properties"]["labor"]["enum"]
        self.assertIn("FISH", labors)
        self.assertIn("CLEAN_FISH", labors)
        self.assertIn("PLANT", labors)
        self.assertIn("MINE", labors)
        self.assertIn("CARPENTER", labors)

    def test_schema_rejects_bad_types_enums_empty_targets_and_extra_args(self):
        bad = [
            ("brew_drinks", {"qty": "five"}),
            ("brew_drinks", {"qty": 31}),
            ("gather_plants", {"qty": 0}),
            ("set_rationing", {"level": "zero", "rationale": "r"}),
            ("conscript", {"units": [], "squad": 1, "rationale": "r"}),
            ("pass_turn", {"surprise": True}),
        ]
        for tool, params in bad:
            with self.assertRaises(InvalidActionCall):
                validate_call(tool, params)

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

    def test_pass_turn_must_be_the_only_action(self):
        with self.assertRaises(InvalidActionCall):
            Governor.validate(ActionPlan(actions=[
                ActionCall("gather_plants", {"qty": 5}),
                ActionCall("pass_turn"),
            ]))

    def test_llm_adapter_drops_redundant_pass_turn_transparently(self):
        plan = LLMGovernor._plan_from_data({"actions": [
            {"tool": "gather_plants", "params": {"qty": 5}},
            {"tool": "pass_turn", "params": {}},
        ], "strategy": control_strategy()})
        self.assertEqual([a.tool for a in plan.actions], ["gather_plants"])
        self.assertEqual(plan.normalizations[0]["type"],
                         "drop_redundant_pass_turn")

    def test_llm_adapter_deduplicates_pass_turn_transparently(self):
        plan = LLMGovernor._plan_from_data({"actions": [
            {"tool": "pass_turn", "params": {}},
            {"tool": "pass_turn", "params": {}},
        ], "strategy": control_strategy()})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertEqual(plan.normalizations[0]["type"],
                         "deduplicate_pass_turn")

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

    def test_llm_governor_mock_exercises_structured_model_path(self):
        g = LLMGovernor(LLMClient(provider="mock", model="mock"))
        charter = charter_mod.load("neutral")
        plan = g.act(charter, "# briefing", {"month_index": 1},
                     {"month_index": 1, "account": []})
        self.assertEqual(plan.actions[0].tool, "pass_turn")
        self.assertFalse(plan.diary)
        self.assertTrue(plan.strategy["assessment"])
        diary = g.reflect(charter, "# briefing", {"month_index": 1},
                          [{"tool": "pass_turn", "ok": True}],
                          {"month_index": 1},
                          {"month_index": 1, "account": []})
        self.assertTrue(diary)
        answers = g.answer_probes(charter, ["One?", "Two?"],
                                  {"account": []})
        self.assertEqual(len(answers), 2)
        self.assertEqual(g.usage_summary()["total"]["calls"], 3)


class KimiClientTests(unittest.TestCase):
    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                model="kimi-k2.6",
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"actions": [], "diary": "test"}'))],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
            )

    class FakeOpenAI:
        instance = None

        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.chat = SimpleNamespace(
                completions=KimiClientTests.FakeCompletions())
            KimiClientTests.FakeOpenAI.instance = self

    def test_provider_default_models(self):
        self.assertEqual(default_model("kimi"), "kimi-k2.6")
        self.assertEqual(default_model("mock"), "mock")

    def test_kimi_requires_key_before_sdk_import(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MOONSHOT_API_KEY"):
                LLMClient(provider="kimi")

    def test_kimi_uses_moonshot_endpoint_schema_and_nonthinking_low_effort(self):
        fake_module = SimpleNamespace(OpenAI=self.FakeOpenAI)
        with patch.dict(os.environ, {"MOONSHOT_API_KEY": "test-key"}, clear=True):
            with patch.dict("sys.modules", {"openai": fake_module}):
                client = LLMClient(provider="kimi", effort="low")
                response = client.complete(
                    "system", "user", stage="govern",
                    schema={"type": "object", "properties": {}})

        sdk = self.FakeOpenAI.instance
        self.assertEqual(sdk.init_kwargs["api_key"], "test-key")
        self.assertEqual(sdk.init_kwargs["base_url"],
                         "https://api.moonshot.ai/v1")
        self.assertEqual(sdk.init_kwargs["max_retries"], 0)
        call = sdk.chat.completions.kwargs
        self.assertEqual(call["model"], "kimi-k2.6")
        self.assertEqual(call["extra_body"]["thinking"]["type"], "disabled")
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertEqual(response.input_tokens, 12)
        self.assertEqual(response.output_tokens, 8)
        self.assertGreater(response.cost_usd, 0)

    def test_thinking_effort_gets_a_budget_reasoning_cannot_exhaust(self):
        fake_module = SimpleNamespace(OpenAI=self.FakeOpenAI)
        with patch.dict(os.environ, {"MOONSHOT_API_KEY": "test-key"},
                        clear=True):
            with patch.dict("sys.modules", {"openai": fake_module}):
                client = LLMClient(provider="kimi", effort="high")
                client.complete("system", "user", stage="govern",
                                schema={"type": "object", "properties": {}})

        call = self.FakeOpenAI.instance.chat.completions.kwargs
        # Reasoning tokens are billed against this same cap. A governed month
        # at high effort once spent all 4096 on thinking and returned an
        # empty body, so the thinking budget must exceed the answer budget.
        self.assertEqual(call["extra_body"]["thinking"]["type"], "enabled")
        self.assertGreater(call["max_completion_tokens"], 4096)

    def test_empty_completion_names_the_budget_not_a_parse_error(self):
        response = LLMResponse(text="", model="kimi-k2.6", output_tokens=4096,
                               finish_reason="length", stage="govern")
        with self.assertRaises(EmptyCompletionError) as caught:
            response.json()
        message = str(caught.exception)
        self.assertIn("no content", message)
        self.assertIn("length", message)
        self.assertIn("4096", message)


if __name__ == "__main__":
    unittest.main()
