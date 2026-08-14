"""Deterministic carpenter-and-barrel acceptance controller.

This proves the physical workshop -> construction labor -> manager order ->
container stock chain. It is a mechanism test, not a research governor.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class ContainerRecoveryGovernor(Governor):
    name = "acceptance:container-recovery"
    model_id = "control:container-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        operations = briefing_json.get("operations") or {}
        workshops = [w for w in operations.get("workshops") or []
                     if w.get("subtype") == "Carpenters"]
        actions: list[ActionCall] = []
        account_actions = [
            action
            for entry in context.get("account") or []
            if entry.get("tag") == "reasoning"
            for action in entry.get("actions") or []
        ]
        barrel_receipt = next((
            action for action in reversed(account_actions)
            if action.get("tool") == "make_barrels"
            and action.get("status") == "applied"
        ), None)
        receipt_result = (barrel_receipt or {}).get("result") or {}
        receipt_preconditions = receipt_result.get("preconditions") or {}
        baseline_present = "barrel_item_ids_before" in receipt_preconditions
        baseline_ids = set(receipt_preconditions.get(
            "barrel_item_ids_before") or [])
        current_ids = set((((briefing_json.get("stocks") or {}).get(
            "tracked_item_ids") or {}).get("barrels") or []))
        physically_produced = bool(
            baseline_present and current_ids - baseline_ids)

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
            for labor in ("MINE", "PLANT", "FISH", "CLEAN_FISH", "HERBALIST",
                          "BREWER", "CUTWOOD", "CARPENTER"):
                if labor != required_labor and labors.get(labor):
                    actions.append(ActionCall("assign_labor", {
                        "dwarf_id": worker["id"], "labor": labor,
                        "enabled": False}))

        if not workshops:
            available_wood = int((briefing_json.get("stocks") or {}).get(
                "available_wood") or 0)
            cutting_started = any(a.get("tool") == "chop_trees"
                                  and a.get("status") == "applied"
                                  for a in account_actions)
            if available_wood > 0:
                actions.append(ActionCall(
                    "build_workshop", {"workshop": "Carpenters"}))
            elif not cutting_started:
                actions.append(ActionCall("chop_trees", {"qty": 2}))
                reserve_worker("CUTWOOD", "Woodworker")
            else:
                reserve_worker("CUTWOOD", "Woodworker")
        else:
            shop = workshops[0]
            if not shop.get("complete"):
                job = shop.get("construction_job") or {}
                if not job.get("high_priority"):
                    actions.append(ActionCall(
                        "prioritize_workshop_construction",
                        {"workshop_id": shop["id"]}))
                reserve_worker("CARPENTER", "Woodworker")
            else:
                if barrel_receipt is None:
                    actions.append(ActionCall("make_barrels", {"qty": 1}))
                    reserve_worker("CARPENTER", "Woodworker")
                elif not physically_produced:
                    # A manager-order receipt proves only that the request was
                    # queued. Keep the production labor available until a new
                    # physical barrel id appears in a later observation.
                    reserve_worker("CARPENTER", "Woodworker")

        if not actions:
            actions.append(ActionCall("pass_turn"))
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Acceptance controller is establishing carpentry and "
                "verifying one additional physical food-storage container. "
                f"Physical item-id delta observed: {physically_produced}."),
            diary=("I followed the deterministic container acceptance "
                   "procedure; later stock observations determine success."),
        )


def build() -> ContainerRecoveryGovernor:
    return ContainerRecoveryGovernor()


__all__ = ["ContainerRecoveryGovernor", "build"]
