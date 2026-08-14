"""Deterministic typed-stockpile and reachability acceptance controller."""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class LogisticsRecoveryGovernor(Governor):
    name = "acceptance:logistics-recovery"
    model_id = "control:logistics-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        logistics = briefing_json.get("logistics") or {}
        piles = logistics.get("stockpiles") or []
        names = {str(p.get("name") or "") for p in piles}
        workshops = (briefing_json.get("operations") or {}).get(
            "workshops") or []
        still = next((w for w in workshops
                      if w.get("subtype") == "Still"), None)
        actions: list[ActionCall] = []
        if not any(name.startswith("DwarfCiv booze") for name in names):
            params = {"kind": "booze", "width": 3, "height": 3}
            if still:
                params["near_building_id"] = still["id"]
            actions.append(ActionCall("build_stockpile", params))
        elif not any(name.startswith("DwarfCiv refuse") for name in names):
            actions.append(ActionCall("build_stockpile", {
                "kind": "refuse", "width": 3, "height": 3}))
        else:
            actions.append(ActionCall("pass_turn"))
        reachable = all(p.get("reachable") for p in piles
                        if str(p.get("name") or "").startswith("DwarfCiv"))
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Acceptance controller is creating a booze pile near the "
                "still and an outside refuse pile, then checking native "
                f"reachability. Existing DwarfCiv piles reachable: {reachable}."),
            diary=("I followed the deterministic logistics acceptance "
                   "procedure; later hauling and reachability observations "
                   "determine operational success."),
        )


def build() -> LogisticsRecoveryGovernor:
    return LogisticsRecoveryGovernor()


__all__ = ["LogisticsRecoveryGovernor", "build"]
