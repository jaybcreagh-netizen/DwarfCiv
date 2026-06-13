"""The account record — the governor's own contemporaneous narration.

This is the self-report corpus Phase 3 fact-checks against ground truth. It
holds three kinds of entry, all written at the time, all tagged with the
in-game date and month:

  * `diary`    — the governor's monthly reflection (free narration),
  * `reasoning`— the rationale it gave for the actions it took that month,
  * `in_situ`  — answers to the fixed yearly neutral probes (Workstream C).

Stored as append-only JSONL (account.jsonl) plus a readable Markdown
transcript (account.md). The in_situ entries are the self-report signal the
drift readout (Workstream E) compares against behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path


class AccountRecord:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.jsonl = self.run_dir / "account.jsonl"
        self.md = self.run_dir / "account.md"
        self.entries: list[dict] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, entry: dict) -> dict:
        self.entries.append(entry)
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._render_md_tail(entry)
        return entry

    def diary(self, month_index: int, date: dict | None, text: str) -> dict:
        return self._append({
            "tag": "diary", "month_index": month_index, "date": date,
            "text": text,
        })

    def reasoning(self, month_index: int, date: dict | None,
                  actions: list[dict]) -> dict:
        """Record the actions taken and the rationale given for each."""
        return self._append({
            "tag": "reasoning", "month_index": month_index, "date": date,
            "actions": actions,
        })

    def in_situ(self, month_index: int, year: int, date: dict | None,
                qa: list[dict]) -> dict:
        """Record a yearly neutral-probe block: [{question, answer}, ...]."""
        return self._append({
            "tag": "in_situ", "month_index": month_index, "year": year,
            "date": date, "qa": qa,
        })

    def by_tag(self, tag: str) -> list[dict]:
        return [e for e in self.entries if e.get("tag") == tag]

    # -- markdown transcript -------------------------------------------------

    def _render_md_tail(self, entry: dict) -> None:
        date = (entry.get("date") or {}).get("pretty", "")
        lines: list[str] = []
        tag = entry["tag"]
        if tag == "diary":
            lines.append(f"\n## Diary — month {entry['month_index']} "
                         f"({date})\n")
            lines.append(entry["text"].strip() + "\n")
        elif tag == "reasoning":
            if not entry["actions"]:
                return
            lines.append(f"\n### Actions — month {entry['month_index']} "
                         f"({date})\n")
            for a in entry["actions"]:
                params = {k: v for k, v in (a.get("params") or {}).items()
                          if k != "rationale"}
                lines.append(f"- **{a.get('tool')}** {params}")
                if a.get("rationale"):
                    lines.append(f"  - *rationale:* {a['rationale']}")
        elif tag == "in_situ":
            lines.append(f"\n## In-situ probe — year {entry['year']} "
                         f"({date})\n")
            for qa in entry["qa"]:
                lines.append(f"**Q: {qa['question']}**\n")
                lines.append(qa["answer"].strip() + "\n")
        with open(self.md, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
