"""Operational knowledge, preflight, and persistent-strategy contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from agent.farm_recovery_governor import FarmRecoveryGovernor
from agent.container_recovery_governor import ContainerRecoveryGovernor
from agent.trade_recovery_governor import TradeRecoveryGovernor
from agent.hospital_recovery_governor import HospitalRecoveryGovernor
from agent.treatment_recovery_governor import TreatmentRecoveryGovernor
from harness.scenarios import SCENARIOS, apply_injury
from agent.strategy import StrategyRecord, control_strategy
from harness.actions import (assign_broker, assign_hospital_doctor,
                             assign_manager,
                             build_farm_plot, build_stockpile,
                             build_trade_depot, build_workshop,
                             cancel_workorder,
                             execute_trade,
                             mark_goods_for_trade,
                             furnish_hospital,
                             make_barrels, make_hospital_furniture,
                             make_trade_goods, prepare_farm_room,
                             prioritize_farm_construction,
                             prioritize_trade_depot_construction,
                             prioritize_trader_job,
                             prioritize_workshop_construction, protect_seeds,
                             request_trader,
                             set_farm_crop,
                             designate_water_source, make_well_components,
                             ACTIONS)
from harness.survival import (assess_procedures, build_operational_context,
                              derive_survival_metrics, handbook_digest,
                              load_handbook, write_handbook_snapshot)


def _state(**stock_overrides):
    stocks = {
        "food_total": 0, "food": 0, "plants": 0, "raw_fish": 13,
        "drink": 14, "brewable_plants": 3, "seeds": 8, "wood": 40,
        "available_brewable_plants": 3, "available_raw_fish": 13,
        "available_wood": 40,
        "stone": 20, "bars": 0, "empty_barrels": 0,
        "empty_food_containers": 0,
    }
    stocks.update(stock_overrides)
    return {
        "population": 7,
        "stocks": stocks,
        "operations": {
            "completed_workshops": {"Fishery": 1, "Still": 1},
            "completed_farm_plots": 0,
            "manager_orders": [{"id": 9, "job": "PrepareRawFish"}],
            "active_designations": {"plants": 0, "trees": 0},
        },
        "agriculture": {"available_seed_types": [{
            "plant_id": "MUSHROOM_HELMET_PLUMP", "count": 8,
            "environment": "subterranean",
            "seasons": ["spring", "summer", "autumn", "winter"],
        }]},
    }


class HandbookTests(unittest.TestCase):
    def test_handbook_is_versioned_unique_and_snapshottable(self):
        handbook = load_handbook()
        self.assertEqual(handbook["version"], 13)
        self.assertEqual(len(handbook_digest(handbook)), 64)
        with tempfile.TemporaryDirectory() as d:
            path = write_handbook_snapshot(d, handbook)
            frozen = json.loads(path.read_text())
            self.assertEqual(frozen["sha256"], handbook_digest(handbook))

    def test_metrics_make_rates_and_runway_explicit(self):
        current = _state(food_total=35, drink=70)
        previous = _state(food_total=42, drink=84)
        metrics = derive_survival_metrics(current, previous)
        self.assertEqual(metrics["estimated_runway_months"]["food"], 2.0)
        self.assertEqual(metrics["estimated_runway_months"]["drink"], 2.0)
        self.assertEqual(metrics["observed_stock_delta"]["food_total"], -7)
        self.assertEqual(metrics["planning_rates_per_citizen_month"]["drink"],
                         5.0)

    def test_preflight_finds_fish_feasible_and_brew_blocked_by_container(self):
        procedures = {p["id"]: p for p in assess_procedures(_state())}
        self.assertEqual(procedures["process_raw_fish"]["feasibility"],
                         "feasible")
        self.assertEqual(procedures["brew_available_plants"]["feasibility"],
                         "blocked")
        self.assertEqual(procedures["establish_reliable_farming"][
            "feasibility"], "feasible")
        self.assertIn("set_farm_crop", procedures[
            "establish_reliable_farming"]["action_tools"])

    def test_context_surfaces_pilot_recovery_without_claiming_completion(self):
        context = build_operational_context(_state())
        codes = {f["code"] for f in context["urgent_findings"]}
        self.assertIn("NO_EDIBLE_FOOD", codes)
        self.assertIn("RAW_FISH_RECOVERY_AVAILABLE", codes)
        self.assertIn("SEED_BASE_LOW", codes)
        self.assertIn("Treat feasible as a preflight result, not proof of "
                      "completion.", context["planner_rules"])


class StrategyTests(unittest.TestCase):
    def test_strategy_persists_receipts_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            record = StrategyRecord(d)
            record.record(0, {"month": "Granite"}, control_strategy("first"),
                          [{"tool": "prepare_fish", "status": "applied"}],
                          {"estimated_runway_months": {"food": 0}})
            boundary = len(record.entries)
            record.record(1, {"month": "Slate"}, control_strategy("second"),
                          [{"tool": "prepare_fish", "status": "no_effect"}],
                          {"estimated_runway_months": {"food": 0}})
            record.truncate(boundary)
            self.assertEqual(len(record.entries), 1)
            self.assertNotIn("second", Path(d, "strategy.jsonl").read_text())
            self.assertTrue(Path(d, "strategy-current.json").exists())


class WorkorderRecoveryTests(unittest.TestCase):
    def test_assign_manager_verifies_native_role_without_replacement(self):
        class FakeClient:
            def lua(self, code):
                self.code = code
                return json.dumps({
                    "status": "applied", "effect": "manager_appointed",
                    "dwarf_id": 210, "verified_manager_unit_id": 210,
                })

        client = FakeClient()
        receipt = assign_manager(client, 210)
        self.assertEqual(receipt["verified_manager_unit_id"], 210)
        self.assertIn("occupied by a different historical figure", client.code)
        self.assertIn("getUnitByNobleRole('manager')", client.code)

    def test_assign_broker_verifies_native_role_without_replacement(self):
        class FakeClient:
            def lua(self, code):
                self.code = code
                return json.dumps({
                    "status": "applied", "effect": "broker_appointed",
                    "dwarf_id": 210, "verified_broker_unit_id": 210,
                })

        client = FakeClient()
        receipt = assign_broker(client, 210)
        self.assertEqual(receipt["verified_broker_unit_id"], 210)
        self.assertIn("occupied by a different historical figure", client.code)
        self.assertIn("getUnitByNobleRole('broker')", client.code)

    def test_cancel_workorder_returns_verified_exact_receipt(self):
        class FakeClient:
            def lua(self, code):
                self.code = code
                return json.dumps({
                    "status": "applied",
                    "effect": "manager_order_cancelled",
                    "order_id": 9,
                    "cancelled": {"id": 9, "job": "PrepareRawFish"},
                    "remains": False,
                })

        client = FakeClient()
        receipt = cancel_workorder(client, 9)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["order_id"], 9)
        self.assertIn("dependent order ids", client.code)

    def test_farm_wrappers_keep_dynamic_ids_and_seasons_typed(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def run_json_script(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {"status": "applied", "farm_id": 17}

        client = FakeClient()
        receipt = build_farm_plot(client, "subterranean", 3, 4)
        self.assertEqual(receipt["farm_id"], 17)
        set_farm_crop(client, 17, "mushroom_helmet_plump",
                      ["spring", "winter"])
        self.assertEqual(client.calls[0][0][0:3],
                         ("ops-farm", "build", "subterranean"))
        self.assertEqual(client.calls[1][0][0:5],
                         ("ops-farm", "assign", "17",
                          "MUSHROOM_HELMET_PLUMP", "spring,winter"))

    def test_farm_room_wrapper_keeps_bounded_dimensions_typed(self):
        class FakeClient:
            def run_json_script(self, *args, **kwargs):
                self.call = (args, kwargs)
                return {"status": "applied", "designated_tiles": 28}

        client = FakeClient()
        receipt = prepare_farm_room(client, 5, 5)
        self.assertEqual(receipt["designated_tiles"], 28)
        self.assertEqual(client.call[0][:4],
                         ("ops-farm", "prepare", "5", "5"))

    def test_farm_priority_targets_and_verifies_one_exact_job(self):
        class FakeClient:
            def lua(self, code):
                self.code = code
                return json.dumps({
                    "status": "applied", "farm_id": 17, "job_id": 88,
                    "before": False, "after": True, "suspended": False,
                })

        client = FakeClient()
        receipt = prioritize_farm_construction(client, 17)
        self.assertEqual(receipt["job_id"], 88)
        self.assertIn("df.building.find(17)", client.code)
        self.assertIn("job.flags.do_now = true", client.code)

    def test_workshop_actions_return_exact_native_ids(self):
        class FakeClient:
            def __init__(self):
                self.outputs = iter([{
                    "status": "applied", "workshop_id": 8,
                    "material_item_id": 41, "job_id": 90,
                }, {
                    "status": "applied", "workshop_id": 8,
                    "job_id": 90, "after": True,
                }])

            def lua(self, code):
                return json.dumps(next(self.outputs))

        client = FakeClient()
        built = build_workshop(client, "Carpenters")
        prioritized = prioritize_workshop_construction(client, 8)
        self.assertEqual(built["material_item_id"], 41)
        self.assertEqual(prioritized["job_id"], 90)

    def test_stockpile_wrapper_keeps_kind_dimensions_and_anchor_typed(self):
        class FakeClient:
            def run_json_script(self, *args):
                self.args = args
                return {"status": "applied", "stockpile_id": 12}

        client = FakeClient()
        receipt = build_stockpile(client, "booze", 3, 4, 8)
        self.assertEqual(receipt["stockpile_id"], 12)
        self.assertEqual(client.args,
                         ("ops-logistics", "build-stockpile", "booze",
                          "3", "4", "8"))

    def test_trade_depot_wrappers_use_exact_script_and_id(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def run_json_script(self, *args):
                self.calls.append(args)
                return {"status": "applied", "depot_id": 21,
                        "construction_job_id": 93}

        client = FakeClient()
        built = build_trade_depot(client)
        prioritized = prioritize_trade_depot_construction(client, 21)
        self.assertEqual(built["depot_id"], 21)
        self.assertEqual(prioritized["construction_job_id"], 93)
        self.assertEqual(client.calls, [
            ("ops-trade", "build-depot"),
            ("ops-trade", "prioritize-depot", "21"),
        ])

    def test_mark_goods_wrapper_preserves_exact_item_ids(self):
        class FakeClient:
            def run_json_script(self, *args):
                self.args = args
                return {"status": "applied", "haul_jobs": [
                    {"item_id": 40, "job_id": 2},
                    {"item_id": 41, "job_id": 3},
                ]}

        client = FakeClient()
        receipt = mark_goods_for_trade(client, 21, [40, 41])
        self.assertEqual(len(receipt["haul_jobs"]), 2)
        self.assertEqual(client.args,
                         ("ops-trade", "mark-goods", "21", "40", "41"))

    def test_request_trader_targets_exact_depot(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([json.dumps({
                    "broker_id": 210,
                    "caravan": {"entity_id": 12, "state": "AtDepot"},
                    "trader_requested": False, "trade_flags_whole": 0,
                    "jobs": [],
                }), "", json.dumps({
                    "trader_requested": True,
                    "trade_flags_whole": 1,
                    "jobs": [{"id": 91, "suspended": False,
                              "worker_id": None}],
                }), ""])

            def lua(self, code):
                self.lua_code = getattr(self, "lua_code", "") + code
                return next(self.observations)

            def click_text(self, label):
                self.clicked = label

        client = FakeClient()
        receipt = request_trader(client, 21)
        self.assertEqual(receipt["effect"], "native_trader_job_created")
        self.assertEqual(receipt["trader_job"]["id"], 91)
        self.assertEqual(client.clicked, "Broker requested at depot")
        self.assertIn("df.building.find(21)", client.lua_code)

    def test_prioritize_trader_targets_exact_depot(self):
        class FakeClient:
            def run_json_script(self, *args):
                self.args = args
                return {"status": "applied", "depot_id": 21,
                        "job_id": 91, "after": True}

        client = FakeClient()
        receipt = prioritize_trader_job(client, 21)
        self.assertTrue(receipt["after"])
        self.assertEqual(client.args,
                         ("ops-trade", "prioritize-trader", "21"))

    def test_execute_trade_requires_exact_native_ownership_changes(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([
                    json.dumps({
                        "depot_id": 21,
                        "trader_job": {"id": 91, "worker_id": 55},
                        "caravan": {"index": 0, "days_remaining": 3},
                        "exports": [{"id": 30, "trader": False}],
                        "imports": [{"id": 40, "trader": True}],
                    }),
                    "", "",
                    json.dumps({
                        "export_value": 20, "import_value": 10,
                        "exports": [{"id": 30, "side": 1, "value": 20}],
                        "imports": [{"id": 40, "side": 0, "value": 10}],
                    }),
                    "", "",
                    json.dumps({
                        "exports": [{"id": 30, "trader": True,
                                     "description": "craft"}],
                        "imports": [{"id": 40, "trader": False,
                                     "description": "food"}],
                    }),
                    "",
                ])
                self.screens = iter([
                    ["Trader Profit: 10",
                     "Seize        Trade        Offer as gift"],
                    ["Confirm trade", "Enter: Yes, proceed"],
                    ["A fine trade"],
                ])

            def lua(self, code):
                return next(self.observations)

            def screen_text(self):
                return next(self.screens)

        receipt = execute_trade(FakeClient(), 21, [30], [40])

        self.assertEqual(receipt["effect"], "native_itemized_exchange")
        self.assertTrue(receipt["exports"][0]["trader"])
        self.assertFalse(receipt["imports"][0]["trader"])

    def test_hospital_furnishing_wrapper_uses_registered_native_location(self):
        class FakeClient:
            def run_json_script(self, *args, **kwargs):
                self.args = args
                return {"status": "applied", "zone_id": 3,
                        "location_id": 0}

        client = FakeClient()
        receipt = furnish_hospital(client)
        self.assertEqual(receipt["location_id"], 0)
        self.assertEqual(client.args, ("ops-hospital", "furnish"))

    def test_hospital_furniture_order_is_bounded_and_bound_to_wood(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([{
                    "shops": 1, "wood": 4, "pending": 0,
                    "max_order_id": -1, "output_ids": [],
                }, {
                    "pending": 1, "orders": [{
                        "id": 0, "amount_left": 1, "amount_total": 1,
                        "wood": True, "newly_created": True,
                    }],
                }])

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                self.command = args
                return "ok"

        client = FakeClient()
        receipt = make_hospital_furniture(client, "container", 1)
        order = json.loads(client.command[1])
        self.assertEqual(order, {
            "job": "ConstructChest", "amount_total": 1,
            "material_category": ["wood"],
        })
        self.assertEqual(receipt["purpose"], "hospital_furniture")

    def test_assign_doctor_verifies_both_native_occupation_indices(self):
        class FakeClient:
            def lua(self, code):
                self.code = code
                return json.dumps({
                    "status": "applied", "occupation_id": 2,
                    "location_id": 0, "dwarf_id": 207,
                    "verified_location_index": True,
                    "verified_world_index": True,
                })

        client = FakeClient()
        receipt = assign_hospital_doctor(client, 207, 0)
        self.assertTrue(receipt["verified_location_index"])
        self.assertTrue(receipt["verified_world_index"])
        self.assertIn("location.occupations:insert", client.code)
        self.assertIn("world.occupations.all:insert", client.code)

    def test_barrel_order_is_bound_to_wood(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([{
                    "shops": 1, "wood": 2, "empty": 0, "pending": 0,
                    "max_order_id": -1, "barrel_ids": [],
                }, {
                    "pending": 1, "orders": [{"id": 0,
                                                "newly_created": True}],
                }])
                self.command = None

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                self.command = args
                return "ok"

        client = FakeClient()
        make_barrels(client, 1)
        order = json.loads(client.command[1])
        self.assertEqual(order["job"], "MakeBarrel")
        self.assertEqual(order["material_category"], ["wood"])

    def test_trade_goods_order_is_bound_to_wood_and_tracks_outputs(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([{
                    "shops": 1, "wood": 2, "pending": 0,
                    "max_order_id": -1, "output_ids": [7],
                }, {
                    "pending": 2, "orders": [{
                        "id": 0, "amount_left": 2, "amount_total": 2,
                        "wood": True, "newly_created": True,
                    }],
                }])

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                self.command = args
                return "ok"

        client = FakeClient()
        receipt = make_trade_goods(client, 2)
        order = json.loads(client.command[1])
        self.assertEqual(order["job"], "MakeCrafts")
        self.assertEqual(order["material_category"], ["wood"])
        self.assertEqual(receipt["preconditions"][
            "safe_output_item_ids_before"], [7])

    def test_seed_protection_verifies_enabled_target(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([
                    {"enabled": False, "available_seeds": 8},
                    {"enabled": True, "target": 10,
                     "available_seeds": 8},
                ])

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                return "ok"

        receipt = protect_seeds(
            FakeClient(), "MUSHROOM_HELMET_PLUMP", minimum=10)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["available_seeds"], 8)


class HospitalRecoveryGovernorTests(unittest.TestCase):
    def _hospital_state(self):
        state = _state()
        state["operations"].update({"workshops": [], "manager_orders": []})
        state["healthcare"] = {
            "room_project": {"status": "zoned", "location_id": 0,
                             "zone_id": 3},
            "locations": [{"id": 0, "occupations": []}],
            "furnishings": {
                "beds": 0, "tables": 0, "containers": 0,
                "planned": {"beds": 0, "tables": 0, "containers": 0},
            },
            "available_furniture": {
                "beds": [], "tables": [], "containers": [],
            },
            "doctor_candidates": [{
                "id": 207, "medical_skill_score": 0,
                "selection_burden": 0,
            }],
        }
        return state

    def test_native_location_id_zero_is_not_mistaken_for_missing(self):
        plan = HospitalRecoveryGovernor().act(
            {}, "", self._hospital_state(), {})
        self.assertEqual([a.tool for a in plan.actions], ["build_workshop"])
        self.assertEqual(plan.actions[0].params, {"workshop": "Carpenters"})

    def test_completed_furnishings_assign_least_burdened_candidate(self):
        state = self._hospital_state()
        state["healthcare"]["furnishings"].update({
            "beds": 1, "tables": 1, "containers": 1,
        })
        state["healthcare"]["doctor_candidates"] = [
            {"id": 207, "medical_skill_score": 0,
             "selection_burden": 0},
            {"id": 204, "medical_skill_score": 0,
             "selection_burden": 1},
        ]
        plan = HospitalRecoveryGovernor().act({}, "", state, {})
        self.assertEqual([a.tool for a in plan.actions],
                         ["assign_hospital_doctor"])
        self.assertEqual(plan.actions[0].params,
                         {"dwarf_id": 207, "location_id": 0})

    def test_planned_furniture_is_not_completed_capacity(self):
        state = self._hospital_state()
        state["healthcare"]["furnishings"]["planned"] = {
            "beds": 1, "tables": 1, "containers": 1,
        }
        plan = HospitalRecoveryGovernor().act({}, "", state, {})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertIn("under_construction", plan.strategy["assessment"])


class TradeRecoveryGovernorTests(unittest.TestCase):
    def test_builds_depot_only_with_three_available_logs(self):
        state = _state(available_wood=3)
        state["trade"] = {"depots": []}
        plan = TradeRecoveryGovernor().act({}, "", state, {})
        self.assertEqual([a.tool for a in plan.actions],
                         ["build_trade_depot"])

    def test_prioritizes_exact_incomplete_depot(self):
        state = _state()
        state["trade"] = {"depots": [{
            "id": 21, "complete": False,
            "construction_job": {"high_priority": False},
        }]}
        plan = TradeRecoveryGovernor().act({}, "", state, {})
        self.assertEqual(plan.actions[0].tool,
                         "prioritize_trade_depot_construction")
        self.assertEqual(plan.actions[0].params, {"depot_id": 21})

    def test_requests_broker_mode_when_survival_deadline_is_not_close(self):
        state = _state()
        state["trade"] = {
            "broker": {"id": 7},
            "caravans": [{"active": True, "days_remaining": 60}],
            "depots": [{"id": 21, "complete": True,
                        "trader_requested": False}],
            "safe_export_candidates": [],
        }

        plan = TradeRecoveryGovernor().act({}, "", state, {})

        request = next(a for a in plan.actions
                       if a.tool == "request_trader")
        self.assertEqual(request.params, {"depot_id": 21,
                                          "mode": "broker"})

    def test_requests_anyone_mode_when_drink_is_exhausted(self):
        state = _state(drink=0)
        state["trade"] = {
            "broker": {"id": 7},
            "caravans": [{"active": True, "days_remaining": 60}],
            "depots": [{"id": 21, "complete": True,
                        "trader_requested": False}],
            "safe_export_candidates": [],
        }

        plan = TradeRecoveryGovernor().act({}, "", state, {})

        request = next(a for a in plan.actions
                       if a.tool == "request_trader")
        self.assertEqual(request.params, {"depot_id": 21,
                                          "mode": "anyone"})

    def test_switches_stalled_broker_job_to_anyone_near_deadline(self):
        state = _state()
        state["trade"] = {
            "broker": {"id": 7},
            "caravans": [{"active": True, "days_remaining": 31}],
            "depots": [{
                "id": 21, "complete": True, "trader_requested": True,
                "trader_request_mode": "broker",
                "trader_job": {"id": 91, "suspended": False},
            }],
            "safe_export_candidates": [],
        }

        plan = TradeRecoveryGovernor().act({}, "", state, {})

        request = next(a for a in plan.actions
                       if a.tool == "request_trader")
        self.assertEqual(request.params, {"depot_id": 21,
                                          "mode": "anyone"})

    def test_executes_itemized_exchange_only_after_worker_and_goods_arrive(self):
        state = _state()
        state["operations"]["workshops"] = [{
            "id": 4, "subtype": "Craftsdwarfs", "complete": True,
        }]
        state["operations"]["manager"] = {"assigned": True}
        state["trade"] = {
            "broker": {"id": 7},
            "caravans": [{"active": True, "days_remaining": 3,
                          "trade_state": "AtDepot"}],
            "depots": [{
                "id": 21, "complete": True, "trader_requested": True,
                "trader_request_mode": "anyone",
                "trader_job": {"id": 91,
                               "worker": {"id": 55}},
            }],
            "safe_export_candidates": [{
                "id": 30, "eligible": True, "at_depot": True,
            }],
            "survival_import_candidates": [{
                "id": 40, "survival_role": "food",
            }],
        }

        plan = TradeRecoveryGovernor().act({}, "", state, {})

        exchange = next(a for a in plan.actions
                        if a.tool == "execute_trade")
        self.assertEqual(exchange.params, {
            "depot_id": 21,
            "export_item_ids": [30],
            "import_item_ids": [40],
        })


class FarmRecoveryGovernorTests(unittest.TestCase):
    def test_crop_environment_drives_farm_environment(self):
        state = _state()
        state["operations"]["farms"] = []

        plan = FarmRecoveryGovernor().act({}, "", state, {})

        build = next(a for a in plan.actions
                     if a.tool == "build_farm_plot")
        self.assertEqual(build.params["environment"], "subterranean")

    def test_failed_subterranean_placement_starts_bounded_room(self):
        state = _state()
        state["operations"]["farms"] = []
        state["agriculture"]["seed_protection"] = {
            "enabled": True,
            "targets": [{"plant_id": "MUSHROOM_HELMET_PLUMP",
                         "minimum": 10}],
        }
        state["dwarves"] = [{
            "id": 44, "adult": True, "profession": "Miner",
            "labors": {"MINE": True, "FISH": True},
        }]
        context = {"account": [{
            "tag": "reasoning", "actions": [{
                "tool": "build_farm_plot", "status": "failed",
                "params": {"environment": "subterranean"},
            }],
        }]}

        plan = FarmRecoveryGovernor().act({}, "", state, context)

        self.assertEqual([a.tool for a in plan.actions], [
            "prepare_farm_room", "assign_labor"])
        self.assertEqual(plan.actions[0].params, {"width": 5, "height": 5})
        self.assertEqual(plan.actions[1].params, {
            "dwarf_id": 44, "labor": "FISH", "enabled": False,
        })

    def test_incomplete_farm_frees_an_existing_planter_from_fishing(self):
        state = _state()
        state["agriculture"] = {"available_seed_types": [{
            "plant_id": "BERRIES_PRICKLE", "count": 2,
            "environment": "surface", "seasons": [
                "spring", "summer", "autumn", "winter"],
        }]}
        state["operations"]["farms"] = [{
            "id": 3, "environment": "surface", "complete": False,
            "crops": {}, "construction_job": {"high_priority": False},
        }]
        state["dwarves"] = [{
            "id": 44, "adult": True,
            "labors": {"PLANT": True, "FISH": True},
        }]

        plan = FarmRecoveryGovernor().act(
            {}, "", state, {"prior_strategy": {
                "execution_receipts": [{
                    "tool": "protect_seeds", "status": "applied",
                    "result": {"crop_id": "BERRIES_PRICKLE"},
                }],
            }})

        self.assertEqual([a.tool for a in plan.actions], [
            "prioritize_farm_construction", "assign_labor"])
        self.assertEqual(plan.actions[0].params, {"farm_id": 3})
        self.assertEqual(plan.actions[1].params, {
            "dwarf_id": 44, "labor": "FISH", "enabled": False,
        })

    def test_completed_protected_farm_does_not_switch_crops(self):
        state = _state()
        state["operations"]["farms"] = [{
            "id": 3, "environment": "surface", "complete": True,
            "crops": {season: "KANIWA" for season in (
                "spring", "summer", "autumn", "winter")},
        }]
        state["agriculture"] = {
            "available_seed_types": [],
            "seed_protection": {
                "enabled": True,
                "targets": [{"plant_id": "KANIWA", "minimum": 10,
                             "available_seeds": 0}],
            },
        }

        plan = FarmRecoveryGovernor().act({}, "", state, {})

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].tool, "pass_turn")


class ContainerRecoveryGovernorTests(unittest.TestCase):
    def test_completed_shop_reenables_the_professional_carpenter(self):
        state = _state()
        state["operations"]["workshops"] = [{
            "id": 8, "subtype": "Carpenters", "complete": True,
        }]
        state["dwarves"] = [{
            "id": 55, "adult": True, "profession": "Woodworker",
            "labors": {"CARPENTER": False, "CUTWOOD": True},
        }]

        plan = ContainerRecoveryGovernor().act({}, "", state, {})

        self.assertEqual([a.tool for a in plan.actions], [
            "make_barrels", "assign_labor", "assign_labor"])
        self.assertEqual(plan.actions[1].params, {
            "dwarf_id": 55, "labor": "CARPENTER", "enabled": True,
        })

    def test_missing_available_log_starts_bounded_tree_cutting(self):
        state = _state(available_wood=0)
        state["operations"]["workshops"] = []
        state["dwarves"] = [{
            "id": 55, "adult": True, "profession": "Woodworker",
            "labors": {"CUTWOOD": True, "FISH": True},
        }]

        plan = ContainerRecoveryGovernor().act({}, "", state, {})

        self.assertEqual([a.tool for a in plan.actions], [
            "chop_trees", "assign_labor"])
        self.assertEqual(plan.actions[0].params, {"qty": 2})

    def test_incomplete_carpenter_shop_is_prioritized_and_staffed(self):
        state = _state()
        state["operations"]["workshops"] = [{
            "id": 8, "subtype": "Carpenters", "complete": False,
            "construction_job": {"high_priority": False},
        }]
        state["dwarves"] = [{
            "id": 55, "adult": True, "profession": "Woodworker",
            "labors": {"CARPENTER": True, "FISH": True},
        }]

        plan = ContainerRecoveryGovernor().act({}, "", state, {})

        self.assertEqual([a.tool for a in plan.actions], [
            "prioritize_workshop_construction", "assign_labor"])
        self.assertEqual(plan.actions[1].params, {
            "dwarf_id": 55, "labor": "FISH", "enabled": False,
        })

    def test_queued_barrel_is_not_treated_as_physical_success(self):
        state = _state()
        state["stocks"]["tracked_item_ids"] = {"barrels": [10, 11]}
        state["operations"]["workshops"] = [{
            "id": 8, "subtype": "Carpenters", "complete": True,
        }]
        state["dwarves"] = [{
            "id": 55, "adult": True, "profession": "Woodworker",
            "labors": {"CARPENTER": False},
        }]
        context = {"account": [{"tag": "reasoning", "actions": [{
            "tool": "make_barrels", "status": "applied", "result": {
                "preconditions": {"barrel_item_ids_before": [10, 11]},
            },
        }]}]}

        plan = ContainerRecoveryGovernor().act({}, "", state, context)

        self.assertEqual([a.tool for a in plan.actions], ["assign_labor"])
        self.assertIn("False", plan.strategy["assessment"])

    def test_new_barrel_item_id_completes_physical_acceptance(self):
        state = _state()
        state["stocks"]["tracked_item_ids"] = {"barrels": [10, 11, 99]}
        state["operations"]["workshops"] = [{
            "id": 8, "subtype": "Carpenters", "complete": True,
        }]
        context = {"account": [{"tag": "reasoning", "actions": [{
            "tool": "make_barrels", "status": "applied", "result": {
                "preconditions": {"barrel_item_ids_before": [10, 11]},
            },
        }]}]}

        plan = ContainerRecoveryGovernor().act({}, "", state, context)

        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertIn("True", plan.strategy["assessment"])


class WaterChainTests(unittest.TestCase):
    def test_water_procedure_stays_unavailable_until_live_verified(self):
        state = _state()
        state["water"] = {
            "wells": [], "visible_tiles": {"fresh": 6, "salt": 0,
                                           "stagnant": 2},
            "fresh_access_sample": [{"x": 10, "y": 12, "z": 100,
                                     "depth": 4,
                                     "adjacent": {"x": 10, "y": 13,
                                                  "z": 100}}],
            "components": {"buckets": [], "chains": [7],
                           "mechanisms": [], "blocks": [],
                           "boulders": [30, 31]},
        }
        procedures = {p["id"]: p for p in assess_procedures(state)}
        water = procedures["establish_clean_water"]
        # Even with every requirement observable and met, an unverified
        # procedure must not be offered as feasible: no live micro-scenario
        # has yet demonstrated the chain on the pinned runtime.
        self.assertEqual(water["implementation_status"], "unverified")
        self.assertEqual(water["feasibility"], "unavailable")

    def test_every_water_tool_is_dispatchable_and_schema_backed(self):
        from agent.schemas import SCHEMAS_BY_NAME
        handbook = load_handbook()
        water = next(p for p in handbook["procedures"]
                     if p["id"] == "establish_clean_water")
        for tool in water["action_tools"]:
            self.assertIn(tool, ACTIONS)
            self.assertIn(tool, SCHEMAS_BY_NAME)

    def test_stone_order_binds_an_observed_material_token(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([{
                    "shops": 1, "inputs": 3, "pending": 0,
                    "max_order_id": -1, "output_ids": [],
                    "material": "INORGANIC:LIMESTONE", "material_item": 2164,
                }, {
                    "pending": 1, "orders": [{
                        "id": 0, "amount_left": 1, "amount_total": 1,
                        "validated": False, "active": False,
                        "newly_created": True,
                    }],
                }])

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                self.command = args
                return "ok"

        client = FakeClient()
        receipt = make_well_components(client, "block", 1)
        # An unbound stone order validates as false forever and never
        # reaches a workshop, so the material must come from an item the
        # observer actually saw.
        self.assertEqual(json.loads(client.command[1]), {
            "job": "ConstructBlocks", "amount_total": 1,
            "material": "INORGANIC:LIMESTONE",
        })
        self.assertEqual(receipt["material_source_item_id"], 2164)

    def test_stone_order_refuses_when_no_material_is_decodable(self):
        class FakeClient:
            def lua(self, code):
                return json.dumps({
                    "shops": 1, "inputs": 2, "pending": 0,
                    "max_order_id": -1, "output_ids": [],
                    "material": None, "material_item": None,
                })

        with self.assertRaises(Exception):
            make_well_components(FakeClient(), "block", 1)

    def test_bucket_order_is_bound_to_wood(self):
        class FakeClient:
            def __init__(self):
                self.observations = iter([{
                    "shops": 1, "inputs": 3, "pending": 0,
                    "max_order_id": -1, "output_ids": [],
                    "material": "PLANT_MAT:OAK:WOOD", "material_item": 5,
                }, {
                    "pending": 1, "orders": [{
                        "id": 0, "amount_left": 1, "amount_total": 1,
                        "validated": False, "active": False,
                        "newly_created": True,
                    }],
                }])

            def lua(self, code):
                return json.dumps(next(self.observations))

            def run_command(self, *args):
                self.command = args
                return "ok"

        client = FakeClient()
        receipt = make_well_components(client, "bucket", 1)
        self.assertEqual(json.loads(client.command[1]), {
            "job": "MakeBucket", "amount_total": 1,
            "material_category": ["wood"],
        })
        self.assertEqual(receipt["purpose"], "well_component")

    def test_well_component_rejects_unknown_kind_and_unbounded_qty(self):
        with self.assertRaises(Exception):
            make_well_components(None, "barrel", 1)
        with self.assertRaises(Exception):
            make_well_components(None, "block", 0)
        with self.assertRaises(Exception):
            make_well_components(None, "block", 6)

    def test_water_source_zone_footprint_is_bounded(self):
        with self.assertRaises(Exception):
            designate_water_source(None, 1, 2, 3, width=6, height=1)
        with self.assertRaises(Exception):
            designate_water_source(None, 1, 2, 3, width=1, height=0)

    def test_briefing_carries_water_observation(self):
        from harness import briefing as briefing_mod
        state = _state()
        state["water"] = {
            "wells": [{"id": 4, "x": 1, "y": 2, "z": 3, "complete": False,
                       "reachable": True}],
            "visible_tiles": {"fresh": 5, "salt": 0, "stagnant": 1},
            "fresh_access_sample": [],
            "components": {"buckets": [9], "chains": [], "mechanisms": [],
                           "blocks": [], "boulders": []},
        }
        briefing = briefing_mod.build(state, [], None, 3)
        self.assertEqual(briefing["water"]["visible_tiles"]["fresh"], 5)
        markdown = briefing_mod.render_markdown(briefing)
        self.assertIn("Water: visible tiles fresh=5", markdown)
        self.assertIn("buckets=1", markdown)


class TreatmentRecoveryGovernorTests(unittest.TestCase):
    @staticmethod
    def _briefing(patients, doctor=True, wells=(), zones=(), sample=()):
        occupations = ([{"type": "DOCTOR", "unit_alive": True}]
                       if doctor else [])
        return {
            "healthcare": {
                "patients": list(patients),
                "medical_jobs": [],
                "locations": [{"id": 0, "occupations": occupations}],
                "doctor_candidates": [{"id": 207}],
            },
            "water": {"wells": list(wells), "source_zones": list(zones),
                      "fresh_access_sample": list(sample)},
        }

    def test_no_patient_is_a_pass_not_a_claim(self):
        gov = TreatmentRecoveryGovernor()
        plan = gov.act({}, "", self._briefing([]), {})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertIn("no_observed_patient", plan.strategy["assessment"])

    def test_patient_without_doctor_assigns_ranked_candidate(self):
        briefing = self._briefing(
            [{"id": 210, "wound_count": 2, "health_requests": ["rq_diagnosis"]}],
            doctor=False)
        plan = TreatmentRecoveryGovernor().act({}, "", briefing, {})
        self.assertEqual(plan.actions[0].tool, "assign_hospital_doctor")
        self.assertEqual(plan.actions[0].params["dwarf_id"], 207)

    def test_patient_without_water_designates_verified_source(self):
        briefing = self._briefing(
            [{"id": 210, "wound_count": 2, "health_requests": []}],
            sample=[{"x": 8, "y": 9, "z": 100, "depth": 5,
                     "adjacent": {"x": 8, "y": 10, "z": 100}}])
        plan = TreatmentRecoveryGovernor().act({}, "", briefing, {})
        self.assertEqual(plan.actions[0].tool, "designate_water_source")
        self.assertEqual(plan.actions[0].params, {"x": 8, "y": 9, "z": 100})

    def test_patient_with_water_and_doctor_lets_native_chain_run(self):
        briefing = self._briefing(
            [{"id": 210, "wound_count": 1, "health_requests": []}],
            wells=[{"id": 5, "complete": True}])
        plan = TreatmentRecoveryGovernor().act({}, "", briefing, {})
        self.assertEqual([a.tool for a in plan.actions], ["pass_turn"])
        self.assertIn("patient_flagged_awaiting_native_jobs",
                      plan.strategy["assessment"])


class InjuryFixtureTests(unittest.TestCase):
    def test_injury_scenario_is_registered(self):
        self.assertIn("injury", SCENARIOS)

    def test_drop_height_is_bounded(self):
        with self.assertRaises(ValueError):
            apply_injury(None, drop_height=0)
        with self.assertRaises(ValueError):
            apply_injury(None, drop_height=4)


if __name__ == "__main__":
    unittest.main()
