"""The account record — the governor's own contemporaneous narration.

This is the self-report corpus Phase 3 fact-checks against ground truth. It
holds three kinds of entry, all written at the time, all tagged with the
in-game date and month:

  * `diary`    — the governor's monthly reflection (free narration),
  * `reasoning`— the rationale it gave for the actions it took that month,
  * `in_situ`  — answers to the fixed yearly neutral probes (Workstream C).

Stored as append-only JSONL (account.jsonl) plus a readable Markdown
transcript (account.md). Diary entries are also mirrored to ``diary/`` and
actions to ``transcript.jsonl``, the artifact names consumed by Phase 3.
The in_situ entries are the self-report signal the drift readout compares
against behaviour.
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
        if self.jsonl.exists():
            with open(self.jsonl, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.entries.append(json.loads(line))

    def _append(self, entry: dict) -> dict:
        self.entries.append(entry)
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._render_md_tail(entry)
        return entry

    def diary(self, month_index: int, date: dict | None, text: str) -> dict:
        entry = self._append({
            "tag": "diary", "month_index": month_index, "date": date,
            "text": text,
        })
        diary_dir = self.run_dir / "diary"
        diary_dir.mkdir(exist_ok=True)
        (diary_dir / f"month-{month_index:03d}.md").write_text(
            text.strip() + "\n", encoding="utf-8")
        return entry

    def reasoning(self, month_index: int, date: dict | None,
                  actions: list[dict],
                  plan_normalizations: list[dict] | None = None) -> dict:
        """Record the actions taken and the rationale given for each."""
        entry = self._append({
            "tag": "reasoning", "month_index": month_index, "date": date,
            "actions": actions,
            "plan_normalizations": list(plan_normalizations or []),
        })
        self._append_transcript(entry)
        return entry

    def in_situ(self, month_index: int, year: int, date: dict | None,
                qa: list[dict]) -> dict:
        """Record a yearly neutral-probe block: [{question, answer}, ...]."""
        return self._append({
            "tag": "in_situ", "month_index": month_index, "year": year,
            "date": date, "qa": qa,
        })

    def by_tag(self, tag: str) -> list[dict]:
        return [e for e in self.entries if e.get("tag") == tag]

    def truncate(self, entry_count: int) -> None:
        """Roll account artifacts back to a pre-month transaction boundary."""
        self.entries = self.entries[:entry_count]
        with open(self.jsonl, "w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.md.write_text("", encoding="utf-8")
        diary_dir = self.run_dir / "diary"
        diary_dir.mkdir(exist_ok=True)
        for path in diary_dir.glob("month-*.md"):
            path.unlink()
        (self.run_dir / "transcript.jsonl").write_text("", encoding="utf-8")
        for entry in self.entries:
            self._render_md_tail(entry)
            if entry.get("tag") == "diary":
                month = int(entry.get("month_index", 0) or 0)
                (diary_dir / f"month-{month:03d}.md").write_text(
                    str(entry.get("text", "")).strip() + "\n",
                    encoding="utf-8")
            elif entry.get("tag") == "reasoning":
                self._append_transcript(entry)

    def _append_transcript(self, entry: dict) -> None:
        transcript = self.run_dir / "transcript.jsonl"
        with open(transcript, "a", encoding="utf-8") as f:
            for action in entry.get("actions") or []:
                row = {
                    "month_index": entry.get("month_index"),
                    "date": entry.get("date"),
                    "action": action.get("tool"),
                    "args": action.get("params") or {},
                    "rationale": action.get("rationale", ""),
                    "participants": action.get("participants") or [],
                    "ok": action.get("ok"),
                    "status": action.get("status"),
                    "result": action.get("result"),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

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
            for repair in entry.get("plan_normalizations") or []:
                lines.append("- *plan normalization:* " +
                             json.dumps(repair, ensure_ascii=False,
                                        sort_keys=True))
            for a in entry["actions"]:
                params = {k: v for k, v in (a.get("params") or {}).items()
                          if k != "rationale"}
                lines.append(f"- **{a.get('tool')}** {params}")
                if a.get("rationale"):
                    lines.append(f"  - *rationale:* {a['rationale']}")
                ok = a.get("ok")
                status = "applied" if ok else "failed"
                result = a.get("result")
                if isinstance(result, dict):
                    status = str(result.get("status") or status)
                    detail = json.dumps(result, ensure_ascii=False,
                                        sort_keys=True)
                else:
                    detail = str(result or "")
                lines.append(f"  - *receipt:* **{status}**"
                             + (f" — {detail}" if detail else ""))
        elif tag == "in_situ":
            lines.append(f"\n## In-situ probe — year {entry['year']} "
                         f"({date})\n")
            for qa in entry["qa"]:
                lines.append(f"**Q: {qa['question']}**\n")
                lines.append(qa["answer"].strip() + "\n")
        with open(self.md, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
