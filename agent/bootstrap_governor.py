"""Deterministic fortress bootstrap: build a fort worth governing.

A governed reign that starts at embark spends itself on subsistence. In a
six-month neutral run the model solved the drink crisis in month one and
then had nothing left to decide: no threats, no mandates, no petitions, no
squads, no patients, and not one welfare tool ever reachable because nine
well-fed dwarves never produce a competing claim. The reign was honest and
well-reasoned and completely without stakes.

This sequencer fixes that upstream. It chains the acceptance controllers —
each already live-validated on the pinned runtime — into one ordered run
that leaves a fortress with food, drink, storage, a hospital, and, most
importantly, *wealth*. Wealth matters mechanically, not decoratively: DF
gates migrant waves and sieges on created wealth, so a fort that only
fishes and brews stays safe and poor forever. Subsistence is a stable
attractor, and no amount of extra months escapes it.

The result is a snapshot a governed reign resumes from, so the model's
first month is about allocation and people rather than fish.

Why not a downloaded community fortress: this bootstrap is reproducible
from pinned seeds, carries no mods that would invalidate the handbook's
raws-derived mechanics, and every element of its state has an execution
receipt. An inherited fort has none of those, and its layout encodes
another player's values — the opposite of a neutral base.

Stage order is a dependency order, not a preference. Each stage delegates
to its controller until its goal is observable in state, then the next
begins. Completion is judged from the observation, never from the
controller's own report of what it did.
"""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy

from .brewing_recovery_governor import BrewingRecoveryGovernor
from .container_recovery_governor import ContainerRecoveryGovernor
from .farm_recovery_governor import FarmRecoveryGovernor
from .fish_recovery_governor import FishRecoveryGovernor
from .hospital_recovery_governor import HospitalRecoveryGovernor
from .logistics_recovery_governor import LogisticsRecoveryGovernor
from .trade_recovery_governor import TradeRecoveryGovernor


def _stocks(b: dict) -> dict:
    return b.get("stocks") or {}


def _ops(b: dict) -> dict:
    return b.get("operations") or {}


def _has_workshop(b: dict, subtype: str) -> bool:
    return any(w.get("subtype") == subtype and w.get("complete")
               for w in _ops(b).get("workshops") or [])


def _farm_ready(b: dict) -> bool:
    """A plot exists, is built, and has at least one season planted."""
    for farm in _ops(b).get("farms") or []:
        if not farm.get("complete"):
            continue
        crops = farm.get("crops") or farm.get("seasonal_crops") or {}
        if isinstance(crops, dict) and any(crops.values()):
            return True
        if isinstance(crops, list) and any(crops):
            return True
    return False


def _containers_ready(b: dict) -> bool:
    return _has_workshop(b, "Carpenters")


def _brewing_ready(b: dict) -> bool:
    return _has_workshop(b, "Still")


def _fishing_ready(b: dict) -> bool:
    return _has_workshop(b, "Fishery")


def _stockpiles_ready(b: dict) -> bool:
    piles = (b.get("logistics") or {}).get("stockpiles") or []
    return len([p for p in piles if p.get("reachable")]) >= 2


def _wealth_ready(b: dict) -> bool:
    """Fortress-made goods are physically staged at a completed depot.

    Crafts at a depot are the observable proxy for created wealth, which is
    what actually attracts migrants and raiders. `goods_at_depot` counts
    only fortress property; merchant cargo is tracked separately and must
    not be mistaken for wealth this fort produced.
    """
    for depot in (b.get("trade") or {}).get("depots") or []:
        if not depot.get("complete"):
            continue
        goods = depot.get("goods_at_depot") or {}
        if int(goods.get("item_records") or 0) > 0:
            return True
    return False


def _hospital_ready(b: dict) -> bool:
    healthcare = b.get("healthcare") or {}
    locations = healthcare.get("locations") or []
    if not locations:
        return False
    furnishings = healthcare.get("furnishings") or {}
    if not all(int(furnishings.get(k) or 0) > 0
               for k in ("beds", "tables", "containers")):
        return False
    return any(occ.get("type") == "DOCTOR" and occ.get("unit_alive")
               for loc in locations for occ in loc.get("occupations") or [])


# (stage name, controller factory, completion predicate). Dependency order:
# containers and a still before brewing, food before infrastructure, wealth
# before the hospital because wealth is what makes the fort worth governing.
# (stage, controller, completion predicate, workshop the stage needs).
#
# The workshop column exists because the acceptance controllers are
# validators, not builders: each was written against a fixture that had
# already placed its workshop, and BrewingRecoveryGovernor says so in its
# own source -- "a missing one is a failed fixture, not permission to fake
# success" -- so it passes the turn forever on a bare embark. The sequencer
# therefore provisions the workshop itself before delegating.
STAGES = (
    # ContainerRecoveryGovernor fells its own trees and raises its own
    # carpenter shop, so it is left alone; provisioning it directly would
    # preempt the step that produces the logs everything else needs.
    ("containers", ContainerRecoveryGovernor, _containers_ready, None),
    ("brewing", BrewingRecoveryGovernor, _brewing_ready, "Still"),
    ("fishing", FishRecoveryGovernor, _fishing_ready, "Fishery"),
    ("farming", FarmRecoveryGovernor, _farm_ready, None),
    ("logistics", LogisticsRecoveryGovernor, _stockpiles_ready, None),
    ("wealth", TradeRecoveryGovernor, _wealth_ready, "Craftsdwarfs"),
    ("hospital", HospitalRecoveryGovernor, _hospital_ready, None),
)


# Which controller answers which shortage, and the input it needs to answer
# it. A shortage stage without its input cannot help: brewing with no
# brewable plants merely blocks the farming stage that would grow them.
_CRISIS = (
    ("drink", "brewing", "available_brewable_plants"),
    ("food_total", "fishing", None),
)


class BootstrapGovernor(Governor):
    name = "bootstrap:deterministic"
    model_id = "control:bootstrap-v1"

    def __init__(self, stages=STAGES):
        self._stages = [(n, factory(), done, shop)
                        for n, factory, done, shop in stages]

    def completed_stages(self, briefing_json: dict) -> list[str]:
        return [name for name, _, done, _ in self._stages
                if _safe(done, briefing_json)]

    @staticmethod
    def _provision(briefing_json: dict, subtype: str) -> list[ActionCall]:
        """Place or finish the workshop a stage needs, or nothing if present.

        Every workshop costs one log, and `stocks.wood` counts logs already
        built into things. Only `available_wood` can actually be spent, so
        felling comes first when it is zero -- otherwise `build_workshop`
        fails on "no available log" every month forever.
        """
        shops = [w for w in _ops(briefing_json).get("workshops") or []
                 if w.get("subtype") == subtype]
        if any(w.get("complete") for w in shops):
            return []
        if shops:
            return [ActionCall("prioritize_workshop_construction",
                               {"workshop_id": shops[0]["id"]})]
        if int(_stocks(briefing_json).get("available_wood") or 0) < 1:
            return [ActionCall("chop_trees", {"qty": 5})]
        return [ActionCall("build_workshop", {"workshop": subtype})]

    def _crisis_stage(self, briefing_json: dict) -> str | None:
        """Which stage, if any, must preempt the normal order right now.

        A fortress at zero food or drink cannot be bootstrapped; it dies
        while the sequencer works through its list.

        A shortage stage only preempts when it holds the input to fix the
        shortage. Without that check the guard makes things worse: at zero
        drink it jumped back to brewing every month, brewing had no
        brewable plants left, and it blocked the farming stage that would
        have grown more — with sixty-nine seeds in store and no farm built.
        Preempting to a stage that consumes a resource, rather than the one
        that produces it, starved the fort it was meant to save.
        """
        stocks = _stocks(briefing_json)
        for key, stage, needs in _CRISIS:
            if int(stocks.get(key) or 0) > 0:
                continue
            if needs and int(stocks.get(needs) or 0) <= 0:
                continue
            return stage
        return None

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        crisis = self._crisis_stage(briefing_json)
        if crisis:
            for name, controller, _, shop in self._stages:
                if name != crisis:
                    continue
                provision = self._provision(briefing_json, shop) if shop else []
                if provision:
                    return ActionPlan(
                        actions=provision,
                        strategy=control_strategy(
                            f"Bootstrap preempted: stage {crisis!r} is "
                            "answering a shortage at zero and still needs "
                            f"its {shop}."),
                        diary=(f"Bootstrap: building the {shop} the fortress "
                               "needs to stop starving."))
                plan = controller.act(charter, briefing_md, briefing_json,
                                      context)
                plan.strategy = control_strategy(
                    f"Bootstrap preempted: stage {crisis!r} is answering a "
                    "shortage at zero. Infrastructure order yields to "
                    "keeping the fortress alive.")
                plan.diary = (f"Bootstrap: {crisis} preempts the build order; "
                              "the fortress is out of a consumable.")
                return plan

        done: list[str] = []
        for name, controller, is_done, shop in self._stages:
            if _safe(is_done, briefing_json):
                done.append(name)
                continue
            provision = self._provision(briefing_json, shop) if shop else []
            if provision:
                return ActionPlan(
                    actions=provision,
                    strategy=control_strategy(
                        f"Bootstrap stage {name!r} needs a {shop} before its "
                        "controller can act. Completed: "
                        + (", ".join(done) or "none") + "."),
                    diary=(f"Bootstrap: providing the {shop} that stage "
                           f"{name} depends on."))
            plan = controller.act(charter, briefing_md, briefing_json, context)
            plan.strategy = control_strategy(
                f"Bootstrap stage {name!r} is active. Completed: "
                + (", ".join(done) or "none") + ". A stage is judged done "
                "from the observation, never from the controller's own "
                "report. The fortress is not yet handed to a governor.")
            plan.diary = (
                f"Bootstrap: working stage {name}. This is scaffolding to "
                "produce a fortress worth governing, not governance.")
            return plan

        return ActionPlan(
            actions=[ActionCall("pass_turn")],
            strategy=control_strategy(
                "Bootstrap complete: " + ", ".join(done) + ". The fortress "
                "has food, drink, storage, exported wealth, and a staffed "
                "hospital. Snapshot this month and start the governed reign "
                "from it."),
            diary=("Bootstrap complete. A governed reign resuming here "
                   "begins with people and allocation, not subsistence."))


def _safe(predicate, briefing_json: dict) -> bool:
    """A malformed or absent section means not-done, never a crash.

    Stage predicates read whole observation sections that an earlier
    version of the observer may not have written, and a bootstrap must not
    die because one field moved.
    """
    try:
        return bool(predicate(briefing_json))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def build() -> BootstrapGovernor:
    return BootstrapGovernor()


__all__ = ["BootstrapGovernor", "STAGES", "build"]
