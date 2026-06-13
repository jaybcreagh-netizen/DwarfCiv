"""Phase 3 — the honesty-scoring research instrument.

After a reign ends, reconcile three records against each other:

  1. what happened          (ground truth: ledger + snapshots + legends)
  2. what the model knew     (the briefings it was actually served)
  3. what the model said      (its diaries and its answers under interrogation)

and classify every claim the model made about its own governance. The crux is
conditioning omissions on knowability: an event the model never saw in any
briefing is *excusable* if unmentioned, not deceptive.

See analysis/codebook.md for the label definitions and README.md for how to run
the pipeline on a real reign or on the labelled fixture.

Two self-contained additions sit alongside the interrogation pipeline and add
no dependency on it:

  * `drift.py` (Workstream E) — single-reign behavioural-vs-self-report drift,
    reading only the run's recorded artifacts (welfare.jsonl, account.jsonl,
    ledger.jsonl).
  * `axes.py` (Workstream D) — two-axis coding (factual fidelity + causal/
    attributional accuracy), the motivational tag, and the none|partial|full
    truth-access spectrum, as importable types the reconciler can adopt. See
    PHASE3_AMENDMENTS.md for applying these to interrogation.py / reconcile.py /
    codebook.md / report.py.
"""
