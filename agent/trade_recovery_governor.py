"""Deterministic trade-depot construction and access acceptance controller."""

from __future__ import annotations

from .governor import ActionCall, ActionPlan, Governor
from .strategy import control_strategy


class TradeRecoveryGovernor(Governor):
    name = "acceptance:trade-recovery"
    model_id = "control:trade-recovery-v1"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        trade = briefing_json.get("trade") or {}
        depots = trade.get("depots") or []
        stocks = briefing_json.get("stocks") or {}
        operations = briefing_json.get("operations") or {}
        workshops = [w for w in operations.get("workshops") or []
                     if w.get("subtype") == "Craftsdwarfs"]
        account_actions = [
            action
            for entry in context.get("account") or []
            if entry.get("tag") == "reasoning"
            for action in entry.get("actions") or []
        ]
        goods_receipt = next((a for a in reversed(account_actions)
                              if a.get("tool") == "make_trade_goods"
                              and a.get("status") == "applied"), None)
        pending_crafts = next((o for o in
                               operations.get("manager_orders") or []
                               if o.get("job") == "MakeCrafts"
                               and (o.get("amount_left") or 0) > 0), None)
        candidates = [item for item in
                      trade.get("safe_export_candidates") or []
                      if item.get("eligible")]
        import_candidates = trade.get("survival_import_candidates") or []
        active_caravans = [car for car in trade.get("caravans") or []
                           if car.get("active")]
        actions: list[ActionCall] = []
        phase = "verify"

        def reserve_woodcrafter() -> None:
            adults = [d for d in briefing_json.get("dwarves") or []
                      if d.get("adult")]
            worker = next((d for d in adults
                           if "crafter" in str(
                               d.get("profession") or "").lower()), None)
            worker = worker or next((d for d in adults
                                     if (d.get("labors") or {}).get(
                                         "WOOD_CRAFT")), None)
            worker = worker or (adults[0] if adults else None)
            if worker and not (worker.get("labors") or {}).get("WOOD_CRAFT"):
                actions.append(ActionCall("assign_labor", {
                    "dwarf_id": worker["id"], "labor": "WOOD_CRAFT",
                    "enabled": True,
                }))

        if depots and depots[0].get("complete") and active_caravans:
            trader_job = depots[0].get("trader_job") or {}
            days_left = min(int(car.get("days_remaining") or 0)
                            for car in active_caravans)
            deadline_fallback = (days_left <= 35
                                 or int(stocks.get("drink") or 0) <= 0)
            if not trade.get("broker"):
                adults = [d for d in briefing_json.get("dwarves") or []
                          if d.get("adult")]
                broker = next((d for d in adults
                               if "expedition leader" in str(
                                   d.get("profession") or "").lower()),
                              adults[0] if adults else None)
                if broker:
                    actions.append(ActionCall(
                        "assign_broker", {"dwarf_id": broker["id"]}))
            if not depots[0].get("trader_requested"):
                actions.append(ActionCall(
                    "request_trader", {
                        "depot_id": depots[0]["id"],
                        "mode": "anyone" if deadline_fallback else "broker",
                    }))
            elif (trader_job and not trader_job.get("worker")
                  and depots[0].get("trader_request_mode") == "broker"
                  and deadline_fallback):
                actions.append(ActionCall(
                    "request_trader", {"depot_id": depots[0]["id"],
                                       "mode": "anyone"}))
            elif (trader_job and not trader_job.get("worker")
                  and not trader_job.get("high_priority")):
                actions.append(ActionCall(
                    "prioritize_trader_job", {"depot_id": depots[0]["id"]}))
        if not depots:
            if (stocks.get("available_wood") or 0) >= 3:
                actions.append(ActionCall("build_trade_depot"))
                phase = "designate"
            else:
                actions.append(ActionCall("chop_trees", {"qty": 3}))
                cutter = next((d for d in briefing_json.get("dwarves") or []
                               if d.get("adult") and not
                               (d.get("labors") or {}).get("CUTWOOD")), None)
                if cutter:
                    actions.append(ActionCall("assign_labor", {
                        "dwarf_id": cutter["id"], "labor": "CUTWOOD",
                        "enabled": True,
                    }))
                phase = "materials"
        else:
            incomplete = next((d for d in depots if not d.get("complete")),
                              None)
            if incomplete:
                job = incomplete.get("construction_job") or {}
                if not job.get("high_priority"):
                    actions.append(ActionCall(
                        "prioritize_trade_depot_construction",
                        {"depot_id": incomplete["id"]}))
                else:
                    actions.append(ActionCall("pass_turn"))
                phase = "construct"
            else:
                if not workshops:
                    actions.append(ActionCall(
                        "build_workshop", {"workshop": "Craftsdwarfs"}))
                    phase = "export_workshop"
                else:
                    incomplete_shop = next((w for w in workshops
                                            if not w.get("complete")), None)
                    if incomplete_shop:
                        job = incomplete_shop.get("construction_job") or {}
                        if not job.get("high_priority"):
                            actions.append(ActionCall(
                                "prioritize_workshop_construction",
                                {"workshop_id": incomplete_shop["id"]}))
                        reserve_woodcrafter()
                        phase = "export_workshop_construction"
                    elif not (operations.get("manager") or {}).get("assigned"):
                        adults = [d for d in
                                  briefing_json.get("dwarves") or []
                                  if d.get("adult")]
                        manager = next((d for d in adults
                                        if "expedition leader" in str(
                                            d.get("profession") or "").lower()),
                                       adults[0] if adults else None)
                        if manager:
                            actions.append(ActionCall(
                                "assign_manager", {"dwarf_id": manager["id"]}))
                        phase = "production_management"
                    elif not goods_receipt and not pending_crafts \
                            and not candidates:
                        actions.append(ActionCall(
                            "make_trade_goods", {"qty": 2}))
                        reserve_woodcrafter()
                        phase = "export_production"
                    elif not candidates:
                        reserve_woodcrafter()
                        phase = "verify_export_output"
                    else:
                        unmarked = [item for item in candidates
                                    if not item.get("at_depot")
                                    and not item.get("haul")]
                        if unmarked and active_caravans:
                            actions.append(ActionCall(
                                "mark_goods_for_trade", {
                                    "depot_id": depots[0]["id"],
                                    "item_ids": [item["id"]
                                                 for item in unmarked[:5]],
                                }))
                            phase = "export_hauling_designated"
                        elif unmarked:
                            actions.append(ActionCall("pass_turn"))
                            phase = "wait_for_caravan"
                        elif all(item.get("at_depot")
                                 for item in candidates):
                            depot = depots[0]
                            trader_job = depot.get("trader_job") or {}
                            at_depot_caravan = any(
                                car.get("trade_state") == "AtDepot"
                                for car in active_caravans)
                            preferred_import = next((item for item in
                                import_candidates
                                if item.get("survival_role") == "drink"),
                                None)
                            preferred_import = preferred_import or next((
                                item for item in import_candidates
                                if item.get("survival_role") == "food"),
                                None)
                            if (trader_job.get("worker")
                                    and at_depot_caravan
                                    and preferred_import):
                                actions.append(ActionCall("execute_trade", {
                                    "depot_id": depot["id"],
                                    "export_item_ids": [
                                        item["id"] for item in candidates[:5]],
                                    "import_item_ids": [
                                        preferred_import["id"]],
                                }))
                                phase = "itemized_exchange"
                            else:
                                actions.append(ActionCall("pass_turn"))
                                phase = "export_hauled"
                        else:
                            actions.append(ActionCall("pass_turn"))
                            phase = "verify_export_hauling"
        if not actions:
            actions.append(ActionCall("pass_turn"))
        elif len(actions) > 1:
            actions = [action for action in actions
                       if action.tool != "pass_turn"]
        return ActionPlan(
            actions=actions,
            strategy=control_strategy(
                "Trade acceptance phase is " + phase + ". A depot is not "
                "ready until its native job completes and the observer "
                "confirms citizen and wagon access."),
            diary=("I followed the deterministic depot dependency chain; "
                   "the designation, completion, and pathability receipts "
                   "remain separate claims."),
        )


def build() -> TradeRecoveryGovernor:
    return TradeRecoveryGovernor()


__all__ = ["TradeRecoveryGovernor", "build"]
