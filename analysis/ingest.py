"""Load a completed run directory and reconcile it against the input contract.

The brief's contract assumes ``run.json`` carries ``model id, seed, charter``
and that Phase 2 artifacts (``diary/``, ``transcript.jsonl``) exist. Phase 1's
actual ``run.json`` carries only ``{started, months, ticks_per_month, df_dir,
resumed_from}`` and Phase 2 isn't wired up yet. Rather than assume, this module
loads what is present, records every mismatch in ``RunData.schema_warnings``,
and lets the pipeline run on whatever subset exists. Phase 4 can group by
``run.model_id`` once Phase 2 starts writing it.
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
    welfare: list[dict] = field(default_factory=list)          # Workstream A welfare records
    legends_files: list[Path] = field(default_factory=list)
    snapshot_dirs: list[Path] = field(default_factory=list)
    schema_warnings: list[str] = field(default_factory=list)

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

    # -- run.json ------------------------------------------------------------
    run_json = run_dir / "run.json"
    if run_json.exists():
        rd.run_meta = json.loads(run_json.read_text())
        rd.model_id = rd.run_meta.get("model_id") or rd.run_meta.get("model") or "unknown"
        rd.seed = rd.run_meta.get("seed")
        rd.charter = rd.run_meta.get("charter")
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
    if not rd.diaries:
        rd.warn("no diary/*.md found (Phase 2 artifact) — diary scoring will be empty.")

    # -- transcript (Phase 2) ------------------------------------------------
    transcript = run_dir / "transcript.jsonl"
    if transcript.exists():
        rd.transcript = _read_jsonl(transcript)
    else:
        rd.warn("transcript.jsonl missing (Phase 2 artifact) — Tier-2 events that "
                "derive from the action log cannot be reconstructed.")

    # -- welfare trace (Workstream A) ----------------------------------------
    # The linked causal records: moral/policy actions with the model's
    # contemporaneous rationale and the deaths matched back to them. Optional —
    # only present for governed runs — so its absence is not a schema warning.
    welfare = run_dir / "welfare.jsonl"
    if welfare.exists():
        rd.welfare = _read_jsonl(welfare)

    # -- legends + snapshots -------------------------------------------------
    legends_dir = run_dir / "legends"
    if legends_dir.is_dir():
        rd.legends_files = sorted(legends_dir.glob("*.xml"))
    snap_dir = run_dir / "snapshots"
    if snap_dir.is_dir():
        rd.snapshot_dirs = sorted(p for p in snap_dir.glob("month-*") if p.is_dir())

    return rd
