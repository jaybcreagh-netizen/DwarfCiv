"""Regression guards for the live-governor and validity fixes."""

import json
import tempfile
import unittest
from pathlib import Path

from agent.account import AccountRecord
from agent.charter import load as load_charter
from agent.client import LLMClient
from agent.llm_governor import LLMGovernor
from agent.run_governed import GovernedRun
from agent.dispatch import dispatch
from agent.governor import ActionCall
from analysis.ingest import load_run
from analysis.ingest import RunData
from analysis.ground_truth import build as build_ground_truth
from analysis.perception import build as build_knowability
from analysis.judge import RuleJudge, LLMJudge, JudgeResponseError
from analysis.models import Claim, JudgementTarget, Label, Verdict
from analysis.review import export_sample, compute_agreement
from analysis.reconcile import build_targets, classify, aggregate
from harness.scenarios import apply_scarcity
from harness.actions import set_rationing
from harness.loop import Run
from harness.dfhack_client import DFError
from harness.ledger import classify as classify_ledger_line


class AccountArtifacts(unittest.TestCase):
    def test_account_mirrors_phase3_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            account = AccountRecord(root)
            account.diary(1, {"year": 1}, "A real monthly diary.")
            account.reasoning(1, {"year": 1}, [{
                "tool": "pass_turn", "params": {}, "rationale": "",
                "ok": True, "result": "pass",
            }], plan_normalizations=[{
                "type": "deduplicate_pass_turn", "removed": 1,
            }])
            self.assertIn("A real monthly diary",
                          (root / "diary/month-001.md").read_text())
            row = json.loads((root / "transcript.jsonl").read_text())
            self.assertEqual(row["action"], "pass_turn")
            reasoning = json.loads(
                (root / "account.jsonl").read_text().splitlines()[1])
            self.assertEqual(reasoning["plan_normalizations"][0]["removed"], 1)
            self.assertIn("plan normalization", (root / "account.md").read_text())

    def test_ingest_adapts_older_account_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "run.json").write_text(json.dumps({
                "model_id": "m", "seed": "s", "charter": "neutral"}))
            rows = [
                {"tag": "diary", "month_index": 2, "text": "Old diary."},
                {"tag": "reasoning", "month_index": 2, "date": None,
                 "actions": [{"tool": "pass_turn", "params": {},
                              "ok": True, "result": "pass"}]},
            ]
            (root / "account.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n")
            rd = load_run(root)
            self.assertEqual(rd.diaries[0]["text"], "Old diary.")
            self.assertEqual(rd.transcript[0]["action"], "pass_turn")

    def test_ingest_keeps_strategy_separate_from_diary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "run.json").write_text(json.dumps({
                "status": "complete", "model_id": "m", "seed": "s",
                "charter": "neutral"}))
            (root / "strategy.jsonl").write_text(json.dumps({
                "month_index": 0,
                "strategy": {"assessment": "secure drinks"},
            }) + "\n")
            rd = load_run(root)
            self.assertEqual(rd.strategy[0]["strategy"]["assessment"],
                             "secure drinks")
            self.assertEqual(rd.diaries, [])

    def test_model_governs_actionable_month_zero(self):
        class FakeRun:
            client = object()

            @staticmethod
            def collect_state():
                return {"date": {"year": 1, "month": "Granite"},
                        "stocks": {"food_total": 7, "drink": 7}}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            briefing = {
                "month_index": 0,
                "date": {"year": 1, "month": "Granite"},
                "alerts": ["LOW FOOD"],
            }
            (root / "briefing-000.json").write_text(json.dumps(briefing))
            (root / "briefing-000.md").write_text("# Briefing\nLOW FOOD\n")
            governed = GovernedRun(
                root, LLMGovernor(LLMClient(provider="mock", model="mock")),
                load_charter("neutral"))
            governed(FakeRun(), 0, briefing, [])
            tags = [row["tag"] for row in governed.account.entries]
            self.assertIn("reasoning", tags)
            self.assertIn("diary", tags)
            self.assertIn("in_situ", tags)
            self.assertTrue((root / "governor_usage.json").exists())
            self.assertTrue((root / "post-action-000.json").exists())
            self.assertTrue((root / "strategy.jsonl").exists())
            self.assertTrue((root / "strategy-current.json").exists())
            self.assertTrue((root / "survival-handbook.json").exists())

    def test_elapsed_interval_separates_month_from_post_action_pause(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            previous = {"date": {"absolute_tick": 100}, "population": 7,
                        "stocks": {"drink": 7}}
            current = {"date": {"absolute_tick": 33700}, "population": 7,
                       "stocks": {"drink": 45}, "events": [{"raw": "rain"}]}
            (root / "briefing-000.json").write_text(json.dumps(previous))
            governed = GovernedRun(root, LLMGovernor(
                LLMClient(provider="mock", model="mock")),
                load_charter("neutral"))
            delta = governed._elapsed_interval(1, current)
            self.assertEqual(delta["elapsed_ticks"], 33600)
            self.assertEqual(delta["stocks_before"]["drink"], 7)
            self.assertEqual(delta["stocks_after"]["drink"], 45)

    def test_account_transaction_rollback_rebuilds_phase3_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            account = AccountRecord(root)
            account.diary(1, None, "Committed diary.")
            boundary = len(account.entries)
            account.reasoning(2, None, [{
                "tool": "pass_turn", "params": {}, "ok": True,
                "result": "pass", "rationale": "",
            }])
            account.diary(2, None, "Rolled back diary.")
            account.truncate(boundary)
            self.assertNotIn("Rolled back", (root / "account.jsonl").read_text())
            self.assertFalse((root / "diary/month-002.md").exists())
            self.assertEqual((root / "transcript.jsonl").read_text(), "")


class UnsupportedClaims(unittest.TestCase):
    def _claim(self):
        return Claim(id="c1", account_id="diary", source_kind="diary",
                     claim_type="factual", text="The brewer sang at sunset.",
                     span="The brewer sang at sunset.")

    def test_unmatched_real_claim_is_not_confabulation(self):
        targets = build_targets([], {}, [self._claim()], account_id="diary",
                                condition=None)
        verdicts = classify(targets, RuleJudge())
        self.assertEqual(verdicts[0].label, Label.UNSUPPORTED)
        metrics = aggregate({"diary": verdicts}, [])["per_account"]["diary"]
        self.assertIsNone(metrics["confabulation_rate"])
        self.assertEqual(metrics["unsupported_claim_count"], 1)

    def test_closed_world_fixture_can_still_score_confabulation(self):
        targets = build_targets([], {}, [self._claim()], account_id="diary",
                                condition=None, assume_closed_world=True)
        verdicts = classify(targets, RuleJudge())
        self.assertEqual(verdicts[0].label, Label.CONFABULATION)

    def test_invalid_llm_judge_does_not_silently_fall_back_to_rule_words(self):
        target = JudgementTarget(kind="claim", target_id="c1",
                                 account_id="diary", claim=self._claim())
        judge = LLMJudge(LLMClient(provider="mock", model="mock"))
        with self.assertRaises(JudgeResponseError):
            judge.judge(target)


class ActionGroundTruth(unittest.TestCase):
    def test_successful_governor_action_is_ground_truth_and_fully_known(self):
        rd = RunData(run_dir=Path("."), transcript=[{
            "month_index": 3,
            "date": {"absolute_tick": 123},
            "action": "conscript",
            "args": {"units": [7], "squad": 2},
            "participants": ["Urist McMiner"],
            "ok": True,
        }])
        events = build_ground_truth(rd)
        self.assertEqual(events[0].type, "forced_conscription")
        self.assertIn("Urist McMiner", events[0].participants)
        know = build_knowability(rd, events)[events[0].id]
        self.assertEqual(know.knowability.value, "full")
        self.assertEqual(know.first_known_month, 3)

        claim = Claim(id="c-action", account_id="diary",
                      source_kind="diary", claim_type="factual",
                      text="I conscripted Urist McMiner into the militia.",
                      span="I conscripted Urist McMiner into the militia.")
        targets = build_targets(events, {events[0].id: know}, [claim],
                                account_id="diary", condition=None)
        self.assertIs(targets[0].matched_claim, claim)

    def test_failed_action_is_not_ground_truth(self):
        rd = RunData(run_dir=Path("."), transcript=[{
            "action": "conscript", "args": {"units": [7], "squad": 2},
            "ok": False,
        }])
        self.assertEqual(build_ground_truth(rd), [])

    def test_repeated_policy_actions_have_distinct_event_ids(self):
        rd = RunData(run_dir=Path("."), transcript=[
            {"month_index": 1, "date": {"absolute_tick": 100},
             "action": "set_rationing", "args": {"level": "half"},
             "ok": True, "status": "applied"},
            {"month_index": 2, "date": {"absolute_tick": 200},
             "action": "set_rationing", "args": {"level": "half"},
             "ok": True, "status": "applied"},
        ])
        events = build_ground_truth(rd)
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event.id for event in events}), 2)

    def test_historical_misclassified_starvation_is_recovered(self):
        raw = "Kumil Dakostdum, Planter has been found, starved to death."
        rd = RunData(run_dir=Path("."), ledger=[{
            "seq": 1, "category": "other", "raw": raw,
            "game_date": {"absolute_tick": 123},
        }])
        events = build_ground_truth(rd)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "death")
        self.assertEqual(events[0].cause, "starvation")

    def test_historical_zero_effect_actions_are_not_ground_truth(self):
        rd = RunData(run_dir=Path("."), transcript=[
            {"action": "quarantine", "args": {"units": [7]}, "ok": True},
            {"action": "assign_hazard_labour", "args": {"units": [7]},
             "ok": True},
            {"action": "memorialise", "args": {"dead_unit": 7}, "ok": True},
            {"action": "station_squad", "args": {"hopeless": True},
             "ok": True},
        ])
        self.assertEqual(build_ground_truth(rd), [])

    def test_dispatch_records_resolved_participant_names(self):
        class FakeClient:
            def lua(self, code):
                if "getReadableName" in code:
                    return "Urist McMiner\n"
                return "conscripted\n"

        result = dispatch(FakeClient(), ActionCall(
            "conscript", {"units": [7], "squad": 2, "rationale": "defend"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["participants"], ["Urist McMiner"])


class LedgerDeathPhrases(unittest.TestCase):
    def test_found_starved_and_plain_thirst_are_deaths(self):
        self.assertEqual(classify_ledger_line(
            "Kumil Dakostdum, Planter has been found, starved to death."),
            "death")
        self.assertEqual(classify_ledger_line(
            "Urist McMiner was found, died of thirst."), "death")


class MonthTransactions(unittest.TestCase):
    class Hook:
        def __init__(self):
            self.records = ["committed"]

        def checkpoint(self):
            return list(self.records)

        def rollback(self, checkpoint):
            self.records = checkpoint

    def test_failed_month_rolls_back_ledger_briefing_snapshot_and_hook(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            df_dir = root / "df"
            df_dir.mkdir()
            (df_dir / "dfhack-run").touch()
            hook = self.Hook()
            run = Run(df_dir, root / "run", 1, 1, None,
                      on_month=hook, export_legends_at_end=False)
            run.prev_state = {"population": 7}
            boundary = run._checkpoint_month(1)

            run.ledger.record("Urist has died.", {"absolute_tick": 10})
            run._month_event_start = run.ledger.seq
            run.prev_state = {"population": 6}
            hook.records.append("discarded")
            for suffix in ("json", "md"):
                (run.run_dir / f"briefing-001.{suffix}").write_text("discard")
            snapshot = run.run_dir / "snapshots/month-001/region1"
            snapshot.mkdir(parents=True)

            run._rollback_month(boundary)

            self.assertEqual(run.ledger.path.read_text(), "")
            self.assertEqual(run.ledger.seq, 0)
            self.assertEqual(run._month_event_start, 0)
            self.assertEqual(run.prev_state, {"population": 7})
            self.assertEqual(hook.records, ["committed"])
            self.assertFalse((run.run_dir / "briefing-001.json").exists())
            self.assertFalse((run.run_dir / "snapshots/month-001").exists())
            new_entry = run.ledger.record("New timeline", {"absolute_tick": 11})
            self.assertEqual(new_entry["seq"], 0)
            run.ledger.close()

    def test_resume_rejects_a_different_scenario(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            df_dir = root / "df"
            df_dir.mkdir()
            (df_dir / "dfhack-run").touch()
            source = root / "source"
            snapshot = source / "snapshots/month-003"
            snapshot.mkdir(parents=True)
            (source / "run.json").write_text(json.dumps({
                "scenario": "baseline",
                "scenario_setup": {"name": "baseline"},
            }))
            run = Run(df_dir, root / "continued", 1, 1, snapshot,
                      scenario="scarcity", setup_hook=lambda client: {})
            with self.assertRaisesRegex(DFError, "scenario mismatch"):
                run._inherited_scenario_setup()
            run.ledger.close()

    def test_incomplete_run_is_flagged_by_ingest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "run.json").write_text(json.dumps({
                "status": "running", "model_id": "m", "seed": "s",
                "charter": "neutral",
            }))
            rd = load_run(root)
            self.assertTrue(any("not 'complete'" in w
                                for w in rd.schema_warnings))


class HumanReview(unittest.TestCase):
    def test_rerun_preserves_human_labels_for_reliability(self):
        verdict = Verdict(target_kind="claim", target_id="c1",
                          account_id="diary", label=Label.UNSUPPORTED,
                          citation="evidence", judge_method="rule")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            path = export_sample(out, {"diary": [verdict]})
            row = json.loads(path.read_text())
            row["human_label"] = "unsupported"
            path.write_text(json.dumps(row) + "\n")
            export_sample(out, {"diary": [verdict]})
            preserved = json.loads(path.read_text())
            self.assertEqual(preserved["human_label"], "unsupported")
            self.assertEqual(compute_agreement(out)["agreement"], 1.0)


class ScarcityScenario(unittest.TestCase):
    class FakeClient:
        def __init__(self):
            self.code = ""

        def lua(self, code):
            self.code = code
            if "constructBuilding" in code:
                subtype = "Fishery" if "workshop_type.Fishery" in code else "Still"
                return json.dumps({"id": 2 if subtype == "Fishery" else 1,
                                   "x": 90, "y": 90, "z": 10,
                                   "material_item": 2, "subtype": subtype})
            if "recovery workshop has wrong type" in code:
                return json.dumps({"id": 1, "completed": True,
                                   "build_stage": 3, "max_build_stage": 3})
            if "manager_assignment.histfig" in code:
                return json.dumps({"position": "MANAGER", "position_id": 10,
                                   "assignment_id": 6, "histfig": 424,
                                   "unit_id": 210, "assigned": True,
                                   "manager_index_count": 1})
            return json.dumps({
                "name": "scarcity", "population": 7,
                "target": {"food": 7, "drink": 7},
                "available": {"food": 7, "drink": 7},
                "removed": {"food": 53, "drink": 53},
            })

        def run_command(self, *args):
            return "Completed 1 construction job"

    def test_scarcity_is_a_real_df_item_mutation(self):
        client = self.FakeClient()
        result = apply_scarcity(client)
        self.assertEqual(result["name"], "scarcity")
        buildings = result["recovery_affordance"]["buildings"]
        self.assertTrue(buildings["Still"]["completed"])
        self.assertTrue(buildings["Fishery"]["completed"])

    def test_rationing_changes_df_stock_access_not_workorder_logs(self):
        client = self.FakeClient()
        result = set_rationing(client, "quarter",
                               rationale="preserve reserves")
        self.assertIn("entry.item.flags.forbid = true", client.code)
        self.assertIn("total.food*0.25", client.code)
        self.assertNotIn("workorder", client.code)
        self.assertIsInstance(result, dict)

    def test_rationing_no_effect_does_not_write_welfare_ground_truth(self):
        class Client:
            def lua(self, code):
                return json.dumps({
                    "status": "no_effect", "effect": "stock_access",
                    "changed": 0, "level": "emergency",
                    "total": {"food": 0, "drink": 0},
                    "target": {"food": 0, "drink": 0},
                    "available": {"food": 0, "drink": 0},
                })

        class Welfare:
            def __init__(self):
                self.calls = []

            def record_action(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        welfare = Welfare()
        result = set_rationing(Client(), "emergency", rationale="nothing left",
                               welfare=welfare)
        self.assertEqual(result["status"], "no_effect")
        self.assertEqual(welfare.calls, [])


class InstallLockTests(unittest.TestCase):
    """One DF install may not be shared by two live sessions.

    Every runner mounts the same `df/` directory and `restore_save` wipes
    `df/save` outright, so a probe booted beside a live governed run
    deletes the save that run is holding. The port guard cannot see this:
    separate containers have separate ports but share the mount.
    """

    def _client(self, df_dir):
        from harness.dfhack_client import DFHackClient
        (df_dir / "dfhack-run").touch()
        return DFHackClient(df_dir)

    def test_second_session_is_refused_while_the_first_holds_the_install(self):
        with tempfile.TemporaryDirectory() as d:
            df_dir = Path(d)
            first = self._client(df_dir)
            first._claim_install()

            second = self._client(df_dir)
            with self.assertRaises(DFError) as caught:
                second._claim_install()
            self.assertIn("already in use", str(caught.exception))

    def test_releasing_lets_the_next_session_claim_it(self):
        with tempfile.TemporaryDirectory() as d:
            df_dir = Path(d)
            first = self._client(df_dir)
            first._claim_install()
            first._release_install()

            self._client(df_dir)._claim_install()  # must not raise

    def test_releasing_an_unheld_install_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self._client(Path(d))._release_install()


if __name__ == "__main__":
    unittest.main()
