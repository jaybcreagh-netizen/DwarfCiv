"""Workstream A acceptance: welfare records preserve contemporaneous rationale,
link compatible victim-scoped consequences, and do not manufacture causality
from fortress-wide policy timing.

The scenario (no live DF needed — welfare matching is pure Python over the
ledger): set rationing under low food, confine a unit, and leave a death
un-memorialised. Then feed in later deaths and assert the conservative linking
rules.
"""

import tempfile
import unittest
from pathlib import Path

from harness.welfare import (WelfareTrace, death_cause, death_victim,
                             date_token)
from harness.actions import (quarantine, lockdown, memorialise,
                             set_rescue_priority, set_medical_priority,
                             assign_hazard_labour)
from harness.dfhack_client import DFError


def _date(year, month, day=1):
    return {"year": year, "month": month, "day": day,
            "pretty": f"{day} {month}, year {year}"}


def _death(seq, raw, date):
    return {"seq": seq, "category": "death", "raw": raw, "game_date": date}


class WelfareScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "welfare.jsonl"
        self.w = WelfareTrace(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rationale_is_required(self):
        with self.assertRaises(ValueError):
            self.w.record_action("set_rationing", _date(101, "Slate"),
                                 {"level": "half"}, rationale="  ",
                                 kind="policy_set")

    def test_fortress_policy_does_not_manufacture_causal_death_links(self):
        # 1. Set emergency rationing under low food (a policy).
        ration = self.w.record_action(
            "set_rationing", _date(101, "Slate"), {"level": "emergency"},
            rationale="Food is critically low; cutting rations to stretch "
                      "stores until the spring caravan.",
            kind="policy_set")
        self.assertEqual(ration["rationale"][:9], "Food is c")
        self.assertTrue(ration["id"].startswith("set_rationing@"))

        # 2. Confine a specific unit (a per-unit moral action).
        self.w.record_action(
            "quarantine", _date(101, "Slate"), {"units": [42], "area": "Sickward"},
            rationale="Confining Urist McMiner, who is feverish, to stop "
                      "contagion spreading through the dormitory.",
            affected_scope=["Urist McMiner"])

        # 3. Months later: two deaths. A thirst death (fortress-wide, charge to
        #    rationing) and the confined miner dying in the sealed ward.
        events = [
            _death(400, "Stukos Ducimber has died of thirst.",
                   _date(101, "Felsite")),
            _death(401, "A kitten has starved to death.",
                   _date(101, "Felsite")),
            _death(402, "Urist McMiner has bled to death.",
                   _date(101, "Hematite")),
        ]
        links = self.w.match_deaths(events)

        # Cause + temporal overlap is not enough to attribute either death to
        # rationing. The policy remains recorded, with no fabricated effects.
        ration_rec = self.w.find(ration["id"])
        self.assertEqual(ration_rec["consequences"], [])

        # The miner's bleeding death does NOT attach to rationing (wrong cause)
        # and is left without memorialisation.
        self.assertTrue(all(c["unit"] != "Urist McMiner"
                            for c in ration_rec["consequences"]))
        self.assertEqual(self.w.memorialised_units(), set())
        self.assertEqual(links, [])

    def test_per_unit_scope_beats_fortress_policy(self):
        self.w.record_action(
            "set_rationing", _date(5, "Slate"), {"level": "half"},
            rationale="stretch food", kind="policy_set")
        self.w.record_action(
            "conscript", _date(5, "Slate"), {"units": [7], "squad": 1},
            rationale="need bodies on the wall",
            affected_scope=["Catten Boatmurdered"])
        links = self.w.match_deaths([
            _death(10, "Catten Boatmurdered has been struck down.",
                   _date(5, "Felsite"))])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["attributed_to"][:9], "conscript")

    def test_superseding_policy_changes_active_record_without_causal_claim(self):
        old = self.w.record_action("set_rationing", _date(5, "Slate"),
                                    {"level": "half"}, rationale="a",
                                    kind="policy_set")
        new = self.w.record_action("set_rationing", _date(5, "Hematite"),
                                   {"level": "emergency"}, rationale="b",
                                   kind="policy_set")
        self.assertFalse(self.w.find(old["id"])["active"])
        self.assertEqual(self.w.find(old["id"])["superseded_by"], new["id"])
        self.w.match_deaths([_death(1, "X has died of thirst.",
                                    _date(5, "Malachite"))])
        self.assertEqual(len(self.w.find(new["id"])["consequences"]), 0)
        self.assertEqual(len(self.w.find(old["id"])["consequences"]), 0)

    def test_persistence_round_trip(self):
        self.w.record_action("lockdown", _date(2, "Slate"),
                             {"burrow": "Keep", "level": "hard"},
                             rationale="seal the breach", kind="policy_set")
        reloaded = WelfareTrace(self.path)
        self.assertEqual(len(reloaded.records), 1)
        self.assertEqual(reloaded.records[0]["tool"], "lockdown")

    def test_unimplemented_policies_fail_before_welfare_logging(self):
        calls = [
            lambda: quarantine(None, [1], "Ward", rationale="r",
                               welfare=self.w),
            lambda: lockdown(None, "Keep", "hard", rationale="r",
                             welfare=self.w),
            lambda: memorialise(None, 1, rationale="r", welfare=self.w),
            lambda: set_rescue_priority(None, "children_first", rationale="r",
                                        welfare=self.w),
            lambda: set_medical_priority(None, "most_savable", rationale="r",
                                         welfare=self.w),
            lambda: assign_hazard_labour(None, [1], "Magma",
                                         rationale="r", welfare=self.w),
        ]
        for call in calls:
            with self.assertRaises(DFError):
                call()
        self.assertEqual(self.w.records, [])


class CauseParsing(unittest.TestCase):
    def test_causes(self):
        self.assertEqual(death_cause("X has died of thirst."), "dehydration")
        self.assertEqual(death_cause("X has starved to death."), "starvation")
        self.assertEqual(death_cause("X has bled to death."), "bleeding")
        self.assertEqual(death_cause("X has been crushed."), "crushing")
        self.assertIsNone(death_cause("X has thrown a tantrum."))

    def test_victim(self):
        self.assertEqual(death_victim("Urist McMiner has died of thirst."),
                         "Urist McMiner")
        self.assertEqual(death_victim("The kitten has starved to death."),
                         "kitten")

    def test_date_token(self):
        self.assertEqual(date_token({"year": 5, "month": "Slate", "day": 3}),
                         "5-Slate-3")
        self.assertEqual(date_token({"absolute_tick": 99}), "t99")
        self.assertEqual(date_token(None), "unknown")


if __name__ == "__main__":
    unittest.main()
