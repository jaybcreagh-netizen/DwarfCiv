"""Deterministic clinical-treatment acceptance controller.

Drives the injured-patient micro-scenario behind
``validate_clinical_treatment``: an `injury`-scenario citizen must be
recovered, diagnosed, and treated by DF's native medical chain. The
controller supplies only the observable dependencies (a verified water
source, an assigned doctor) and otherwise lets the native chain run; the
acceptance evidence is the observer's record of exact medical jobs and the
patient's health-request flags clearing, never this controller's phases.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class TreatmentRecoveryGovernor(Governor):
    name = "acceptance:treatment-recovery"
    model_id = "control:treatment-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        healthcare = briefing_json.get("healthcare") or {}
        water = briefing_json.get("water") or {}
        patients = healthcare.get("patients") or []
        medical_jobs = healthcare.get("medical_jobs") or []
        locations = healthcare.get("locations") or []
        actions: list[ActionCall] = []
        phase = "verify"

        doctor_assigned = any(
            occ.get("type") == "DOCTOR" and occ.get("unit_alive")
            for loc in locations for occ in loc.get("occupations") or [])

        has_water = (
            any(w.get("complete") for w in water.get("wells") or [])
            or any(z.get("active", True)
                   for z in water.get("source_zones") or []))
        fresh_sample = water.get("fresh_access_sample") or []

        if not patients:
            actions.append(ActionCall("pass_turn"))
            if medical_jobs:
                phase = "medical_jobs_outlast_patient_flags"
            else:
                phase = "no_observed_patient"
        elif not locations:
            actions.append(ActionCall("pass_turn"))
            phase = "no_native_hospital_for_treatment"
        elif not doctor_assigned:
            candidates = healthcare.get("doctor_candidates") or []
            if candidates:
                actions.append(ActionCall("assign_hospital_doctor", {
                    "dwarf_id": candidates[0]["id"],
                    "location_id": locations[0]["id"],
                }))
                phase = "assign_doctor_before_treatment"
            else:
                actions.append(ActionCall("pass_turn"))
                phase = "no_eligible_doctor_candidate"
        elif not has_water and fresh_sample:
            tile = fresh_sample[0]
            actions.append(ActionCall("designate_water_source", {
                "x": tile["x"], "y": tile["y"], "z": tile["z"],
            }))
            phase = "designate_verified_water_source"
        else:
            actions.append(ActionCall("pass_turn"))
            if not has_water:
                phase = "patient_waiting_without_visible_fresh_water"
            elif medical_jobs:
                phase = "native_medical_chain_active"
            else:
                phase = "patient_flagged_awaiting_native_jobs"

        patient_note = "; ".join(
            f"patient {p.get('id')} wounds={p.get('wound_count')} "
            f"requests={','.join(p.get('health_requests') or []) or 'none'}"
            for p in patients[:3]) or "no patients observed"
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Treatment acceptance phase is " + phase + ". " + patient_note
                + ". Recovery, diagnosis, individual procedures, and outcome "
                "are separate native events; none is inferred from a zone, "
                "an occupation, or this controller's own phase."),
            diary=("I supplied only observable treatment dependencies and "
                   "let DF's native medical chain produce the evidence."),
        )


def build() -> TreatmentRecoveryGovernor:
    return TreatmentRecoveryGovernor()


__all__ = ["TreatmentRecoveryGovernor", "build"]
