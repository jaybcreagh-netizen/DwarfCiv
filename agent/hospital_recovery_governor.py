"""Deterministic protected-room and hospital-location acceptance controller."""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class HospitalRecoveryGovernor(Governor):
    name = "acceptance:hospital-recovery"
    model_id = "control:hospital-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        healthcare = briefing_json.get("healthcare") or {}
        project = healthcare.get("room_project")
        locations = healthcare.get("locations") or []
        furnishings = healthcare.get("furnishings") or {}
        planned = furnishings.get("planned") or {}
        available = healthcare.get("available_furniture") or {}
        operations = briefing_json.get("operations") or {}
        actions: list[ActionCall] = []
        phase = "verify"

        if (locations and project
                and project.get("location_id") is None):
            # Recover an earlier partial action whose native location exists
            # but whose verified ids were not persisted in the project record.
            actions.append(ActionCall("establish_hospital_zone"))
            phase = "reconcile_native_location_receipt"
        elif locations:
            required = ("beds", "tables", "containers")
            if all(int(furnishings.get(kind) or 0) > 0 for kind in required):
                occupations = [occ for loc in locations
                               for occ in loc.get("occupations") or []]
                if any(occ.get("type") == "DOCTOR"
                       and occ.get("unit_alive") for occ in occupations):
                    actions.append(ActionCall("pass_turn"))
                    phase = "basic_hospital_ready_treatment_validation_pending"
                else:
                    candidates = healthcare.get("doctor_candidates") or []
                    if candidates:
                        actions.append(ActionCall(
                            "assign_hospital_doctor", {
                                "dwarf_id": candidates[0]["id"],
                                "location_id": locations[0]["id"],
                            }))
                        phase = "assign_native_hospital_doctor"
                    else:
                        actions.append(ActionCall("pass_turn"))
                        phase = "no_eligible_doctor_candidate"
            elif all(int(furnishings.get(kind) or 0)
                     + int(planned.get(kind) or 0) > 0 for kind in required):
                actions.append(ActionCall("pass_turn"))
                phase = "furnishings_under_construction"
            else:
                workshops = operations.get("workshops") or []
                carpenter = next((w for w in workshops
                                  if w.get("subtype") == "Carpenters"), None)
                if not carpenter:
                    actions.append(ActionCall(
                        "build_workshop", {"workshop": "Carpenters"}))
                    phase = "designate_carpenter_workshop"
                elif not carpenter.get("complete"):
                    actions.append(ActionCall(
                        "prioritize_workshop_construction",
                        {"workshop_id": carpenter["id"]}))
                    phase = "complete_carpenter_workshop"
                else:
                    jobs = {"beds": "ConstructBed",
                            "tables": "ConstructTable",
                            "containers": "ConstructChest"}
                    kinds = {"beds": "bed", "tables": "table",
                             "containers": "container"}
                    pending = {str(o.get("job"))
                               for o in operations.get("manager_orders") or []
                               if int(o.get("amount_left") or 0) > 0}
                    missing_orders = []
                    for plural in required:
                        capacity = (int(furnishings.get(plural) or 0)
                                    + int(planned.get(plural) or 0)
                                    + len(available.get(plural) or []))
                        if capacity == 0 and jobs[plural] not in pending:
                            missing_orders.append(ActionCall(
                                "make_hospital_furniture",
                                {"kind": kinds[plural], "qty": 1}))
                    if missing_orders:
                        actions.extend(missing_orders)
                        phase = "queue_missing_hospital_furniture"
                    elif all((int(furnishings.get(kind) or 0)
                              + int(planned.get(kind) or 0)
                              + len(available.get(kind) or [])) > 0
                             for kind in required):
                        actions.append(ActionCall("furnish_hospital"))
                        phase = "install_basic_hospital_furnishings"
                    else:
                        actions.append(ActionCall("pass_turn"))
                        phase = "manufacture_hospital_furnishings"
        elif not project:
            actions.append(ActionCall(
                "prepare_hospital_room", {"width": 7, "height": 7}))
            phase = "designate_protected_room"
        elif ((project.get("access_tiles") or [{}])[0].get("dig") == "No"
              and project.get("status") not in {"ready", "zoned"}):
            actions.append(ActionCall("repair_hospital_access"))
            adults = [d for d in briefing_json.get("dwarves") or []
                      if d.get("adult")]
            miner = next((d for d in adults
                          if "miner" in str(
                              d.get("profession") or "").lower()), None)
            miner = miner or (adults[0] if adults else None)
            if miner:
                actions.append(ActionCall("assign_labor", {
                    "dwarf_id": miner["id"], "labor": "MINE",
                    "enabled": True,
                }))
            phase = "repair_visible_access"
        elif project.get("status") == "ready":
            actions.append(ActionCall("establish_hospital_zone"))
            phase = "create_native_location"
        elif project.get("status") in {"blocked", "unsafe"}:
            actions.append(ActionCall("pass_turn"))
            phase = "room_" + str(project.get("status"))
        else:
            dwarves = [d for d in briefing_json.get("dwarves") or []
                       if d.get("adult")]
            miner = next((d for d in dwarves
                          if "miner" in str(
                              d.get("profession") or "").lower()), None)
            miner = miner or next((d for d in dwarves
                                   if (d.get("labors") or {}).get("MINE")),
                                  None)
            miner = miner or (dwarves[0] if dwarves else None)
            if miner and not (miner.get("labors") or {}).get("MINE"):
                actions.append(ActionCall("assign_labor", {
                    "dwarf_id": miner["id"], "labor": "MINE",
                    "enabled": True,
                }))
            else:
                actions.append(ActionCall("pass_turn"))
            phase = "excavate_protected_room"

        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Hospital acceptance phase is " + phase + ". A zone is "
                "only the first dependency; furniture, supplies, clinical "
                "occupations, patients, and treatment jobs remain separate."),
            diary=("I treated excavation and the hospital location as "
                   "separate physical claims and did not infer care from a "
                   "zone alone."),
        )


def build() -> HospitalRecoveryGovernor:
    return HospitalRecoveryGovernor()


__all__ = ["HospitalRecoveryGovernor", "build"]
