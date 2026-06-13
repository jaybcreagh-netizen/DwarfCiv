"""Phase 2 agent package — the LLM-facing half of the observatory.

Two roles live here:

  * The **governor** (this workstream): turns the headless harness (harness/)
    into a governed run — injects a founding charter into the governing model's
    context, feeds it the per-month briefing, captures the actions and the
    contemporaneous rationale it takes (Workstream A), and asks it a fixed set
    of neutral operational questions each year (Workstream C). The model
    interface is pluggable (agent.governor), so a scripted/mock governor can
    drive the same path under test without an LLM.

  * The **provider-agnostic LLM client** (agent/client.py): the one client the
    Phase 2 governing loop and the Phase 3 interrogation harness + judge both
    reuse. Keeping it here (rather than in analysis/) means the two phases
    share a single client, as the brief requires.
"""
