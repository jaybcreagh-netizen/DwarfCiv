"""Boot a snapshot and run one throwaway DFHack Lua probe against it.

Harness development needs live answers ("does this map have water?", "what
does this native call actually return?") before a mechanism is worth
building an action around. Those questions are not governed runs and must
not produce run artifacts that later analysis could mistake for evidence,
so this driver restores a save, evaluates one script, prints its output,
and exits without writing a ledger, briefing, or account.

    python -m setup.probe <snapshot-dir> <lua-file>
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from harness.dfhack_client import DFHackClient
from harness.loop import Run

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m setup.probe <snapshot> <lua-file>")
    snapshot, script = Path(sys.argv[1]), Path(sys.argv[2])
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    scratch = REPO_ROOT / "runs" / "_probe"
    scratch.mkdir(parents=True, exist_ok=True)
    run = Run(REPO_ROOT / "df", scratch, months=0,
              ticks_per_month=33600, resume_from=None)
    run.restore_save(snapshot)
    run.boot_and_load()
    try:
        print(run.client.lua(script.read_text(encoding="utf-8")))
    finally:
        run.client.stop()


if __name__ == "__main__":
    main()
