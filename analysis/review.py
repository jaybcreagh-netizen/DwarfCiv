"""Human review export + judge reliability (Deliverable 6, review half).

Export a stratified sample of verdicts to ``review/`` for a human to check
against the same codebook the judge used, and — if a human-labelled file is
present — compute the judge's agreement with those labels.

Stratification is by label, so rare-but-important categories (confabulation,
misrepresentation) are always represented rather than swamped by the common
ones. The exported file is pre-filled with the judge's verdict and a blank
``human_label`` field for the reviewer to complete.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .models import Verdict, Label, jsonable


def export_sample(out_dir: Path, verdicts_by_account: dict[str, list[Verdict]],
                  per_label: int = 3) -> Path:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    by_label: dict[Label, list[tuple[str, Verdict]]] = defaultdict(list)
    for acc, vs in verdicts_by_account.items():
        for v in vs:
            by_label[v.label].append((acc, v))

    sample = []
    for label in Label:
        items = by_label.get(label, [])
        # Deterministic stratified pick: first ``per_label`` by stable id.
        items = sorted(items, key=lambda av: av[1].target_id)[:per_label]
        for acc, v in items:
            sample.append({
                "target_id": v.target_id,
                "account_id": acc,
                "judge_label": v.label.value,
                "judge_method": v.judge_method,
                "severity": v.severity,
                "citation": v.citation,
                "rationale": v.rationale,
                "ground_truth_event_ids": v.ground_truth_event_ids,
                "knowability": v.knowability,
                "human_label": "",      # <- reviewer fills this in
                "human_notes": "",
            })

    path = review_dir / "sample.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for row in sample:
            f.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
    # A copy of the codebook lives next to the sample so the reviewer applies the
    # identical rubric the judge did.
    codebook = Path(__file__).with_name("codebook.md")
    if codebook.exists():
        (review_dir / "codebook.md").write_text(codebook.read_text())
    return path


def compute_agreement(out_dir: Path) -> dict | None:
    """If ``review/sample.jsonl`` has been filled in with ``human_label`` values,
    return the judge-vs-human agreement; otherwise None."""
    path = out_dir / "review" / "sample.jsonl"
    if not path.exists():
        return None
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    labelled = [r for r in rows if r.get("human_label")]
    if not labelled:
        return None
    agree = sum(1 for r in labelled if r["human_label"] == r["judge_label"])
    return {"n": len(labelled), "agreement": round(agree / len(labelled), 3),
            "agree": agree}
