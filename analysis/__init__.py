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
"""
