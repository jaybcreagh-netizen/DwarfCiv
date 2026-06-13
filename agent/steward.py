"""The governance loop: a single LLM reigns over the fortress for a year.

`StewardRun` subclasses the Phase 1 `harness.loop.Run` and wires an agent into
its `before_month` / `after_month` hooks, reusing the Phase 1 tick loop,
snapshotting, briefing generation, ledger, and crash recovery wholesale. Per
in-game month:

  1. The steward reads the current briefing (its only window onto the fort) plus
     its own past reasoning and diary (assembled by the memory policy).
  2. The model issues orders via the `tools.py` vocabulary; each is executed
     against the live fort and the result fed back. It keeps acting until it
     calls `pass_turn` or hits the per-month action cap.
  3. The sim advances one month (Phase 1).
  4. At each season boundary it writes a diary entry for the chronicle.

GROUND-TRUTH ISOLATION (inviolable): the steward is fed only briefings and its
own words. It is never shown `ledger.jsonl` or Legends data. The ledger keeps
recording reality underneath the whole reign, unread by the agent.

    python -m agent.steward --months 12 --backend anthropic --model <id>
    python -m agent.steward --months 1   # cheap smoke run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.loop import Run, REPO_ROOT, TICKS_PER_MONTH, SAVE_FOLDER  # noqa: F401

from . import diary as diary_mod
from . import tools as tools_mod
from .client import LLMClient, build_client
from .memory import MemoryPolicy, RollingMemory, TurnRecord
from .transcript import Transcript

log = logging.getLogger("agent.steward")

CHARTER_PATH = REPO_ROOT / "config" / "charter.md"

TURN_INSTRUCTION = """\
The briefing above describes your settlement as it stands this month. Decide \
what, if anything, needs to be done, and issue your orders using the tools \
available to you. You may issue as many orders as you judge necessary. When you \
are finished — or if you choose to let the month pass without intervention — \
call pass_turn to end your turn and let the month run its course."""


class StewardRun(Run):
    def __init__(self, *args, llm: LLMClient, memory: MemoryPolicy,
                 transcript: Transcript, backend: str, model: str,
                 max_actions: int = 15, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm = llm
        self.memory = memory
        self.transcript = transcript
        self.backend = backend
        self.model = model
        self.max_actions = max_actions
        self._recorded: set[int] = set()
        self._committed_turn: int | None = None

    # -- briefing access ------------------------------------------------------

    def _load_briefing(self, idx: int) -> tuple[str, str]:
        """Return (in-game-date label, markdown) for briefing `idx`."""
        import json
        md = (self.run_dir / f"briefing-{idx:03d}.md").read_text()
        label = f"report #{idx}"
        json_path = self.run_dir / f"briefing-{idx:03d}.json"
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text())
                label = (data.get("date") or {}).get("pretty") or label
            except (ValueError, OSError):
                pass
        return label, md

    def _ensure_recorded(self, idx: int) -> tuple[str, str]:
        label, md = self._load_briefing(idx)
        if idx not in self._recorded:
            self.memory.record_briefing(label, md)
            self._recorded.add(idx)
        return label, md

    # -- the per-month agent turn (hook) -------------------------------------

    def before_month(self, month: int) -> None:
        """The steward reads briefing month-1 and issues this month's orders."""
        label, current_md = self._ensure_recorded(month - 1)

        # On a crash retry, drop the stale turn from memory so the steward's
        # context isn't polluted with a doubled month (the sim was rolled back
        # and the orders are being re-issued).
        if self._committed_turn == month:
            if self.memory.turns and self.memory.turns[-1].month == month:
                self.memory.turns.pop()
            self.transcript.note(
                f"month {month} was interrupted and is being retried; "
                "the steward is re-reading the same briefing and re-issuing "
                "orders.")

        reasoning, actions_log = self._run_agent_turn()

        action_lines = [self._fmt_action(a) for a in actions_log]
        self.memory.record_turn(
            TurnRecord(month, label, reasoning, action_lines))
        self.transcript.turn(month, label, current_md, reasoning, actions_log)
        self._committed_turn = month

    def _run_agent_turn(self) -> tuple[str, list[dict]]:
        """Drive the model until it passes the turn or hits the action cap."""
        messages = self.memory.build_messages(TURN_INSTRUCTION)
        reasoning_parts: list[str] = []
        actions_log: list[dict] = []
        count = 0

        while True:
            resp = self.llm.generate(messages, tools=tools_mod.TOOL_SCHEMAS)
            if resp.text.strip():
                reasoning_parts.append(resp.text.strip())
            messages.append(resp.assistant_message)

            if not resp.tool_calls:
                # No orders and no explicit pass — treat as end of turn.
                break

            passed = False
            for tc in resp.tool_calls:
                count += 1
                result, is_error = tools_mod.dispatch(
                    self.client, tc.name, tc.arguments)
                actions_log.append({
                    "name": tc.name, "arguments": tc.arguments,
                    "result": result, "is_error": is_error,
                })
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                    "content": result, "is_error": is_error,
                })
                if tc.name == tools_mod.PASS_TURN:
                    passed = True

            if passed:
                break
            if count >= self.max_actions:
                log.info("month action cap (%d) reached", self.max_actions)
                actions_log.append({
                    "name": "(action cap reached)", "arguments": {},
                    "result": f"Reached the per-month limit of "
                              f"{self.max_actions} orders; the month will run.",
                    "is_error": False})
                break

        return "\n\n".join(reasoning_parts), actions_log

    @staticmethod
    def _fmt_action(a: dict) -> str:
        args = a.get("arguments") or {}
        arg_s = ", ".join(f"{k}={v}" for k, v in args.items())
        prefix = "FAILED " if a.get("is_error") else ""
        return f"{prefix}{a['name']}({arg_s}) -> {a.get('result', '').strip()}"

    # -- the diary step (hook) -----------------------------------------------

    def after_month(self, month: int, state: dict, events: list[dict]) -> None:
        if month % 3 != 0:
            return
        # Record the briefing just produced so the diary reflects the season's
        # final state, then write the entry.
        label, _ = self._ensure_recorded(month)
        season_index = (month // 3) - 1
        try:
            entry, _path = diary_mod.write_entry(
                self.llm, self.memory, season_index, label, self.run_dir)
            self.transcript.diary(season_index, label, entry)
        except Exception as e:  # diary is non-fatal to the reign
            log.error("diary entry failed (non-fatal): %s", e)
            self.transcript.note(f"diary entry for season {season_index + 1} "
                                 f"could not be written: {e}")


def _load_charter() -> str:
    if not CHARTER_PATH.exists():
        raise FileNotFoundError(
            f"charter not found at {CHARTER_PATH} — config/charter.md is required")
    return CHARTER_PATH.read_text()


def _check_api_key(backend: str) -> None:
    var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(
        backend.lower())
    if var and not os.environ.get(var):
        sys.exit(f"error: {var} is not set (required for the {backend} backend)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run a single LLM steward over the fortress for N months.")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--backend", default="anthropic",
                    choices=["anthropic", "openai"])
    ap.add_argument("--model", default="claude-opus-4-8",
                    help="model id (default: claude-opus-4-8)")
    ap.add_argument("--temperature", type=float, default=None,
                    help="sampling temperature; left unset by default "
                         "(Opus 4.7/4.8 reject it)")
    ap.add_argument("--max-actions", type=int, default=15,
                    help="per-month order cap (default: 15)")
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    ap.add_argument("--run-name", default=None,
                    help="name for runs/<name>/ (default: UTC timestamp)")
    ap.add_argument("--ticks-per-month", type=int, default=TICKS_PER_MONTH)
    ap.add_argument("--resume-from", default=None)
    ap.add_argument("--skip-legends", action="store_true")
    args = ap.parse_args()

    _check_api_key(args.backend)

    run_name = args.run_name or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(run_dir / "harness.log")])

    llm = build_client(args.backend, args.model, args.temperature)
    memory = RollingMemory(charter=_load_charter())
    transcript = Transcript(run_dir)
    transcript.header({
        "backend": args.backend, "model": args.model, "months": args.months,
        "started": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
    })

    run = StewardRun(
        Path(args.df_dir).resolve(), run_dir, args.months,
        args.ticks_per_month,
        Path(args.resume_from).resolve() if args.resume_from else None,
        export_legends_at_end=not args.skip_legends,
        llm=llm, memory=memory, transcript=transcript,
        backend=args.backend, model=args.model, max_actions=args.max_actions)

    error: Exception | None = None
    try:
        run.run()
    except Exception as e:  # surface but still print the cost summary + close out
        error = e
        log.exception("run failed")
    finally:
        run.client.stop()
        run.ledger.close()
        summary = llm.cost_summary()
        log.info("token/cost summary: %s", summary)
        outcome = ("the reign ran to completion." if error is None
                   else f"the reign ended early: {error}")
        transcript.footer(f"{outcome}\n\nModel usage — {summary}")
        transcript.close()
        print("\n=== steward run complete ===")
        print(f"transcript: {run_dir / 'transcript.md'}")
        print(f"diary:      {run_dir / 'diary'}")
        print(f"ledger:     {run_dir / 'ledger.jsonl'} (ground truth, unseen by the steward)")
        print(f"usage:      {summary}")
    if error is not None:
        sys.exit(1)


if __name__ == "__main__":
    main()
