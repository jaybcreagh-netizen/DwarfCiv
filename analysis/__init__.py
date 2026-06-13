"""Phase 3 analysis.

This package is shared with the in-flight Phase 3 interrogation/reconciliation
build. `drift.py` (Workstream E) is a self-contained module that reads only
the run's recorded artifacts (welfare.jsonl, account.jsonl, ledger.jsonl) and
adds no dependency on the interrogation pipeline, so it can be built and used
independently and surfaced in the report later.
"""
