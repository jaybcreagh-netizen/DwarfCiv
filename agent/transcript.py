"""The readable record of a reign — the Phase 2 deliverable.

Writes two files in lockstep:
  * transcript.md   — for a human reading the whole reign in one sitting and
    judging whether a culture emerged.
  * transcript.jsonl — the same content, machine-readable, for later phases.

Both are flushed as the run proceeds so an interrupted run still leaves a
usable, partial transcript.
"""

from __future__ import annotations

import json
from pathlib import Path


class Transcript:
    def __init__(self, run_dir: Path):
        self.md_path = run_dir / "transcript.md"
        self.jsonl_path = run_dir / "transcript.jsonl"
        self._md = open(self.md_path, "a", encoding="utf-8")
        self._jsonl = open(self.jsonl_path, "a", encoding="utf-8")

    # -- low level ------------------------------------------------------------

    def _w(self, text: str = "") -> None:
        self._md.write(text + "\n")
        self._md.flush()

    def _event(self, **fields) -> None:
        self._jsonl.write(json.dumps(fields, ensure_ascii=False) + "\n")
        self._jsonl.flush()

    # -- sections -------------------------------------------------------------

    def run_header(self, fort: str, backend: str, model: str, months: int,
                   memory_policy: str, action_cap: int) -> None:
        self._w(f"# Reign of {fort or 'the fortress'}")
        self._w()
        self._w(f"*A single {backend} steward (`{model}`) governs for "
                f"{months} in-game months.*")
        self._w()
        self._w(f"- memory policy: `{memory_policy}`")
        self._w(f"- per-month action cap: {action_cap}")
        self._w()
        self._w("---")
        self._w()
        self._event(kind="run_header", fort=fort, backend=backend, model=model,
                    months=months, memory_policy=memory_policy,
                    action_cap=action_cap)

    def month(self, month: int, date: str, briefing_md: str) -> None:
        self._w(f"## Month {month} — {date}")
        self._w()
        self._w("<details><summary>Briefing the steward read</summary>")
        self._w()
        self._w(briefing_md.strip())
        self._w()
        self._w("</details>")
        self._w()
        self._event(kind="month_briefing", month=month, date=date,
                    briefing_md=briefing_md)

    def reasoning(self, month: int, text: str, thinking: str = "") -> None:
        self._w("**Steward:**")
        self._w()
        if thinking:
            for line in thinking.strip().splitlines():
                self._w(f"> {line}")
            self._w()
        self._w(text.strip() or "_(no reasoning text)_")
        self._w()
        self._event(kind="reasoning", month=month, text=text, thinking=thinking)

    def actions(self, month: int, records: list[dict]) -> None:
        if not records:
            self._w("_(no orders issued)_")
            self._w()
            return
        self._w("**Orders:**")
        self._w()
        for r in records:
            args = r.get("arguments") or {}
            arg_s = ", ".join(f"{k}={v}" for k, v in args.items())
            status = "✓" if r.get("ok") else "✗"
            result = (r.get("result") or "").strip().replace("\n", " ")
            if len(result) > 240:
                result = result[:240] + "…"
            self._w(f"- {status} `{r['name']}({arg_s})` — {result}")
        self._w()
        self._event(kind="actions", month=month, records=records)

    def note(self, month: int, text: str) -> None:
        self._w(f"_{text}_")
        self._w()
        self._event(kind="note", month=month, text=text)

    def diary(self, month: int, season: str, date: str, text: str) -> None:
        self._w(f"### 📖 Diary — end of {season} ({date})")
        self._w()
        self._w(text.strip())
        self._w()
        self._w("---")
        self._w()
        self._event(kind="diary", month=month, season=season, date=date, text=text)

    def final_state(self, date: str, briefing_md: str) -> None:
        self._w(f"## Final state — {date}")
        self._w()
        self._w("<details><summary>Closing briefing</summary>")
        self._w()
        self._w(briefing_md.strip())
        self._w()
        self._w("</details>")
        self._w()
        self._event(kind="final_state", date=date, briefing_md=briefing_md)

    def cost_summary(self, summary: dict) -> None:
        self._w("## Run summary")
        self._w()
        cost = summary.get("estimated_usd")
        cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "n/a (unknown pricing)"
        self._w(f"- backend/model: {summary.get('backend')} / `{summary.get('model')}`")
        self._w(f"- model calls: {summary.get('calls')}")
        self._w(f"- input tokens: {summary.get('input_tokens'):,}")
        self._w(f"- output tokens: {summary.get('output_tokens'):,}")
        if summary.get("cache_read_tokens"):
            self._w(f"- cache-read tokens: {summary.get('cache_read_tokens'):,}")
        self._w(f"- estimated cost: {cost_s}")
        self._w()
        self._event(kind="cost_summary", **summary)

    def close(self) -> None:
        self._md.close()
        self._jsonl.close()
