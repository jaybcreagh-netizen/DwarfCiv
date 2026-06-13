"""The readable record — the actual Phase 2 deliverable.

`transcript.md` interleaves, per month: the briefing the steward read, its
reasoning, the orders it issued and their results, and (at season ends) its
diary entry. It is optimized for a human reading the whole reign in one sitting
to judge whether a culture emerged. A parallel `transcript.jsonl` keeps the same
events machine-readable for later phases.

Both files are written incrementally (append-as-you-go) so a transcript exists
even if a long run is interrupted.
"""

from __future__ import annotations

import json
from pathlib import Path


class Transcript:
    def __init__(self, run_dir: Path):
        self.md_path = run_dir / "transcript.md"
        self.jsonl_path = run_dir / "transcript.jsonl"
        self._md = self.md_path.open("w", encoding="utf-8")
        self._jsonl = self.jsonl_path.open("w", encoding="utf-8")

    # -- low-level ----------------------------------------------------------

    def _w(self, text: str = "") -> None:
        self._md.write(text + "\n")
        self._md.flush()

    def _event(self, kind: str, **data) -> None:
        self._jsonl.write(json.dumps({"event": kind, **data},
                                     ensure_ascii=False) + "\n")
        self._jsonl.flush()

    # -- sections -----------------------------------------------------------

    def header(self, meta: dict) -> None:
        self._w("# A Reign in the Settlement")
        self._w()
        self._w(f"- Backend / model: **{meta.get('backend')} / "
                f"{meta.get('model')}**")
        self._w(f"- Months: {meta.get('months')}")
        self._w(f"- Started: {meta.get('started')}")
        self._w(f"- Run: `{meta.get('run_dir')}`")
        self._w()
        self._w("This transcript interleaves, for each month, the briefing the "
                "steward read, its reasoning, the orders it issued, and — at "
                "each season's end — the chronicle entry it wrote. It is the "
                "steward's reign as it unfolded.")
        self._w()
        self._event("run_start", **meta)

    def turn(self, month: int, label: str, briefing_md: str, reasoning: str,
             actions: list[dict]) -> None:
        """One month: the briefing read, the steward's reasoning, its orders.

        `actions` is a list of {"name", "arguments", "result", "is_error"}.
        """
        self._w(f"\n## Month {month} — {label}\n")

        self._w("<details><summary>Briefing the steward read</summary>\n")
        self._w(briefing_md.strip())
        self._w("\n</details>\n")

        self._w("### The steward's deliberation\n")
        self._w(reasoning.strip() if reasoning.strip()
                else "*(the steward recorded no spoken reasoning this month)*")
        self._w()

        self._w("### Orders issued\n")
        if not actions:
            self._w("*The steward issued no orders and let the month pass.*")
        for a in actions:
            args = a.get("arguments") or {}
            arg_s = ", ".join(f"{k}={v!r}" for k, v in args.items())
            mark = "⚠️ " if a.get("is_error") else ""
            self._w(f"- **{a['name']}**({arg_s})")
            self._w(f"  - {mark}{a.get('result', '').strip()}")
        self._w()

        self._event("turn", month=month, label=label, reasoning=reasoning,
                    actions=actions)

    def diary(self, season_index: int, in_game_date: str, entry: str) -> None:
        self._w(f"\n### 📖 Chronicle entry — end of season "
                f"{season_index + 1} ({in_game_date})\n")
        self._w("> " + entry.strip().replace("\n", "\n> "))
        self._w()
        self._event("diary", season_index=season_index,
                    in_game_date=in_game_date, entry=entry)

    def note(self, text: str) -> None:
        """A harness-side note (e.g. a recovered crash) folded into the record."""
        self._w(f"\n> _harness note: {text}_\n")
        self._event("note", text=text)

    def footer(self, summary: str) -> None:
        self._w("\n---\n")
        self._w("## End of the reign\n")
        self._w(summary.strip())
        self._w()
        self._event("run_end", summary=summary)

    def close(self) -> None:
        self._md.close()
        self._jsonl.close()
