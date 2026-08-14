"""Deterministic renewable-food acceptance governor.

This is an executable micro-scenario, not a substitute for the research
governor. It proves that observed seeds can be carried through farm placement,
construction labor, seed protection, and seasonal crop assignment using the
same schemas and receipts as a model run.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class FarmRecoveryGovernor(Governor):
    name = "acceptance:farm-recovery"
    model_id = "control:farm-recovery-v2"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        agriculture = briefing_json.get("agriculture") or {}
        operations = briefing_json.get("operations") or {}
        seed_types = agriculture.get("available_seed_types") or []
        farms = operations.get("farms") or []
        actions: list[ActionCall] = []

        # Once the acceptance farm has a crop, keep validating that project
        # instead of switching to whichever seed happens to remain loose in
        # storage after planting consumed the previous stack.
        assigned_crop = None
        assigned_environment = None
        for farm in farms:
            if not farm.get("complete"):
                continue
            assigned = farm.get("crops")
            if isinstance(assigned, dict):
                assigned_crop = next((value for value in assigned.values()
                                      if value), None)
            if assigned_crop:
                assigned_environment = farm.get("environment")
                break

        # Prefer an abundant, year-round, brewable crop. The environment is a
        # property of the observed raw; the controller never silently treats
        # underground seeds as surface crops.
        candidates = [s for s in seed_types
                      if s.get("environment") in {"surface", "subterranean"}
                      and int(s.get("count") or 0) > 0
                      and s.get("seasons")]
        candidates.sort(key=lambda s: (-int(s.get("count") or 0),
                                       -len(s.get("seasons") or []),
                                       -int(bool(s.get("brewable"))),
                                       str(s.get("plant_id"))))
        crop = next((s for s in candidates
                     if s.get("plant_id") == assigned_crop), None)
        if crop is None and assigned_crop:
            crop = {"plant_id": assigned_crop, "seasons": [],
                    "environment": assigned_environment}
        elif crop is None and candidates:
            crop = candidates[0]
        if crop is None:
            return ActionPlan(
                actions=[ActionCall("pass_turn")],
                strategy=control_strategy(
                    "Farm acceptance is blocked: no observed crop seeds."),
                diary="No seed-backed crop was available.")

        crop_id = crop["plant_id"]
        environment = crop.get("environment") or assigned_environment
        target_farms = [f for f in farms
                        if f.get("environment") == environment]

        def reserve_worker(required_labor: str, profession: str) -> None:
            dwarves = [d for d in briefing_json.get("dwarves") or []
                       if d.get("adult")]
            eligible = [d for d in dwarves
                        if (d.get("labors") or {}).get(required_labor)]
            worker = next((d for d in dwarves
                           if str(d.get("profession") or "").lower()
                           == profession.lower()), None)
            worker = worker or (eligible[0] if eligible else None)
            worker = worker or (dwarves[0] if dwarves else None)
            if not worker:
                return
            labors = worker.get("labors") or {}
            if not labors.get(required_labor):
                actions.append(ActionCall("assign_labor", {
                    "dwarf_id": worker["id"], "labor": required_labor,
                    "enabled": True}))
            for labor in ("MINE", "PLANT", "FISH", "CLEAN_FISH",
                          "HERBALIST", "BREWER", "CARPENTER", "CUTWOOD"):
                if labor != required_labor and labors.get(labor):
                    actions.append(ActionCall("assign_labor", {
                        "dwarf_id": worker["id"], "labor": labor,
                        "enabled": False}))
        protection = agriculture.get("seed_protection") or {}
        observed_protected = (
            protection.get("enabled")
            and any(t.get("plant_id") == crop_id
                    and int(t.get("minimum") or 0) >= 10
                    for t in protection.get("targets") or []))
        prior = context.get("prior_strategy") or {}
        prior_receipts = prior.get("execution_receipts") or []
        protected = any(
            receipt.get("tool") == "protect_seeds"
            and receipt.get("status") == "applied"
            and (receipt.get("result") or {}).get("crop_id") == crop_id
            for receipt in prior_receipts)
        if not (observed_protected or protected):
            actions.append(ActionCall(
                "protect_seeds", {"crop_id": crop_id, "minimum": 10}))

        if not target_farms:
            previous_failed = any(
                action.get("tool") == "build_farm_plot"
                and action.get("status") == "failed"
                and (action.get("params") or {}).get("environment")
                    == environment
                for entry in context.get("account") or []
                if entry.get("tag") == "reasoning"
                for action in entry.get("actions") or [])
            farm_room = agriculture.get("farm_room_project")
            if (previous_failed and environment == "subterranean"
                    and not farm_room):
                actions.append(ActionCall("prepare_farm_room", {
                    "width": 5, "height": 5}))
                reserve_worker("MINE", "Miner")
            elif (previous_failed and environment == "subterranean"
                  and farm_room.get("status") == "ready"):
                actions.append(ActionCall("build_farm_plot", {
                    "environment": environment, "width": 3, "height": 3}))
            elif (previous_failed and environment == "subterranean"
                  and farm_room.get("status") == "digging"):
                reserve_worker("MINE", "Miner")
            elif not previous_failed:
                actions.append(ActionCall("build_farm_plot", {
                    "environment": environment, "width": 3, "height": 3}))
        else:
            farm = target_farms[0]
            if not farm.get("complete"):
                construction_job = farm.get("construction_job") or {}
                if not construction_job.get("high_priority"):
                    actions.append(ActionCall(
                        "prioritize_farm_construction", {"farm_id": farm["id"]}))
                # Reserve one known planter for this blocked survival
                # project. This acceptance governor is deliberately more
                # prescriptive than the research governor; every removed
                # labor is separately receipted and reversible.
                reserve_worker("PLANT", "Planter")
            else:
                assigned = farm.get("crops")
                if not isinstance(assigned, dict):
                    assigned = {}
                seasons = [season for season in crop.get("seasons") or []
                           if assigned.get(season) != crop_id]
                if seasons:
                    actions.append(ActionCall("set_farm_crop", {
                        "farm_id": farm["id"], "crop_id": crop_id,
                        "seasons": seasons}))

        if not actions:
            actions.append(ActionCall("pass_turn"))
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                f"Acceptance controller is establishing a {environment} "
                f"{crop_id} "
                "farm with protected seeds and verified seasonal assignments."),
            diary=("I followed the deterministic farm acceptance procedure; "
                   "receipts and later observations determine completion."),
        )


def build() -> FarmRecoveryGovernor:
    return FarmRecoveryGovernor()


__all__ = ["FarmRecoveryGovernor", "build"]
