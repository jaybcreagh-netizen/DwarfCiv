"""Persistent operational strategy, separate from the governor's diary.

The diary is evidence about what the governor says. This ledger is a typed
handoff artifact for what it is trying to achieve across months. Proposed
projects are stored alongside receipts so intent is never confused with
verified effect.
"""

from __future__ import annotations

import json
from pathlib import Path


STRATEGY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessment": {"type": "string", "minLength": 1,
                       "maxLength": 1600},
        "objectives": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1,
                           "maxLength": 80},
                    "goal": {"type": "string", "minLength": 1,
                             "maxLength": 400},
                    "horizon": {"type": "string", "enum": [
                        "immediate", "tactical", "seasonal", "strategic"]},
                    "priority": {"type": "integer", "minimum": 1,
                                 "maximum": 5},
                    "success_criteria": {
                        "type": "array", "maxItems": 6,
                        "items": {"type": "string", "maxLength": 300},
                    },
                },
                "required": ["id", "goal", "horizon", "priority",
                             "success_criteria"],
            },
        },
        "projects": {
            "type": "array", "maxItems": 10,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1,
                           "maxLength": 80},
                    "objective_id": {"type": "string", "minLength": 1,
                                     "maxLength": 80},
                    "status": {"type": "string", "enum": [
                        "planned", "active", "blocked", "completed",
                        "abandoned"]},
                    "next_step": {"type": "string", "minLength": 1,
                                  "maxLength": 400},
                    "evidence": {
                        "type": "array", "maxItems": 8,
                        "items": {"type": "string", "maxLength": 300},
                    },
                    "blockers": {
                        "type": "array", "maxItems": 8,
                        "items": {"type": "string", "maxLength": 300},
                    },
                    "fallback": {"type": "string", "maxLength": 400},
                },
                "required": ["id", "objective_id", "status", "next_step",
                             "evidence", "blockers", "fallback"],
            },
        },
        "contingencies": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "trigger": {"type": "string", "minLength": 1,
                                "maxLength": 300},
                    "response": {"type": "string", "minLength": 1,
                                 "maxLength": 400},
                },
                "required": ["trigger", "response"],
            },
        },
    },
    "required": ["assessment", "objectives", "projects", "contingencies"],
}


def control_strategy(assessment: str = "No operational strategy proposed.") -> dict:
    return {"assessment": assessment, "objectives": [], "projects": [],
            "contingencies": []}


class StrategyRecord:
    """Append-only strategy proposals plus the receipts that followed them."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "strategy.jsonl"
        self.current_path = self.run_dir / "strategy-current.json"
        self.markdown_path = self.run_dir / "strategy.md"
        self.entries: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(json.loads(line))

    @property
    def latest(self) -> dict | None:
        return self.entries[-1] if self.entries else None

    def record(self, month_index: int, date: dict | None, strategy: dict,
               outcomes: list[dict], post_action_survival: dict) -> dict:
        entry = {
            "month_index": month_index,
            "date": date,
            "strategy": strategy,
            "execution_receipts": outcomes,
            "post_action_survival": post_action_survival,
        }
        self.entries.append(entry)
        self._flush()
        return entry

    def truncate(self, count: int) -> None:
        self.entries = self.entries[:count]
        self._flush()

    def _flush(self) -> None:
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n"
                       for e in self.entries)
        self.path.write_text(text, encoding="utf-8")
        if self.entries:
            self.current_path.write_text(
                json.dumps(self.entries[-1], indent=2, ensure_ascii=False),
                encoding="utf-8")
        elif self.current_path.exists():
            self.current_path.unlink()
        self.markdown_path.write_text(self._render_markdown(), encoding="utf-8")

    def _render_markdown(self) -> str:
        lines = ["# Fortress operational strategy", ""]
        for entry in self.entries:
            strategy = entry.get("strategy") or {}
            lines.append(f"## Month {entry.get('month_index')}")
            lines.append("")
            lines.append(strategy.get("assessment") or "(no assessment)")
            lines.append("")
            for objective in strategy.get("objectives") or []:
                lines.append(
                    f"- **{objective.get('id')}** "
                    f"[{objective.get('horizon')}, p{objective.get('priority')}]: "
                    f"{objective.get('goal')}")
            for project in strategy.get("projects") or []:
                lines.append(
                    f"  - `{project.get('id')}` {project.get('status')}: "
                    f"{project.get('next_step')}")
            outcomes = entry.get("execution_receipts") or []
            if outcomes:
                statuses = ", ".join(
                    f"{o.get('tool')}={o.get('status', 'unknown')}"
                    for o in outcomes)
                lines.append(f"- Receipts: {statuses}")
            lines.append("")
        return "\n".join(lines)


__all__ = ["STRATEGY_SCHEMA", "StrategyRecord", "control_strategy"]
