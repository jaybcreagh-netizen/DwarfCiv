"""Deterministic scarcity-recovery governor used as a live acceptance test.

This is not a substitute for the LLM experiment. It exercises the exact same
briefing, schemas, dispatcher, receipts, diary artifacts, snapshots, and tick
loop with conservative state-based decisions before a paid model run.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class RecoveryGovernor(Governor):
    name = "acceptance:scarcity-recovery"
    model_id = "control:scarcity-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        stocks = briefing_json.get("stocks") or {}
        operations = briefing_json.get("operations") or {}
        orders = operations.get("manager_orders") or []
        pending_brew = sum(
            int(o.get("amount_left") or 0) for o in orders
            if o.get("job") == "BREW_DRINK_FROM_PLANT")
        pending_fish = sum(
            int(o.get("amount_left") or 0) for o in orders
            if o.get("job") == "PrepareRawFish")
        workshops = operations.get("completed_workshops") or {}

        actions: list[ActionCall] = []
        for order in orders:
            job = order.get("job")
            impossible = (
                job == "BREW_DRINK_FROM_PLANT"
                and int(stocks.get("brewable_plants") or 0) <= 0
            ) or (
                job == "PrepareRawFish"
                and int(stocks.get("raw_fish") or 0) <= 0
            )
            if impossible and isinstance(order.get("id"), int):
                actions.append(ActionCall(
                    "cancel_workorder", {"order_id": order["id"]}))
        if int(stocks.get("food_total") or 0) < 21:
            actions.append(ActionCall("gather_plants", {"qty": 30}))
        if (int(stocks.get("food_total") or 0) < 21
                and int(stocks.get("raw_fish") or 0) > 0
                and int(workshops.get("Fishery") or 0) > 0
                and pending_fish < 2):
            actions.append(ActionCall("prepare_fish", {"qty": 5}))
        if int(stocks.get("wood") or 0) < 10:
            actions.append(ActionCall("chop_trees", {"qty": 5}))
        if (int(stocks.get("drink") or 0) < 21
                and int(stocks.get("plants") or 0) > 0
                and pending_brew < 2):
            actions.append(ActionCall("brew_drinks", {"qty": 5}))
        if not actions:
            actions.append(ActionCall("pass_turn"))

        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Deterministic acceptance controller is restoring observed "
                "food, drink, and wood floors."),
            diary=("I followed the acceptance recovery rule from the current "
                   "briefing. The execution receipts and next briefing, not "
                   "this proposal, determine which effects actually occurred."),
        )


def build() -> RecoveryGovernor:
    return RecoveryGovernor()


__all__ = ["RecoveryGovernor", "build"]
