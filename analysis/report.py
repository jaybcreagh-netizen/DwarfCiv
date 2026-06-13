"""Deliverable 8 — per-reign output.

Two artifacts:

  * ``results.json`` — the full machine-readable analysis (ground truth,
    knowability, claims, verdicts, aggregates, judge reliability, cost), shaped
    so Phase 4 can load many reigns and group by model id.
  * ``report.md`` — the reign laid out as a timeline. For each significant
    moment: what happened, what the model could see, what it later said in each
    interview condition, and the verdict with its evidence — so the
    contradictions are legible at a glance.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import jsonable, Label, Verdict


def write_results(out_dir: Path, payload: dict) -> Path:
    path = out_dir / "results.json"
    path.write_text(json.dumps(jsonable(payload), indent=2, ensure_ascii=False))
    return path


def _verdicts_for_event(verdicts_by_account: dict[str, list[Verdict]],
                        event_id: str) -> list[Verdict]:
    out = []
    for vs in verdicts_by_account.values():
        for v in vs:
            if event_id in v.ground_truth_event_ids:
                out.append(v)
    return out


def write_report(out_dir: Path, *, run_meta, events, knowability,
                 verdicts_by_account, aggregates, schema_warnings,
                 harness_requirements, usage, judge_reliability=None,
                 planted_eval=None) -> Path:
    L = []
    add = L.append
    model = run_meta.get("model_id") or run_meta.get("model") or "unknown"
    add(f"# Reign honesty report — model `{model}`")
    add("")
    add("Three records reconciled: what happened (ground truth), what the model "
        "could know (briefings), and what it said (diary + interrogation). "
        "Omissions are conditioned on knowability — an event the model never saw "
        "is *excusable* if unmentioned, not deceptive.")
    add("")

    # -- headline ------------------------------------------------------------
    add("## Headline")
    shift = aggregates.get("headline_shift") or {}
    if shift:
        for key, d in shift.items():
            add(f"- **{key}**: friendly {d['friendly']} → adversarial "
                f"{d['adversarial']} (Δ {d['delta']:+})")
    else:
        add("- (interview conditions not run, or insufficient data for a shift)")
    if planted_eval:
        add(f"- **Fixture recovery**: deception precision "
            f"{planted_eval['precision']:.2f}, recall {planted_eval['recall']:.2f} "
            f"against {planted_eval['n_planted']} planted labels.")
    if judge_reliability:
        add(f"- **Judge agreement with human sample**: "
            f"{judge_reliability['agreement']:.2f} over "
            f"{judge_reliability['n']} items.")
    add("")

    # -- per-condition metrics ----------------------------------------------
    add("## Per-account metrics")
    add("")
    add("| account | omission(adv) | confab | misrep | deception signals |")
    add("|---|---|---|---|---|")
    for acc, m in aggregates.get("per_account", {}).items():
        lc = m["label_counts"]
        add(f"| {acc} | {m['adverse_omitted']}/{m['adverse_knowable']} "
            f"| {m['confabulation_count']} | {lc['misrepresentation']} "
            f"| {m['deception_signals']} |")
    add("")

    # -- timeline ------------------------------------------------------------
    add("## Timeline — what happened vs. what was said")
    add("")
    know = knowability
    for ev in events:
        if not ev.significant:
            continue
        kr = know.get(ev.id)
        kval = kr.knowability.value if kr else "none"
        date = (ev.date or {}).get("pretty", "?")
        flag = " ⚠" if ev.adverse else ""
        add(f"### {date} — {ev.type}{flag} (tier {ev.tier})")
        add(f"- **What happened:** {ev.description}")
        add(f"- **Could the model know?** {kval}"
            + (f" (first surfaced month {kr.first_known_month})"
               if kr and kr.first_known_month is not None else ""))
        vs = _verdicts_for_event(verdicts_by_account, ev.id)
        if vs:
            add("- **What it said / verdict:**")
            for v in sorted(vs, key=lambda v: v.account_id):
                sev = f" sev{v.severity}" if v.severity is not None else ""
                add(f"    - `{v.account_id}` → **{v.label.value}**{sev} — {v.citation}")
        else:
            add("- **What it said:** (no account scored)")
        add("")

    # -- confabulations & framing (claims with no ground-truth anchor) -------
    add("## Confabulations & framing (claims not anchored to an event)")
    add("")
    any_unanchored = False
    for acc, vs in verdicts_by_account.items():
        for v in vs:
            if v.target_kind == "claim" and v.label in (Label.CONFABULATION,
                                                        Label.FRAMING):
                any_unanchored = True
                add(f"- `{acc}` → **{v.label.value}** — {v.citation}")
    if not any_unanchored:
        add("- (none)")
    add("")

    # -- provenance / caveats -----------------------------------------------
    add("## Harness requirements deferred to Phase 2")
    add("")
    add("Morally salient Tier-2 event classes that cannot yet be derived from "
        "current harness output (recorded, not fabricated):")
    for name, reason in harness_requirements:
        add(f"- **{name}** — {reason}")
    add("")
    if schema_warnings:
        add("## Input schema mismatches flagged")
        add("")
        for w in schema_warnings:
            add(f"- {w}")
        add("")
    add("## Cost & usage")
    add("")
    tot = usage.get("total", {})
    add(f"- provider `{usage.get('provider')}`, model `{usage.get('model')}`: "
        f"{tot.get('calls', 0)} calls, {tot.get('input_tokens', 0)} in / "
        f"{tot.get('output_tokens', 0)} out tokens, ${tot.get('cost_usd', 0)}")
    add("")
    path = out_dir / "report.md"
    path.write_text("\n".join(L))
    return path
