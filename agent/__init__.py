"""The governor (Phase 2) — the LLM-facing half of the observatory.

This package turns the headless harness (harness/) into a governed run: it
injects a founding charter into the governing model's context, feeds it the
per-month briefing, captures the actions and the contemporaneous rationale it
takes (Workstream A), and asks it a fixed set of neutral operational questions
each year (Workstream C). The model interface is pluggable (agent.governor),
so a scripted/mock governor can drive the same path under test without an LLM.
"""
