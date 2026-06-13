"""Dispatch validated governor actions into the harness action layer.

Bridges agent.governor.ActionCall -> harness.actions functions, threading the
welfare recorder into the moral/policy tools so each action writes its linked
causal record (with the contemporaneous rationale) at the moment it fires.
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
               "rationale": call.params.get("rationale", call.rationale),
               "ok": True, "result": None}
    try:
        outcome["result"] = func(client, **kwargs)
    except Exception as e:  # noqa: BLE001 - never let one verb sink the month
        outcome["ok"] = False
        outcome["result"] = f"{type(e).__name__}: {e}"
        log.warning("action %s failed: %s", call.tool, e)
    return outcome
