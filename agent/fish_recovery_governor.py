"""Deterministic fishing-to-prepared-food physical acceptance controller."""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class FishRecoveryGovernor(Governor):
    name = "acceptance:fish-recovery"
    model_id = "control:fish-recovery-v1"

    def __init__(self) -> None:
        self._baseline_prepared_ids: set[int] | None = None

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        stocks = briefing_json.get("stocks") or {}
        operations = briefing_json.get("operations") or {}
        workshops = operations.get("completed_workshops") or {}
        actions: list[ActionCall] = []
        account_actions = [
            action
            for entry in context.get("account") or []
            if entry.get("tag") == "reasoning"
            for action in entry.get("actions") or []
        ]
        receipt = next((
            action for action in reversed(account_actions)
            if action.get("tool") == "prepare_fish"
            and action.get("status") == "applied"
        ), None)
        result = (receipt or {}).get("result") or {}
        preconditions = result.get("preconditions") or {}
        baseline_present = "prepared_fish_item_ids_before" in preconditions
        baseline_ids = set(
            preconditions.get("prepared_fish_item_ids_before") or [])
        current_ids = set(((stocks.get("tracked_item_ids") or {}).get(
            "prepared_fish") or []))
        if self._baseline_prepared_ids is None:
            self._baseline_prepared_ids = set(current_ids)
        run_baseline_ids = self._baseline_prepared_ids
        physically_produced = bool(current_ids - (
            baseline_ids if baseline_present else run_baseline_ids))

        def reserve_worker(required_labor: str) -> None:
            dwarves = [d for d in briefing_json.get("dwarves") or []
                       if d.get("adult")]
            worker = next((d for d in dwarves
                           if str(d.get("profession") or "").lower()
                           == "fisherdwarf"), None)
            eligible = [d for d in dwarves
                        if (d.get("labors") or {}).get(required_labor)]
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

        if int(workshops.get("Fishery") or 0) < 1:
            actions.append(ActionCall("pass_turn"))
        elif physically_produced:
            actions.append(ActionCall("pass_turn"))
        elif receipt is not None:
            reserve_worker("CLEAN_FISH")
        elif int(stocks.get("available_raw_fish") or 0) > 0:
            actions.append(ActionCall("prepare_fish", {"qty": 1}))
            reserve_worker("CLEAN_FISH")
        else:
            # DF normally creates local PrepareRawFish jobs automatically as
            # catches arrive. Maintain the fishing labor and let the observed
            # prepared-item delta prove that native path; the explicit work
            # order is only needed if available raw fish remains unclaimed.
            reserve_worker("FISH")

        if not actions:
            actions.append(ActionCall("pass_turn"))
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Acceptance controller is proving fishing -> raw fish -> "
                "fishery cleaning -> physical prepared fish. "
                f"Physical prepared-fish item-id delta observed: "
                f"{physically_produced}."),
            diary=("I followed the deterministic fish acceptance procedure; "
                   "later item observations determine success."),
        )


def build() -> FishRecoveryGovernor:
    return FishRecoveryGovernor()


__all__ = ["FishRecoveryGovernor", "build"]
