"""The tick loop: run the pinned fortress unattended, one month at a time.

Usage:
    python -m harness.loop --months 12
    python -m harness.loop --months 3 --run-name smoke-test

Each run:
  1. restores the pristine embark save (saves/dwarfciv-start) into df/save/
     (use --resume-from <snapshot dir> to continue from a snapshot instead),
  2. boots DF headless and loads the fort from the title screen,
  3. for each month: advances ~33,600 ticks, hard-pauses, collects state,
     writes briefing-NNN.{json,md}, appends gamelog events to ledger.jsonl,
     quicksaves and copies a snapshot,
  4. on a DF crash: relaunches, reloads the latest snapshot, and retries the
     month (once per month) — runs are resumable from runs/<id>/snapshots/.

Outputs land in runs/<run-name>/.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .dfhack_client import DFHackClient, DFCrashed, DFError
from . import briefing as briefing_mod
from .ledger import GamelogTailer, Ledger

log = logging.getLogger("loop")

REPO_ROOT = Path(__file__).resolve().parents[1]
TICKS_PER_MONTH = 33600          # 28 days * 1200 ticks
SAVE_FOLDER = "region1"          # the pinned world's save folder name
POLL_INTERVAL = 3.0


class Run:
    def __init__(self, df_dir: Path, run_dir: Path, months: int,
                 ticks_per_month: int, resume_from: Path | None,
                 export_legends_at_end: bool = True):
        self.df_dir = df_dir
        self.run_dir = run_dir
        self.months = months
        self.ticks_per_month = ticks_per_month
        self.resume_from = resume_from
        self.export_legends_at_end = export_legends_at_end
        self.client = DFHackClient(df_dir, log_path=run_dir / "df.log")
        self.tailer = GamelogTailer(df_dir / "gamelog.txt")
        self.ledger = Ledger(run_dir / "ledger.jsonl")
        self.prev_state: dict | None = None
        self.last_snapshot: Path | None = None

    # -- save management ------------------------------------------------------

    def restore_save(self, source: Path) -> None:
        if not source.exists():
            raise DFError(f"start save not found: {source} "
                          "(run python -m setup.make_world first)")
        dest = self.df_dir / "save" / SAVE_FOLDER
        log.info("restoring save %s -> %s", source, dest)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(exist_ok=True)
        src = source / SAVE_FOLDER if (source / SAVE_FOLDER).exists() else source
        shutil.copytree(src, dest)

    def snapshot(self, month: int) -> Path:
        self.client.set_paused(True)
        self.client.quicksave()
        # quicksave is asynchronous: wait for the request flag to clear,
        # then give the writer a moment to finish.
        self.client.wait_for(
            "quicksave to complete",
            lambda: self.client.lua(
                "print(df.global.plotinfo.main.autosave_request)"
            ).strip() == "false",
            timeout=600, interval=2)
        time.sleep(5)
        src = self.df_dir / "save" / SAVE_FOLDER
        dest = self.run_dir / "snapshots" / f"month-{month:03d}" / SAVE_FOLDER
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        self.last_snapshot = dest.parent
        log.info("snapshot written: %s", dest.parent)
        return dest.parent

    # -- game lifecycle ---------------------------------------------------------

    def boot_and_load(self) -> None:
        self.client.start()
        self.client.wait_for_text("Continue active game", timeout=180)
        # Title flow (cf. DFHack ci/test.lua): continue -> world list ->
        # save list -> loading screen. With exactly one world and one save,
        # the wanted entry is always the top button.
        self.client.click_text("Continue active game")
        for _ in range(2):
            time.sleep(2)
            if self._fort_loaded():
                break
            mode = self.client.lua(
                "local scr=dfhack.gui.getCurViewscreen(true) "
                "print(df.viewscreen_titlest:is_instance(scr) and scr.mode or -1)"
            ).strip()
            if mode in ("2", "3"):
                self.client.run_command("obs-title-top")
        self.client.wait_for("fortress map load", self._fort_loaded, timeout=600)
        # Suppress DF tutorial popups for unattended play.
        try:
            self.client.run_command("hide-tutorials")
        except DFError as e:
            log.warning("hide-tutorials failed: %s", e)
        self.client.set_paused(True)
        log.info("fort loaded: %s", self.client.get_focus())

    def _fort_loaded(self) -> bool:
        out = self.client.lua(
            "print(dfhack.world.isFortressMode() and dfhack.isMapLoaded())")
        return out.strip() == "true"

    # -- the loop ------------------------------------------------------------

    def advance_month(self) -> dict:
        """Run the sim for one month of ticks, then hard-pause."""
        status = self.client.run_json_script("obs-advance")
        start_tick = status["date"]["absolute_tick"]
        target = start_tick + self.ticks_per_month
        log.info("advancing %d ticks (%d -> %d)",
                 self.ticks_per_month, start_tick, target)
        last_progress = time.monotonic()
        last_tick = start_tick
        while True:
            status = self.client.run_json_script("obs-advance", str(target))
            tick = status["date"]["absolute_tick"]
            self.collect_events(status["date"])
            if tick >= target:
                break
            if tick != last_tick:
                last_tick = tick
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > 300:
                raise DFError(
                    f"simulation stalled at tick {tick} (focus: "
                    f"{status.get('focus')}, action: {status.get('action')})")
            time.sleep(POLL_INTERVAL)
        self.client.set_paused(True)
        return status

    def collect_events(self, game_date: dict) -> list[dict]:
        lines = self.tailer.read_new()
        return self.ledger.record_many(lines, game_date)

    def run(self) -> None:
        meta = {
            "started": datetime.now(timezone.utc).isoformat(),
            "months": self.months,
            "ticks_per_month": self.ticks_per_month,
            "df_dir": str(self.df_dir),
            "resumed_from": str(self.resume_from) if self.resume_from else None,
        }
        (self.run_dir / "run.json").write_text(json.dumps(meta, indent=2))

        self.restore_save(self.resume_from or REPO_ROOT / "saves" / "dwarfciv-start")
        self.boot_and_load()
        # Gamelog lines from before this run belong to previous sessions.
        self.tailer.skip_to_end()

        # Briefing 000: the state we start from.
        self.write_briefing(0, events=[])

        month = 1
        retried = set()
        while month <= self.months:
            try:
                self.advance_month()
                state = self.collect_state()
                events = self.month_events()
                self.write_briefing(month, events, state)
                self.snapshot(month)
                self.prev_state = state
                month += 1
            except (DFCrashed, DFError) as e:
                if month in retried:
                    raise
                retried.add(month)
                log.error("month %d failed (%s); recovering from last snapshot",
                          month, e)
                self.recover()
        if self.export_legends_at_end:
            try:
                self.export_legends()
            except (DFError, OSError) as e:
                log.error("legends export failed (non-fatal): %s — see "
                          "README for the manual procedure", e)
        log.info("run complete: %s", self.run_dir)

    def export_legends(self) -> None:
        """Export Legends XML from the live fort via open-legends.

        open-legends taints the session (mode switches lose data), which is
        why this runs only after the final snapshot: the tainted session is
        simply discarded — DF auto-quits when legends mode is closed, and we
        never save afterwards.
        """
        log.info("exporting legends XML (this discards the live session)")
        before = set(self._legends_files())
        self.client.run_command("open-legends")
        # Confirm the open-legends warning screen.
        self.client.wait_for(
            "open-legends warning screen",
            lambda: "open-legends" in self.client.get_focus(), timeout=30)
        self.client.lua(
            "require('gui').simulateInput("
            "dfhack.gui.getCurViewscreen(), 'CUSTOM_ALT_L')")
        time.sleep(5)
        self.client.run_command("exportlegends", timeout=120)
        new_files: set = set()

        def export_done():
            nonlocal new_files
            new_files = set(self._legends_files()) - before
            if not new_files:
                return False
            # consider done when sizes are stable for one poll
            sizes = {f: f.stat().st_size for f in new_files}
            time.sleep(4)
            return all(f.stat().st_size == sizes[f] for f in new_files)

        self.client.wait_for("legends export files", export_done,
                             timeout=900, interval=5)
        dest = self.run_dir / "legends"
        dest.mkdir(exist_ok=True)
        for f in new_files:
            shutil.move(str(f), dest / f.name)
            log.info("legends artifact: %s", dest / f.name)
        self.client.stop(graceful=False)  # session is tainted; never save it

    def _legends_files(self) -> list[Path]:
        pats = ("*legends*.xml", "*legends*.xml.gz", "*-world_sites_and_pops.txt",
                "*-world_gen_param.txt")
        found: list[Path] = []
        for d in (self.df_dir, self.df_dir / "save" / SAVE_FOLDER):
            for p in pats:
                found.extend(d.glob(p))
        return found

    def collect_state(self) -> dict:
        state = self.client.run_json_script("obs-state", timeout=120)
        return state

    _month_event_start = 0

    def month_events(self) -> list[dict]:
        """Ledger entries recorded since the previous briefing."""
        # collect any stragglers written during the final pause
        date = self.client.run_json_script("obs-advance")["date"]
        self.collect_events(date)
        events = self._read_ledger_since(self._month_event_start)
        self._month_event_start = self.ledger.seq
        return events

    def _read_ledger_since(self, seq: int) -> list[dict]:
        events = []
        with open(self.ledger.path) as f:
            for line in f:
                e = json.loads(line)
                if e["seq"] >= seq:
                    events.append(e)
        return events

    def write_briefing(self, month: int, events: list[dict],
                       state: dict | None = None) -> None:
        if state is None:
            state = self.collect_state()
            self.prev_state = self.prev_state or None
        b = briefing_mod.build(state, events, self.prev_state, month)
        json_path, md_path = briefing_mod.write_briefing(self.run_dir, month, b)
        log.info("briefing written: %s", md_path)
        if month == 0:
            self.prev_state = state

    def recover(self) -> None:
        """Restart DF after a crash and reload the most recent snapshot."""
        self.client.stop(graceful=False)
        time.sleep(3)
        snapshot = self.last_snapshot
        if snapshot is None:
            snapshot = REPO_ROOT / "saves" / "dwarfciv-start"
            log.warning("no snapshot yet; recovering from pristine start save")
        self.restore_save(snapshot)
        self.boot_and_load()
        self.tailer.skip_to_end()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the fortress unattended for N in-game months.")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    ap.add_argument("--run-name", default=None,
                    help="name for runs/<name>/ (default: UTC timestamp)")
    ap.add_argument("--ticks-per-month", type=int, default=TICKS_PER_MONTH)
    ap.add_argument("--resume-from", default=None,
                    help="snapshot dir to resume from instead of the pristine "
                         "start save (e.g. runs/<id>/snapshots/month-002)")
    ap.add_argument("--skip-legends", action="store_true",
                    help="skip the legends XML export at run end")
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

    run = Run(Path(args.df_dir).resolve(), run_dir, args.months,
              args.ticks_per_month,
              Path(args.resume_from).resolve() if args.resume_from else None,
              export_legends_at_end=not args.skip_legends)
    try:
        run.run()
    finally:
        run.client.stop()
        run.ledger.close()


if __name__ == "__main__":
    main()
