"""Load a completed run directory and reconcile it against the input contract.

Governed runs write ``model_id``, ``diary/``, and ``transcript.jsonl``. Older
runs may only have the equivalent entries in ``account.jsonl``; this loader
adapts them rather than silently discarding the model's account. Missing
artifacts remain explicit ``RunData.schema_warnings``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunData:
    run_dir: Path
    run_meta: dict = field(default_factory=dict)
    model_id: str = "unknown"
    seed: str | None = None
    charter: str | None = None
    ledger: list[dict] = field(default_factory=list)
    briefings: list[dict] = field(default_factory=list)        # sorted by month_index
    diaries: list[dict] = field(default_factory=list)          # [{name, season, text}]
    transcript: list[dict] = field(default_factory=list)
    # Operational planning is intentionally not treated as diary testimony.
    strategy: list[dict] = field(default_factory=list)
    legends_files: list[Path] = field(default_factory=list)
    snapshot_dirs: list[Path] = field(default_factory=list)
    schema_warnings: list[str] = field(default_factory=list)
    valid: bool = True

    def warn(self, msg: str) -> None:
        self.schema_warnings.append(msg)


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_run(run_dir: str | Path) -> RunData:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    rd = RunData(run_dir=run_dir)

    validity_path = run_dir / "validity.json"
    if validity_path.exists():
        validity = json.loads(validity_path.read_text())
        if validity.get("valid") is False:
            rd.valid = False
            rd.warn("run is explicitly marked invalid: " +
                    str(validity.get("reason") or "unspecified reason"))

    # -- run.json ------------------------------------------------------------
    run_json = run_dir / "run.json"
    if run_json.exists():
        rd.run_meta = json.loads(run_json.read_text())
        rd.model_id = rd.run_meta.get("model_id") or rd.run_meta.get("model") or "unknown"
        rd.seed = rd.run_meta.get("seed")
        rd.charter = rd.run_meta.get("charter")
        if (rd.run_meta.get("status") is not None
                and rd.run_meta.get("status") != "complete"):
            rd.valid = False
            rd.warn(
                f"run status is {rd.run_meta.get('status')!r}, not 'complete'; "
                "analysis artifacts may be partial")
        if rd.model_id == "unknown":
            rd.warn("run.json has no model_id/model — Phase 1 doesn't record it; "
                    "Phase 2 must add it so Phase 4 can group by model.")
        for k in ("seed", "charter"):
            if rd.run_meta.get(k) is None:
                rd.warn(f"run.json has no '{k}' (contract expects it; Phase 1 omits it).")
    else:
        rd.warn("run.json missing.")

    # -- ledger.jsonl --------------------------------------------------------
    ledger = run_dir / "ledger.jsonl"
    if ledger.exists():
        rd.ledger = _read_jsonl(ledger)
        if rd.ledger:
            keys = set(rd.ledger[0])
            expected = {"seq", "game_date", "source", "category", "raw"}
            missing = expected - keys
            if missing:
                rd.warn(f"ledger entries missing expected keys: {sorted(missing)}")
    else:
        rd.warn("ledger.jsonl missing — no ground truth from the gamelog.")

    # -- briefings -----------------------------------------------------------
    briefings = []
    for p in sorted(run_dir.glob("briefing-*.json")):
        try:
            briefings.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            rd.warn(f"briefing {p.name} is not valid JSON; skipped.")
    briefings.sort(key=lambda b: b.get("month_index", 0))
    rd.briefings = briefings
    if not briefings:
        rd.warn("no briefing-*.json found — knowability cannot be computed; "
                "every omission will be treated conservatively as unknowable.")

    # -- diaries (Phase 2) ---------------------------------------------------
    diary_dir = run_dir / "diary"
    if diary_dir.is_dir():
        for p in sorted(diary_dir.glob("*.md")):
            rd.diaries.append({"name": p.stem, "season": p.stem, "text": p.read_text()})
    account_path = run_dir / "account.jsonl"
    account_rows = _read_jsonl(account_path) if account_path.exists() else []
    if not rd.diaries and account_rows:
        for row in account_rows:
            if row.get("tag") == "diary" and str(row.get("text", "")).strip():
                month = int(row.get("month_index", 0) or 0)
                rd.diaries.append({
                    "name": f"month-{month:03d}",
                    "season": f"month-{month:03d}",
                    "text": row["text"],
                })
        if rd.diaries:
            rd.warn("diary/ missing; loaded diary entries from account.jsonl "
                    "compatibility path.")
    if not rd.diaries:
        rd.warn("no diary/*.md or account.jsonl diary entries found — diary "
                "scoring will be empty.")

    # -- transcript (Phase 2) ------------------------------------------------
    transcript = run_dir / "transcript.jsonl"
    if transcript.exists():
        rd.transcript = _read_jsonl(transcript)
    elif account_rows:
        for row in account_rows:
            if row.get("tag") != "reasoning":
                continue
            for action in row.get("actions") or []:
                rd.transcript.append({
                    "month_index": row.get("month_index"),
                    "date": row.get("date"),
                    "action": action.get("tool"),
                    "args": action.get("params") or {},
                    "rationale": action.get("rationale", ""),
                    "participants": action.get("participants") or [],
                    "ok": action.get("ok"),
                    "status": action.get("status"),
                    "result": action.get("result"),
                })
        if rd.transcript:
            rd.warn("transcript.jsonl missing; adapted actions from account.jsonl.")
    if not rd.transcript:
        rd.warn("transcript.jsonl missing (Phase 2 artifact) — Tier-2 events that "
                "derive from the action log cannot be reconstructed.")

    # -- persistent operational strategy -----------------------------------
    strategy = run_dir / "strategy.jsonl"
    if strategy.exists():
        rd.strategy = _read_jsonl(strategy)

    # -- legends + snapshots -------------------------------------------------
    legends_dir = run_dir / "legends"
    if legends_dir.is_dir():
        rd.legends_files = sorted(legends_dir.glob("*.xml"))
    snap_dir = run_dir / "snapshots"
    if snap_dir.is_dir():
        rd.snapshot_dirs = sorted(p for p in snap_dir.glob("month-*") if p.is_dir())

    return rd
