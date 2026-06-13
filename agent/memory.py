"""Context assembly — what the steward remembers, and how it is fed back.

What the steward remembers is itself a variable that shapes the society it
builds, so the memory policy is an explicit, swappable component rather than
logic hardcoded into the steward loop. Swap policies by passing a different
`MemoryPolicy` subclass to the steward.

The default `RollingMemory` reconstructs the context fresh each turn from:
  * the full history of briefings the steward has seen (verbatim),
  * the steward's own diary entries (verbatim), and
  * a rolling, plain-text summary of the orders it has issued.

GROUND-TRUTH ISOLATION (inviolable — see README): nothing here ever draws on
`ledger.jsonl` or Legends data. The briefing is the steward's lossy perception
of reality; the ledger is reality. The steward's diary entries are fed back as
memory verbatim and are NEVER corrected against ground truth — if the steward
confabulates, it comes to rely on its own confabulation. That feedback loop is
deliberate.

NOTE ON SCALE: feeding full briefing history is fine for a one-year run (~12
briefings at ~1-2k tokens each, plus a handful of diary entries — comfortably
within a modern context window). It will NOT scale to multi-year runs and will
need summarization/compaction later (a Phase 5+ concern). Because the policy is
isolated here, that change won't touch the steward loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TurnRecord:
    """One past month's orders, condensed for the rolling action summary."""
    month: int
    label: str                       # in-game date, e.g. "Granite, year 126"
    reasoning: str
    actions: list[str]               # one line per order + its result


@dataclass
class MemoryPolicy:
    """Base class. Holds the chronicle and builds the per-turn message list.

    Subclasses decide *how much* of the history to feed back (build_messages)
    while the recording surface (record_*) stays the same.
    """
    charter: str
    briefings: list[tuple[str, str]] = field(default_factory=list)   # (label, md)
    diary_entries: list[tuple[str, str]] = field(default_factory=list)  # (date, md)
    turns: list[TurnRecord] = field(default_factory=list)

    # -- recording -----------------------------------------------------------

    def record_briefing(self, label: str, markdown: str) -> None:
        self.briefings.append((label, markdown))

    def record_turn(self, turn: TurnRecord) -> None:
        self.turns.append(turn)

    def record_diary(self, in_game_date: str, markdown: str) -> None:
        self.diary_entries.append((in_game_date, markdown))

    # -- context assembly ----------------------------------------------------

    def build_messages(self, current_label: str, current_briefing: str,
                       instruction: str) -> list[dict]:
        raise NotImplementedError

    def build_diary_messages(self, prompt: str) -> list[dict]:
        raise NotImplementedError


class RollingMemory(MemoryPolicy):
    """Default policy: full briefing history + own diary + rolling action log."""

    def _chronicle_block(self, briefings: list[tuple[str, str]]) -> str:
        """The steward's perception + actions so far, as one block.

        `briefings` is the slice to render (the caller holds back the current
        briefing so it can be placed last, most-salient, for the action turn;
        the diary step renders all of them)."""
        parts: list[str] = []

        if briefings:
            parts.append("# Past briefings you have received\n")
            for label, md in briefings:
                parts.append(md.strip())
                parts.append("\n---\n")

        if self.turns:
            parts.append("# Record of orders you have issued\n")
            for t in self.turns:
                parts.append(f"## Month {t.month} ({t.label})")
                if t.reasoning.strip():
                    parts.append(f"Your reasoning: {t.reasoning.strip()}")
                if t.actions:
                    parts.append("Orders:")
                    parts.extend(f"  - {a}" for a in t.actions)
                else:
                    parts.append("Orders: none (you let the month pass).")
                parts.append("")

        if self.diary_entries:
            parts.append("# Your chronicle (diary entries you have written)\n")
            for date, md in self.diary_entries:
                parts.append(f"## Entry — {date}")
                parts.append(md.strip())
                parts.append("\n---\n")

        return "\n".join(parts).strip()

    def build_messages(self, instruction: str) -> list[dict]:
        """The most recently recorded briefing is the current month; earlier
        briefings, the action log, and the diary form the chronicle."""
        if not self.briefings:
            raise ValueError("no briefing recorded yet")
        messages: list[dict] = [{"role": "system", "content": self.charter}]

        current_label, current_md = self.briefings[-1]
        chronicle = self._chronicle_block(self.briefings[:-1])

        user_parts: list[str] = []
        if chronicle:
            user_parts.append(chronicle)
            user_parts.append("")
        user_parts.append("# This month's briefing\n")
        user_parts.append(current_md.strip())
        user_parts.append("\n\n" + instruction.strip())

        messages.append({"role": "user", "content": "\n".join(user_parts)})
        return messages

    def build_diary_messages(self, prompt: str) -> list[dict]:
        """Diary writing sees the whole chronicle (all briefings), then the
        diary prompt."""
        messages: list[dict] = [{"role": "system", "content": self.charter}]
        chronicle = self._chronicle_block(self.briefings)
        content = (f"{chronicle}\n\n{prompt.strip()}" if chronicle
                   else prompt.strip())
        messages.append({"role": "user", "content": content})
        return messages
