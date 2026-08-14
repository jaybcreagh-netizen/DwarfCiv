"""Drive a governed run: the Phase 2 loop wired onto the Phase 1 harness.

Composes the pieces:
  * a charter (Workstream B) injected into the governor's context,
  * a Governor (pluggable; LLM or scripted) that reads each month's briefing
    and returns actions-with-rationale and a diary (Workstream A),
  * the welfare trace, into which moral actions and their downstream
    consequences are recorded (Workstream A),
  * the account record, capturing the diary, the per-action reasoning, and the
    yearly neutral in-situ probes (Workstream C).

The harness owns the simulation; this owns the decisions. It installs itself
as the harness's per-month `on_month` hook, so a governed run is just a normal
`harness.loop.Run` with the hook set and the charter id recorded in run.json.

    python -m agent.run_governed --charter preserve_life --months 24

By default it uses a real LLM governor. ``--governor pass`` is the explicit
do-nothing control; ``--governor module:factory`` remains available for custom
governors.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from harness.loop import Run, REPO_ROOT
from harness.scenarios import SCENARIOS, apply_scenario
from harness.survival import (build_operational_context, load_handbook,
                              write_handbook_snapshot)
from harness.welfare import WelfareTrace
from . import charter as charter_mod
from . import probes as probes_mod
from .account import AccountRecord
from .governor import Governor, ActionPlan, ActionCall
from .dispatch import dispatch
from .client import LLMClient, default_model
from .llm_governor import LLMGovernor
from .strategy import StrategyRecord, control_strategy

log = logging.getLogger("agent.run_governed")


class PassGovernor(Governor):
    """Takes no action; answers probes with a constant. The do-nothing control
    that still produces a full account + welfare trace (of no harm)."""

    name = "pass"
    model_id = "control:pass"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        return ActionPlan(actions=[ActionCall("pass_turn")],
                          diary="(no intervention this month)",
                          strategy=control_strategy(
                              "Do-nothing control: no projects are pursued."))


class GovernedRun:
    """Holds the governor + charter + per-run records and serves as the
    harness on_month hook."""

    def __init__(self, run_dir: Path, governor: Governor,
                 charter: charter_mod.Charter):
        self.run_dir = Path(run_dir)
        self.governor = governor
        self.charter = charter
        self.welfare = WelfareTrace(self.run_dir / "welfare.jsonl")
        self.account = AccountRecord(self.run_dir)
        self.strategy = StrategyRecord(self.run_dir)
        self.handbook = load_handbook()
        write_handbook_snapshot(self.run_dir, self.handbook)
        # The charter the model is governing under, saved verbatim for audit.
        (self.run_dir / "charter.md").write_text(charter.text, encoding="utf-8")

    # -- the per-month hook --------------------------------------------------

    def __call__(self, run: Run, month: int, state: dict | None,
                 events: list[dict]) -> None:
        # 1. Link deaths to policy-associated records supported by the matcher,
        #    BEFORE the governor acts, so the briefing it just read and the
        #    consequences it is about to learn of stay in the right order.
        if events:
            links = self.welfare.match_deaths(events)
            for lk in links:
                log.info("welfare: %s death of %s associated with %s",
                         lk["cause"], lk.get("unit"), lk["attributed_to"])

        # Month 0 is actionable: under a pressure scenario the governor must
        # be able to respond before an entire month elapses.
        briefing_md = self._read_briefing(month)
        briefing_json = self._read_briefing_json(month) or (state or {})
        date = briefing_json.get("date")
        previous_briefing = (self._read_briefing_json(month - 1)
                             if month > 0 else None)
        context = {
            "month_index": month,
            "year": probes_mod.probe_year(month),
            "elapsed_interval": self._elapsed_interval(month, briefing_json),
            # Freeze the pre-decision account so the year-0 probe stays a true
            # baseline even though its API call is made after dispatch.
            "account": list(self.account.entries),
            "charter_id": self.charter.id,
            "operational_context": build_operational_context(
                briefing_json, previous_briefing, self.handbook),
            "prior_strategy": copy.deepcopy(self.strategy.latest),
        }

        # 2. Decide and act.
        try:
            plan = self.governor.act(self.charter, briefing_md, briefing_json,
                                     context)
            Governor.validate(plan)
            outcomes = [dispatch(run.client, call, welfare=self.welfare)
                        for call in plan.actions]
            if not plan.strategy:
                plan.strategy = control_strategy(
                    "This governor did not provide structured strategy state.")
            context["current_strategy"] = copy.deepcopy(plan.strategy)
            self.account.reasoning(
                month, date, outcomes,
                plan_normalizations=plan.normalizations)
            post_action_state = run.collect_state()
            post_path = self.run_dir / f"post-action-{month:03d}.json"
            post_path.write_text(json.dumps(post_action_state, indent=2,
                                            ensure_ascii=False),
                                 encoding="utf-8")
            post_operational = build_operational_context(
                post_action_state, briefing_json, self.handbook)
            self.strategy.record(
                month, date, plan.strategy, outcomes,
                post_operational["survival_metrics"])
            diary = self.governor.reflect(
                self.charter, briefing_md, briefing_json, outcomes,
                post_action_state, context, proposed_diary=plan.diary)
            if diary:
                self.account.diary(month, date, diary)

            # 3. Yearly neutral in-situ probe. Also fire a year-0 baseline.
            if month == 0 or probes_mod.is_probe_month(month):
                year = probes_mod.probe_year(month)
                answers = self.governor.answer_probes(
                    self.charter, probes_mod.IN_SITU_QUESTIONS, context)
                if len(answers) != len(probes_mod.IN_SITU_QUESTIONS):
                    raise ValueError(
                        "governor returned the wrong number of probe answers")
                qa = [{"question": q, "answer": a} for q, a
                      in zip(probes_mod.IN_SITU_QUESTIONS, answers)]
                self.account.in_situ(month, year, date, qa)
                log.info("in-situ probe recorded for year %d", year)
        finally:
            self._persist_usage()

    # -- helpers -------------------------------------------------------------

    def _read_briefing(self, month: int) -> str:
        p = self.run_dir / f"briefing-{month:03d}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _read_briefing_json(self, month: int) -> dict:
        p = self.run_dir / f"briefing-{month:03d}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _elapsed_interval(self, month: int, current: dict) -> dict | None:
        """Compact before/after facts for the month that just elapsed."""
        if month <= 0:
            return None
        previous = self._read_briefing_json(month - 1)
        before_date, after_date = previous.get("date") or {}, current.get("date") or {}
        before_tick = before_date.get("absolute_tick")
        after_tick = after_date.get("absolute_tick")
        elapsed = (after_tick - before_tick
                   if isinstance(before_tick, int) and isinstance(after_tick, int)
                   else None)
        return {
            "from": before_date,
            "to": after_date,
            "elapsed_ticks": elapsed,
            "population_before": previous.get("population"),
            "population_after": current.get("population"),
            "stocks_before": previous.get("stocks"),
            "stocks_after": current.get("stocks"),
            "events_during_interval": current.get("events") or [],
        }

    def _persist_usage(self) -> None:
        summary = getattr(self.governor, "usage_summary", None)
        if callable(summary):
            (self.run_dir / "governor_usage.json").write_text(
                json.dumps(summary(), indent=2), encoding="utf-8")

    def checkpoint(self):
        """Capture derived-record state before a month is attempted."""
        return (len(self.account.entries), copy.deepcopy(self.welfare.records),
                {p.name for p in self.run_dir.glob("post-action-*.json")},
                len(self.strategy.entries))

    def rollback(self, checkpoint) -> None:
        """Remove records for a month whose DF snapshot did not commit."""
        (account_count, welfare_records, post_action_files,
         strategy_count) = checkpoint
        self.account.truncate(account_count)
        self.strategy.truncate(strategy_count)
        self.welfare.records = welfare_records
        self.welfare._flush()
        for path in self.run_dir.glob("post-action-*.json"):
            if path.name not in post_action_files:
                path.unlink()
        self._persist_usage()


def load_governor(spec: str | None, *, provider: str = "anthropic",
                  model: str | None = None,
                  effort: str = "medium") -> Governor:
    """Build the first-class LLM/pass governor or resolve a custom factory.

    The factory may be a Governor subclass or a zero-arg callable returning
    one. ``None`` and ``llm`` both mean the real model-backed governor.
    """
    if not spec or spec == "llm":
        return LLMGovernor(LLMClient(provider=provider,
                                     model=model or default_model(provider),
                                     effort=effort))
    if spec == "pass":
        return PassGovernor()
    mod_name, _, attr = spec.partition(":")
    mod = importlib.import_module(mod_name)
    obj = getattr(mod, attr) if attr else mod
    gov = obj() if callable(obj) else obj
    if not isinstance(gov, Governor):
        raise TypeError(f"{spec} did not produce a Governor")
    return gov


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the fortress under a founding charter and a governor.")
    ap.add_argument("--charter", default=charter_mod.NEUTRAL,
                    help=f"charter id from config/charters/ "
                         f"(default: {charter_mod.NEUTRAL})")
    ap.add_argument("--governor", default="llm",
                    help="llm (default), pass (control), or module:factory")
    ap.add_argument("--provider", default="anthropic",
                    choices=["anthropic", "kimi", "mock"])
    ap.add_argument("--model", default=None,
                    help="provider model id (default: provider's recommended model)")
    ap.add_argument("--effort", default="medium",
                    choices=["low", "medium", "high", "max"])
    ap.add_argument("--scenario", default="scarcity", choices=SCENARIOS,
                    help="starting pressure profile (default: scarcity; use "
                         "baseline for the unmodified control embark)")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--resume-from", default=None)
    ap.add_argument("--skip-legends", action="store_true")
    args = ap.parse_args()

    run_name = args.run_name or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / "runs" / run_name
    if run_dir.exists() and any(run_dir.iterdir()):
        ap.error(f"run directory is not empty: {run_dir}; choose a new "
                 "--run-name so attempts cannot be mixed")
    run_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(run_dir / "harness.log")])

    charter = charter_mod.load(args.charter)
    governor = load_governor(args.governor, provider=args.provider,
                             model=args.model, effort=args.effort)
    log.info("governed run: charter=%s governor=%s scenario=%s tension=%r",
             charter.id, governor.name, args.scenario,
             charter.intended_tension)

    governed = GovernedRun(run_dir, governor, charter)
    run = Run(Path(args.df_dir).resolve(), run_dir, args.months,
              ticks_per_month=33600,
              resume_from=Path(args.resume_from).resolve()
              if args.resume_from else None,
              export_legends_at_end=not args.skip_legends,
              charter_id=charter.id, on_month=governed,
              model_id=getattr(governor, "model_id", governor.name),
              scenario=args.scenario,
              setup_hook=lambda client: apply_scenario(client, args.scenario),
              strict_on_month=True)
    try:
        run.run()
    except BaseException as exc:
        run.mark_failed(exc)
        raise
    finally:
        run.client.stop()
        run.ledger.close()


if __name__ == "__main__":
    main()
