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

By default it uses a PassGovernor (takes no actions) so a charter run exercises
charter injection + probes + welfare matching end to end without an LLM. Plug
a real governor with --governor module:factory.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from harness.loop import Run, REPO_ROOT
from harness.welfare import WelfareTrace
from . import charter as charter_mod
from . import client as client_mod
from . import probes as probes_mod
from .account import AccountRecord
from .governor import Governor, ActionPlan, ActionCall
from .dispatch import dispatch

log = logging.getLogger("agent.run_governed")


class PassGovernor(Governor):
    """Takes no action; answers probes with a constant. The do-nothing control
    that still produces a full account + welfare trace (of no harm)."""

    name = "pass"

    def act(self, charter, briefing_md, briefing_json, context) -> ActionPlan:
        return ActionPlan(actions=[ActionCall("pass_turn")],
                          diary="(no intervention this month)")


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
        # The charter the model is governing under, saved verbatim for audit.
        (self.run_dir / "charter.md").write_text(charter.text, encoding="utf-8")

    # -- the per-month hook --------------------------------------------------

    def __call__(self, run: Run, month: int, state: dict | None,
                 events: list[dict]) -> None:
        # 1. Link any deaths this month back to the policies that caused them,
        #    BEFORE the governor acts, so the briefing it just read and the
        #    consequences it is about to learn of stay in the right order.
        if events:
            links = self.welfare.match_deaths(events)
            for lk in links:
                log.info("welfare: %s death of %s attributed to %s",
                         lk["cause"], lk.get("unit"), lk["attributed_to"])

        # Month 0 is the load-time briefing: no actions, but it primes the
        # context and lets us record a year-0 baseline probe.
        date = (state or {}).get("date")
        briefing_md = self._read_briefing(month)
        briefing_json = (state if state is not None
                         else self._read_briefing_json(month))
        context = {
            "month_index": month,
            "year": probes_mod.probe_year(month),
            "account": self.account.entries,
            "charter_id": self.charter.id,
            # Month 0 fires the baseline probe without ever calling act(), so
            # the probe needs this month's briefing handed to it directly.
            "briefing_md": briefing_md,
        }

        # 2. Decide and act.
        if month > 0:
            plan = self.governor.act(self.charter, briefing_md, briefing_json,
                                     context)
            Governor.validate(plan)
            outcomes = [dispatch(run.client, call, welfare=self.welfare)
                        for call in plan.actions]
            self.account.reasoning(month, date, outcomes)
            # The diary is written *after* the outcomes are known (see
            # Governor.observe): a governor that narrates post-dispatch cannot
            # claim an order that silently failed. Governors that narrate up
            # front return "" here and keep the diary their plan carried.
            diary = self.governor.observe(self.charter, outcomes, context) \
                or plan.diary
            if diary:
                self.account.diary(month, date, diary)

        # 3. Yearly neutral in-situ probe (Workstream C). Also fire a year-0
        #    baseline at load so drift has a t=0 anchor.
        if month == 0 or probes_mod.is_probe_month(month):
            year = probes_mod.probe_year(month)
            answers = self.governor.answer_probes(
                self.charter, probes_mod.IN_SITU_QUESTIONS, context)
            qa = [{"question": q, "answer": a} for q, a
                  in zip(probes_mod.IN_SITU_QUESTIONS, answers)]
            self.account.in_situ(month, year, date, qa)
            log.info("in-situ probe recorded for year %d", year)

    # -- end of reign ---------------------------------------------------------

    def finalize(self, governor: Governor) -> None:
        """Write what the reign cost and anything the governor failed to do.

        Provider failures are recorded rather than swallowed: a month whose
        account is empty because the API errored is not the same as a month the
        model chose not to narrate, and Phase 3 must not read the first as the
        second.
        """
        report: dict = {}
        client = getattr(governor, "client", None)
        if client is not None and hasattr(client, "usage_summary"):
            report["usage"] = client.usage_summary()
        errors = list(getattr(governor, "errors", []) or [])
        if errors:
            report["governor_errors"] = errors
            log.warning("%d governor call(s) failed this reign; see "
                        "governor.json", len(errors))
        if report:
            (self.run_dir / "governor.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8")

    # -- helpers -------------------------------------------------------------

    def _read_briefing(self, month: int) -> str:
        p = self.run_dir / f"briefing-{month:03d}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _read_briefing_json(self, month: int) -> dict:
        p = self.run_dir / f"briefing-{month:03d}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_governor(spec: str | None, *, model: str | None = None,
                  provider: str = "anthropic", effort: str = "high") -> Governor:
    """Resolve --governor to a Governor instance.

    Accepts three forms: ``None``/``pass`` (the do-nothing control), ``llm``
    (a real governing model, configured by --model/--provider/--effort), or
    ``module:factory`` for anything else. The factory may be a Governor
    subclass or a zero-arg callable returning one.
    """
    if not spec or spec == "pass":
        return PassGovernor()
    if spec == "llm":
        from .llm_governor import build as build_llm
        return build_llm(model=model, provider=provider, effort=effort)
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
    ap.add_argument("--governor", default=None,
                    help="'llm' for a real governing model, 'pass' for the "
                         "do-nothing control, or module:factory "
                         "(default: pass)")
    ap.add_argument("--model", default=None,
                    help="model id for --governor llm "
                         f"(default: {client_mod.DEFAULT_MODEL})")
    ap.add_argument("--provider", default="anthropic",
                    choices=["anthropic", "mock"],
                    help="'mock' exercises the full governance path offline")
    ap.add_argument("--effort", default="high",
                    help="adaptive-thinking effort for --governor llm")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--resume-from", default=None)
    ap.add_argument("--skip-legends", action="store_true")
    args = ap.parse_args()

    run_name = args.run_name or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(run_dir / "harness.log")])

    charter = charter_mod.load(args.charter)
    governor = load_governor(args.governor, model=args.model,
                             provider=args.provider, effort=args.effort)
    log.info("governed run: charter=%s governor=%s tension=%r",
             charter.id, governor.name, charter.intended_tension)

    governed = GovernedRun(run_dir, governor, charter)
    run = Run(Path(args.df_dir).resolve(), run_dir, args.months,
              ticks_per_month=33600,
              resume_from=Path(args.resume_from).resolve()
              if args.resume_from else None,
              export_legends_at_end=not args.skip_legends,
              charter_id=charter.id, on_month=governed,
              # Phase 3/4 need to know which model governed this reign; the
              # analysis pipeline groups by run.json's model_id.
              run_meta={"governor": governor.name,
                        "model_id": getattr(governor, "model_id", None)})
    try:
        run.run()
    finally:
        run.client.stop()
        run.ledger.close()
        governed.finalize(governor)


if __name__ == "__main__":
    main()
