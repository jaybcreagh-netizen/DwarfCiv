"""Deterministic clean-water acceptance controller.

Drives ``establish_clean_water``: mason and mechanic workshops, then the
exact block, mechanism, and bucket a well consumes, then a channeled shaft
beside verified fresh water and the well itself.

The controller stops rather than improvises when the map does not offer the
resource. If no visible fresh water exists it takes no action and records
that fact: digging blind for a hidden aquifer would both reveal geology the
governor is not entitled to see and manufacture a water source the map did
not provide. A completed component chain is component evidence only; it is
never reported as a water supply.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


# The exact physical components a native well consumes, in the order the
# controller produces them, paired with the workshop each one requires.
_COMPONENTS = (
    ("block", "blocks", "Masons", "ConstructBlocks"),
    ("mechanism", "mechanisms", "Mechanics", "ConstructMechanisms"),
    ("bucket", "buckets", "Carpenters", "MakeBucket"),
)


class WaterRecoveryGovernor(Governor):
    name = "acceptance:water-recovery"
    model_id = "control:water-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        water = briefing_json.get("water") or {}
        operations = briefing_json.get("operations") or {}
        workshops = operations.get("workshops") or (
            (briefing_json.get("logistics") or {}).get("workshops") or [])
        components = water.get("components") or {}
        wells = water.get("wells") or []
        pending_jobs = {str(o.get("job"))
                        for o in operations.get("manager_orders") or []
                        if int(o.get("amount_left") or 0) > 0}
        actions: list[ActionCall] = []
        phase = "verify"

        if any(w.get("complete") for w in wells):
            actions.append(ActionCall("pass_turn"))
            phase = "well_complete_awaiting_use_evidence"
            return self._plan(actions, phase, water)
        if wells:
            actions.append(ActionCall("pass_turn"))
            phase = "well_under_construction"
            return self._plan(actions, phase, water)

        # Produce each missing component at its own verified workshop.
        for kind, stock_key, subtype, job in _COMPONENTS:
            if components.get(stock_key):
                continue
            shop = next((w for w in workshops
                         if w.get("subtype") == subtype), None)
            if not shop:
                actions.append(ActionCall("build_workshop",
                                          {"workshop": subtype}))
                phase = f"designate_{subtype.lower()}_workshop"
            elif not shop.get("complete"):
                actions.append(ActionCall("prioritize_workshop_construction",
                                          {"workshop_id": shop["id"]}))
                phase = f"complete_{subtype.lower()}_workshop"
            elif job in pending_jobs:
                # The order already exists. Re-queuing it every month would
                # stack duplicate orders and hide the real question, which
                # is why the outstanding one has produced no item yet.
                actions.append(ActionCall("pass_turn"))
                phase = f"awaiting_{kind}_from_pending_order"
            else:
                actions.append(ActionCall("make_well_components",
                                          {"kind": kind, "qty": 1}))
                phase = f"queue_{kind}_order"
            return self._plan(actions, phase, water)

        # Every component exists. Siting is now the only open question, and
        # it is answered by the map, not by this controller.
        sample = water.get("fresh_access_sample") or []
        if sample:
            tile = sample[0]
            adjacent = tile.get("adjacent") or {}
            actions.append(ActionCall("prepare_well_site", {
                "x": adjacent.get("x", tile["x"]),
                "y": adjacent.get("y", tile["y"]),
                "z": adjacent.get("z", tile["z"]),
            }))
            phase = "channel_shaft_beside_verified_fresh_water"
            return self._plan(actions, phase, water)

        # No fresh water is visible anywhere. The only remaining supply is
        # stagnant, which is a real fallback with a real cost, so it is
        # taken explicitly and never relabelled as clean.
        stagnant = water.get("stagnant_access_sample") or []
        if stagnant and not (water.get("source_zones") or []):
            tile = stagnant[0]
            actions.append(ActionCall("designate_water_source", {
                "x": tile["x"], "y": tile["y"], "z": tile["z"],
                "allow_stagnant": True,
            }))
            phase = "stagnant_fallback_zone_infection_risk_accepted"
        else:
            actions.append(ActionCall("pass_turn"))
            tiles = water.get("visible_tiles") or {}
            phase = ("components_ready_but_no_visible_fresh_water"
                     f"_stagnant={tiles.get('stagnant', 0)}"
                     f"_salt={tiles.get('salt', 0)}")
        return self._plan(actions, phase, water)

    @staticmethod
    def _plan(actions, phase, water) -> ActionPlan:
        tiles = water.get("visible_tiles") or {}
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Water acceptance phase is " + phase + ". Visible water is "
                f"fresh={tiles.get('fresh', 0)} salt={tiles.get('salt', 0)} "
                f"stagnant={tiles.get('stagnant', 0)}. Components, a shaft, "
                "a completed well, and citizens actually drawing water are "
                "separate claims; stagnant water is not clean water."),
            diary=("I built only the well components the map's own visible "
                   "water could justify and did not dig blind for hidden "
                   "water."),
        )


def build() -> WaterRecoveryGovernor:
    return WaterRecoveryGovernor()


__all__ = ["WaterRecoveryGovernor", "build"]
