"""The governance loop — a single LLM steward over one in-game year.

This reuses the Phase-1 harness wholesale: `StewardRun` subclasses
`harness.loop.Run`, inheriting boot, tick-advance, briefing generation, the
ground-truth ledger, snapshots, crash recovery, and the legends export. The
only thing overridden is the top-level orchestration in `run()`, which
interleaves an agent turn before each month's advance and a diary at each
season's end.

Per-month cycle:
  1. assemble context (charter system prompt + memory) and show the steward
     the latest briefing,
  2. let the model issue orders (the actions.py verbs, as tools) until it
     calls pass_turn or hits the action cap, executing each and feeding the
     result back,
  3. advance the sim one month via the Phase-1 loop and write the next
     briefing + snapshot,
  4. at every third month, write a seasonal diary entry.

Ground-truth isolation: the steward is fed only the Markdown briefings and
its own past output. It is never handed ledger.jsonl or Legends data. The
ledger keeps recording underneath the whole run for later fact-checking.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.loop import Run, REPO_ROOT
from harness.dfhack_client import DFCrashed, DFError

from .client import Message, ToolResult, make_client, LLMError
from .memory import MemoryStore, make_policy
from .transcript import Transcript
from .tools import TOOL_SPECS, execute
from .diary import write_diary

log = logging.getLogger("steward")

GOVERNANCE_INSTRUCTION = (
    "It is the start of a new month. Based on the latest briefing above and "
    "everything you know so far, decide how to govern the settlement this "
    "month. Briefly explain your thinking, then issue your orders using the "
    "available tools. Issue as many orders as you judge wise, then call "
    "pass_turn to end the month. If you judge that nothing needs doing, you "
    "may call pass_turn straight away."
)


class StewardRun(Run):
    def __init__(self, df_dir: Path, run_dir: Path, months: int,
                 ticks_per_month: int, resume_from: Path | None,
                 export_legends_at_end: bool, llm, charter: str,
                 policy_name: str, action_cap: int):
        super().__init__(df_dir, run_dir, months, ticks_per_month, resume_from,
                         export_legends_at_end)
        self.llm = llm
        self.charter = charter
        self.policy = make_policy(policy_name)
        self.action_cap = action_cap
        self.memory = MemoryStore(path=run_dir / "memory" / "memory.jsonl")
        self.transcript = Transcript(run_dir)
        self.diary_index = 0

    # -- helpers --------------------------------------------------------------

    def _briefing_md(self, idx: int) -> str:
        return (self.run_dir / f"briefing-{idx:03d}.md").read_text(encoding="utf-8")

    def _briefing_date(self, idx: int) -> str:
        data = json.loads((self.run_dir / f"briefing-{idx:03d}.json").read_text())
        return (data.get("date") or {}).get("pretty", "")

    # -- the agent's monthly turn --------------------------------------------

    def governance_turn(self, month: int) -> None:
        """Let the steward issue orders until it passes or hits the cap."""
        messages = self.policy.build_messages(self.memory, GOVERNANCE_INSTRUCTION)
        records: list[dict] = []
        reasoning_parts: list[str] = []
        thinking_parts: list[str] = []
        actions_count = 0
        # A safety budget on model calls, above the action cap, so a model
        # that never issues a tool call can't loop forever.
        call_budget = self.action_cap + 3
        ended = False

        while not ended and call_budget > 0:
            call_budget -= 1
            try:
                resp = self.llm.generate(self.charter, messages, TOOL_SPECS)
            except LLMError as e:
                log.error("model call failed: %s", e)
                self.transcript.note(month, f"Model call failed: {e}")
                break

            if resp.text.strip():
                reasoning_parts.append(resp.text.strip())
            if resp.thinking.strip():
                thinking_parts.append(resp.thinking.strip())
            messages.append(resp.assistant_message)

            if resp.stop_reason == "refusal":
                self.transcript.note(month, "The steward declined to respond "
                                     "(safety refusal); treating as pass.")
                break
            if not resp.tool_calls:
                # produced text but issued no order — treat as end of turn.
                break

            results = []
            for tc in resp.tool_calls:
                res = execute(self.client, tc.name, tc.arguments)
                results.append(ToolResult(tc.id, res.content, is_error=not res.ok))
                records.append({"name": tc.name, "arguments": tc.arguments,
                                "ok": res.ok, "result": res.content})
                if tc.name == "pass_turn":
                    ended = True
                else:
                    actions_count += 1
            messages.append(Message.from_tool_results(results))

            if not ended and actions_count >= self.action_cap:
                self.transcript.note(month, f"Reached the per-month action cap "
                                     f"({self.action_cap}); ending the turn.")
                ended = True

        reasoning = "\n\n".join(reasoning_parts)
        thinking = "\n\n".join(thinking_parts)
        self.transcript.reasoning(month, reasoning, thinking)
        self.transcript.actions(month, records)
        self.memory.record_turn(month, reasoning, records)

    # -- orchestration (overrides Run.run) -----------------------------------

    def run(self) -> None:
        meta = {
            "started": datetime.now(timezone.utc).isoformat(),
            "phase": 2,
            "months": self.months,
            "ticks_per_month": self.ticks_per_month,
            "backend": self.llm.backend,
            "model": self.llm.model,
            "memory_policy": self.policy.name,
            "action_cap": self.action_cap,
            "resumed_from": str(self.resume_from) if self.resume_from else None,
        }
        (self.run_dir / "run.json").write_text(json.dumps(meta, indent=2))

        self.restore_save(self.resume_from or REPO_ROOT / "saves" / "dwarfciv-start")
        self.boot_and_load()
        self.tailer.skip_to_end()

        # Briefing 0 — the state the steward inherits.
        self.write_briefing(0, events=[])           # sets self.prev_state
        fort = (self.prev_state.get("fort") or {}).get("site_name") or "the fortress"
        self.transcript.run_header(fort, self.llm.backend, self.llm.model,
                                   self.months, self.policy.name, self.action_cap)
        self.memory.record_briefing(0, self.prev_state["date"]["pretty"],
                                    self._briefing_md(0))

        month = 1
        while month <= self.months:
            # 1) the steward governs on the latest briefing (briefing[month-1]).
            self.transcript.month(month, self._briefing_date(month - 1),
                                  self._briefing_md(month - 1))
            self.governance_turn(month)

            # 2) advance one month; retry the *advance only* on a DF crash so
            #    we never re-spend model tokens (orders for a crashed month are
            #    lost with the reverted snapshot — acceptable for a pilot).
            advanced = False
            for attempt in range(2):
                try:
                    self.advance_month()
                    state = self.collect_state()
                    events = self.month_events()
                    self.write_briefing(month, events, state)
                    self.snapshot(month)
                    self.prev_state = state
                    advanced = True
                    break
                except (DFCrashed, DFError) as e:
                    if attempt == 1:
                        raise
                    log.error("month %d advance failed (%s); recovering", month, e)
                    self.transcript.note(month, f"The simulation faltered ({e}); "
                                         "recovering from the last snapshot and "
                                         "retrying (this month's orders are lost).")
                    self.recover()
            assert advanced

            # 3) record the resulting perception into memory.
            self.memory.record_briefing(month, state["date"]["pretty"],
                                        self._briefing_md(month))

            # 4) seasonal diary every third month.
            if month % 3 == 0:
                season = state["date"].get("season") or f"the {self.diary_index + 1} season"
                self.diary_index += 1
                write_diary(self.llm, self.charter, self.memory, self.policy,
                            self.run_dir, self.transcript, self.diary_index,
                            month, season, state["date"]["pretty"])
            month += 1

        # closing state + legends + cost.
        self.transcript.final_state(self.prev_state["date"]["pretty"],
                                    self._briefing_md(self.months))
        if self.export_legends_at_end:
            try:
                self.export_legends()
            except (DFError, OSError) as e:
                log.error("legends export failed (non-fatal): %s", e)

        summary = self.llm.cost_summary()
        (self.run_dir / "cost.json").write_text(json.dumps(summary, indent=2))
        self.transcript.cost_summary(summary)
        log.info("run complete: %s", self.run_dir)
        log.info("token usage: %s in / %s out over %d calls; est. cost %s",
                 summary["input_tokens"], summary["output_tokens"],
                 summary["calls"],
                 f"${summary['estimated_usd']:.2f}"
                 if summary["estimated_usd"] is not None else "n/a")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run a single LLM steward over one in-game year.")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--backend", default="anthropic",
                    choices=["anthropic", "openai", "mock"])
    ap.add_argument("--model", default=None,
                    help="model id (default: backend's most-capable: "
                         "claude-opus-4-8 / gpt-5 / mock)")
    ap.add_argument("--temperature", type=float, default=None,
                    help="omitted by default; ignored by models that reject it "
                         "(Opus 4.7+/Fable 5)")
    ap.add_argument("--effort", default="high",
                    help="Anthropic effort: low|medium|high|xhigh|max")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--action-cap", type=int, default=15,
                    help="max orders the steward may issue per month")
    ap.add_argument("--memory-policy", default="full_history")
    ap.add_argument("--charter", default=str(REPO_ROOT / "config" / "charter.md"))
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--ticks-per-month", type=int, default=33600)
    ap.add_argument("--resume-from", default=None)
    ap.add_argument("--skip-legends", action="store_true")
    args = ap.parse_args()

    run_name = args.run_name or ("reign-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"))
    run_dir = REPO_ROOT / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(run_dir / "steward.log")])

    charter = Path(args.charter).read_text(encoding="utf-8")
    try:
        llm = make_client(args.backend, args.model,
                          temperature=args.temperature, effort=args.effort,
                          max_tokens=args.max_tokens)
    except LLMError as e:
        sys.exit(f"could not create model client: {e}")

    run = StewardRun(
        Path(args.df_dir).resolve(), run_dir, args.months, args.ticks_per_month,
        Path(args.resume_from).resolve() if args.resume_from else None,
        export_legends_at_end=not args.skip_legends,
        llm=llm, charter=charter, policy_name=args.memory_policy,
        action_cap=args.action_cap)
    try:
        run.run()
    finally:
        run.client.stop()
        run.ledger.close()
        run.transcript.close()


if __name__ == "__main__":
    main()
