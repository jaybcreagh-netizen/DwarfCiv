"""Deterministic still-to-drink physical acceptance controller."""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class BrewingRecoveryGovernor(Governor):
    name = "acceptance:brewing-recovery"
    model_id = "control:brewing-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        operations = briefing_json.get("operations") or {}
        workshops = operations.get("completed_workshops") or {}
        actions: list[ActionCall] = []
        account_actions = [
            action
            for entry in context.get("account") or []
            if entry.get("tag") == "reasoning"
            for action in entry.get("actions") or []
        ]
        brew_receipt = next((
            action for action in reversed(account_actions)
            if action.get("tool") == "brew_drinks"
            and action.get("status") == "applied"
        ), None)
        result = (brew_receipt or {}).get("result") or {}
        preconditions = result.get("preconditions") or {}
        baseline_present = "drink_item_ids_before" in preconditions
        baseline_ids = set(preconditions.get("drink_item_ids_before") or [])
        current_ids = set((((briefing_json.get("stocks") or {}).get(
            "tracked_item_ids") or {}).get("drinks") or []))
        physically_produced = bool(
            baseline_present and current_ids - baseline_ids)

        def reserve_brewer() -> None:
            dwarves = [d for d in briefing_json.get("dwarves") or []
                       if d.get("adult")]
            worker = next((d for d in dwarves
                           if str(d.get("profession") or "").lower()
                           == "planter"), None)
            eligible = [d for d in dwarves
                        if (d.get("labors") or {}).get("BREWER")]
            worker = worker or (eligible[0] if eligible else None)
            worker = worker or (dwarves[0] if dwarves else None)
            if not worker:
                return
            labors = worker.get("labors") or {}
            if not labors.get("BREWER"):
                actions.append(ActionCall("assign_labor", {
                    "dwarf_id": worker["id"], "labor": "BREWER",
                    "enabled": True}))
            for labor in ("MINE", "PLANT", "FISH", "CLEAN_FISH", "HERBALIST",
                          "CUTWOOD", "CARPENTER"):
                if labors.get(labor):
                    actions.append(ActionCall("assign_labor", {
                        "dwarf_id": worker["id"], "labor": labor,
                        "enabled": False}))

        if int(workshops.get("Still") or 0) < 1:
            # The scarcity acceptance scenario promises a completed still. A
            # missing one is a failed fixture, not permission to fake success.
            actions.append(ActionCall("pass_turn"))
        elif brew_receipt is None:
            actions.append(ActionCall("brew_drinks", {"qty": 1}))
            reserve_brewer()
        elif not physically_produced:
            reserve_brewer()

        if not actions:
            actions.append(ActionCall("pass_turn"))
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Acceptance controller is proving the still -> brew labor -> "
                "plant/container consumption -> physical drink chain. "
                f"Physical drink item-id delta observed: {physically_produced}."),
            diary=("I followed the deterministic brewing acceptance procedure; "
                   "later item observations determine success."),
        )


def build() -> BrewingRecoveryGovernor:
    return BrewingRecoveryGovernor()


__all__ = ["BrewingRecoveryGovernor", "build"]
