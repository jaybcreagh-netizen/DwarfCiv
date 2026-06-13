"""Context assembly — the steward's memory.

The steward's memory is reconstructed each turn from stored artifacts, not
carried as one ever-growing native conversation. Three things go in (and
*only* these three — never `ledger.jsonl` or Legends data, see README
"Ground-truth isolation"):

  1. the Markdown briefings the agent has seen (its lossy perception),
  2. the agent's own past reasoning and the orders it issued each month,
  3. its seasonal diary entries.

`MemoryPolicy` is deliberately a swappable component: what the agent
remembers shapes the society it builds, so it's a first-class variable, not
hardcoded in the steward. `FullHistoryMemory` (the default) replays
everything verbatim — fine for a one-year run (≈12 briefings of ~1–2k tokens
plus diaries fits comfortably in a modern context window). It will NOT scale
to multi-year runs; a later phase will need a summarizing policy that
compacts older turns (a Phase 5+ concern). That future work changes only
this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .client import Message


# -- the record --------------------------------------------------------------


@dataclass
class MemoryStore:
    """Append-only record of the agent's perception and its own outputs.

    Persisted to runs/<id>/memory/memory.jsonl as events accrue, so a run's
    memory is inspectable and a future policy could reconstruct from it.
    """
    path: Path | None = None
    events: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, event: dict) -> None:
        self.events.append(event)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def record_briefing(self, month: int, date: str, markdown: str) -> None:
        """Record the briefing the agent saw. `markdown` is the curated
        perception — NOT ledger/ground-truth content."""
        self._append({"kind": "briefing", "month": month, "date": date,
                      "markdown": markdown})

    def record_turn(self, month: int, reasoning: str, actions: list[dict]) -> None:
        self._append({"kind": "turn", "month": month, "reasoning": reasoning,
                      "actions": actions})

    def record_diary(self, month: int, season: str, date: str, text: str) -> None:
        self._append({"kind": "diary", "month": month, "season": season,
                      "date": date, "text": text})


# -- policy ------------------------------------------------------------------


class MemoryPolicy:
    """Turns a MemoryStore into the message list sent to the model."""

    def build_messages(self, store: MemoryStore, final_instruction: str) -> list[Message]:
        raise NotImplementedError


class FullHistoryMemory(MemoryPolicy):
    """Replay every briefing, turn, and diary in order, then append the
    final instruction (governance ask, or diary prompt) as a user message.

    The most recent briefing in the log is the agent's current perception;
    the instruction tells it what to do with it. No briefing is duplicated.
    """

    name = "full_history"

    def build_messages(self, store: MemoryStore, final_instruction: str) -> list[Message]:
        messages: list[Message] = []
        for e in store.events:
            kind = e["kind"]
            if kind == "briefing":
                messages.append(Message.user(
                    f"=== Briefing (month {e['month']}, {e.get('date','')}) ===\n\n"
                    f"{e['markdown']}"))
            elif kind == "turn":
                messages.append(Message(role="assistant",
                                        text=_render_turn(e)))
            elif kind == "diary":
                messages.append(Message(role="assistant",
                                        text=f"[Diary — end of {e['season']}, "
                                             f"{e.get('date','')}]\n\n{e['text']}"))
        messages.append(Message.user(final_instruction))
        return messages


def _render_turn(turn: dict) -> str:
    """Render a completed governance turn: the agent's reasoning + the orders
    it issued and how they resolved (the rolling action record)."""
    parts = []
    if turn.get("reasoning"):
        parts.append(turn["reasoning"].strip())
    actions = turn.get("actions") or []
    if actions:
        lines = ["Orders I issued this month:"]
        for a in actions:
            args = a.get("arguments") or {}
            arg_s = ", ".join(f"{k}={v}" for k, v in args.items())
            status = "ok" if a.get("ok") else "FAILED"
            result = (a.get("result") or "").strip().replace("\n", " ")
            if len(result) > 160:
                result = result[:160] + "…"
            lines.append(f"  - {a['name']}({arg_s}) → {status}: {result}")
        parts.append("\n".join(lines))
    else:
        parts.append("(I issued no orders this month.)")
    return "\n\n".join(parts)


def make_policy(name: str = "full_history") -> MemoryPolicy:
    if name == "full_history":
        return FullHistoryMemory()
    raise ValueError(f"unknown memory policy: {name!r}")
