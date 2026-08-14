"""Dispatch validated governor actions into the harness action layer.

Bridges agent.governor.ActionCall -> harness.actions functions, threading the
welfare recorder into moral/policy tools so each action writes its decision
record (with the contemporaneous rationale) at the moment it fires.
"""

from __future__ import annotations

import logging

from harness.actions import ACTIONS, MORAL_TOOLS
from .schemas import validate_call

log = logging.getLogger("agent.dispatch")


def dispatch(client, call, welfare=None) -> dict:
    """Execute one ActionCall. Returns an outcome record for the account.

    The schema is validated first (re-checking the required rationale on moral
    tools), so a malformed call is rejected before it touches DF. A DF-side
    failure is captured, not raised: one bad action must not sink the month.
    """
    validate_call(call.tool, call.params)
    func = ACTIONS.get(call.tool)
    if func is None:
        raise ValueError(f"no harness action for tool {call.tool!r}")
    kwargs = dict(call.params)
    if call.tool in MORAL_TOOLS:
        kwargs["welfare"] = welfare        # rationale already in params
    outcome = {"tool": call.tool,
               "params": {k: v for k, v in call.params.items()
                          if k != "rationale"},
               "participants": [],
               "rationale": call.params.get("rationale", call.rationale),
               "ok": True, "result": None}
    try:
        outcome["result"] = func(client, **kwargs)
        if isinstance(outcome["result"], dict):
            outcome["status"] = outcome["result"].get("status", "applied")
            # ``ok`` means a verified effect for downstream ground truth.
            # A harmless no-op is preserved as a receipt but not promoted to
            # evidence that the governor changed the world.
            outcome["ok"] = outcome["status"] == "applied"
        else:
            # Legacy actions return a verified command transcript rather than
            # a structured receipt. They still need an explicit status in the
            # account and Phase-3 transcript.
            outcome["status"] = "applied"
        if call.tool == "conscript":
            outcome["participants"] = _unit_names(
                client, call.params.get("units") or [])
    except Exception as e:  # noqa: BLE001 - never let one verb sink the month
        outcome["ok"] = False
        outcome["status"] = "failed"
        outcome["result"] = f"{type(e).__name__}: {e}"
        log.warning("action %s failed: %s", call.tool, e)
    return outcome


def _unit_names(client, unit_ids: list[int]) -> list[str]:
    """Resolve successful unit-targeted actions while DF state is available."""
    names: list[str] = []
    for uid in unit_ids:
        try:
            name = client.lua(
                f"local u=df.unit.find({int(uid)}) "
                "print(u and dfhack.units.getReadableName(u) or '')"
            ).strip()
        except Exception:  # outcome still retains the stable unit id below
            name = ""
        names.append(name or f"unit#{uid}")
    return names
