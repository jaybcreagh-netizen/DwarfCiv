"""The governor: briefing -> reason -> act -> diary, plus yearly probes.

This is the Phase 2 loop's decision side, factored so the model interface is
pluggable. A `Governor` is anything that can, given a charter and a briefing,
return the actions it wants to take (each with a rationale) and narrate; the
harness drives it and owns side effects (dispatch, welfare logging, account
recording). That separation keeps the harness free of any LLM dependency and
lets a `ScriptedGovernor` exercise the whole path under test without a model.

The contract (one month):

    decisions = governor.act(charter, briefing_md, briefing_json, context)
    # -> ActionPlan(actions=[ActionCall(tool, params, rationale), ...],
    #               diary="...")

    answers = governor.answer_probes(charter, questions, context)  # yearly
    # -> ["...", "..."]  (one per question, same order)

`context` carries prior account material so the model can be consistent (or
drift) across the reign.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import validate_call, InvalidActionCall
from .probes import IN_SITU_QUESTIONS


@dataclass
class ActionCall:
    tool: str
    params: dict = field(default_factory=dict)
    rationale: str = ""

    def as_record(self) -> dict:
        return {"tool": self.tool, "params": dict(self.params),
                "rationale": self.rationale}


@dataclass
class ActionPlan:
    actions: list[ActionCall] = field(default_factory=list)
    diary: str = ""


class Governor:
    """Base class. Subclass and implement `act` (+ optionally answer_probes)."""

    name = "governor"

    def act(self, charter, briefing_md: str, briefing_json: dict,
            context: dict) -> ActionPlan:
        raise NotImplementedError

    def answer_probes(self, charter, questions: list[str],
                      context: dict) -> list[str]:
        """Default: a non-committal answer per question. Override for real use."""
        return ["(no answer)" for _ in questions]

    # -- validation shared by all governors ---------------------------------

    @staticmethod
    def validate(plan: ActionPlan) -> ActionPlan:
        """Reject any action that violates its schema (e.g. missing rationale).

        The rationale on a moral tool may be supplied either via the dedicated
        ActionCall.rationale field or inside params; normalize to both so the
        schema check and the dispatcher agree.
        """
        for call in plan.actions:
            if call.rationale and "rationale" not in call.params:
                call.params = {**call.params, "rationale": call.rationale}
            elif call.params.get("rationale") and not call.rationale:
                call.rationale = call.params["rationale"]
            validate_call(call.tool, call.params)
        return plan


class ScriptedGovernor(Governor):
    """A deterministic governor driven by a per-month script — for tests and
    scripted scenarios (the Workstream A acceptance scenario, smoke runs).

    `script` maps month_index -> ActionPlan (or a list of ActionCall, or a
    callable(briefing_json)->ActionPlan). Months with no entry pass the turn.
    `probe_answers` maps year -> list[str]; missing years fall back to a
    constant set so self-report stays measurable.
    """

    name = "scripted"

    def __init__(self, script: dict | None = None,
                 probe_answers: dict | None = None,
                 default_probe: list[str] | None = None):
        self.script = script or {}
        self.probe_answers = probe_answers or {}
        self.default_probe = default_probe

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        entry = self.script.get(context.get("month_index"))
        if entry is None:
            return ActionPlan(actions=[ActionCall("pass_turn")], diary="")
        if callable(entry):
            entry = entry(briefing_json)
        if isinstance(entry, ActionPlan):
            return entry
        if isinstance(entry, list):
            return ActionPlan(actions=entry)
        raise TypeError(f"bad script entry for month {context.get('month_index')}")

    def answer_probes(self, charter, questions, context) -> list[str]:
        year = context.get("year", 0)
        if year in self.probe_answers:
            return self.probe_answers[year]
        if self.default_probe is not None:
            return self.default_probe
        return [f"(year {year}) steady as set out in the charter."
                for _ in questions]


__all__ = ["Governor", "ScriptedGovernor", "ActionCall", "ActionPlan",
           "IN_SITU_QUESTIONS", "InvalidActionCall"]
