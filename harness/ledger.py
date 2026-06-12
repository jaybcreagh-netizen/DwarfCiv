"""Ground-truth event recording.

Tails DF's gamelog.txt, classifies each line, and appends everything to
runs/<id>/ledger.jsonl together with the in-game date at collection time.
The ledger is the source of truth used to fact-check later narrative
retellings, so NOTHING is filtered out: unparsed lines are recorded with
category "other".

Note on dates: DF does not timestamp gamelog lines. The harness polls the
log during/after each advance and stamps lines with the in-game date at
*collection* time, so a stamp is an upper bound (<= one poll interval late).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# Ordered (category, regex) pairs; first match wins. Patterns target DF v50.x
# gamelog phrasing. Anything unmatched lands in "other".
EVENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("death", re.compile(
        r"has been struck down|has been slain|has died|has bled to death|"
        r"has been killed|has suffocated|has starved to death|"
        r"has died of thirst|is dead|has been crushed|has drowned|"
        r"has been shot and killed|has been impaled|killed by", re.I)),
    ("birth", re.compile(r"has given birth to|has been born", re.I)),
    ("strange_mood", re.compile(
        r"is taken by a fey mood|withdraws from society|"
        r"has been possessed|is taken by a secretive mood|"
        r"has begun a mysterious construction|looses a roaring laughter|"
        r"cancels.*Claimed by a strange mood|stalks the fortress in a "
        r"macabre mood", re.I)),
    ("artifact", re.compile(
        r"has created a masterpiece|has created .*(artifact|a legend)|"
        r"has completed.*artifact|has claimed a", re.I)),
    ("tantrum", re.compile(
        r"is throwing a tantrum|has gone berserk|is stumbling around |"
        r"has gone stark raving mad|gives in to depression", re.I)),
    ("siege", re.compile(r"a vile force of darkness|the dead walk|siege", re.I)),
    ("ambush", re.compile(r"ambush|an ambush! curse|snatcher|thief", re.I)),
    ("megabeast", re.compile(
        r"forgotten beast|titan|dragon|hydra|roc |bronze colossus|"
        r"megabeast|has come!", re.I)),
    ("caravan", re.compile(
        r"caravan.*has arrived|merchants have arrived|"
        r"their wagons have bypassed|caravan.*embarked", re.I)),
    ("diplomacy", re.compile(r"diplomat|liaison|emissary", re.I)),
    ("migrants", re.compile(
        r"migrants? have arrived|migrant has arrived|"
        r"no one even considers", re.I)),
    ("mandate", re.compile(r"mandate", re.I)),
    ("noble", re.compile(
        r"has been elected|has become.*(mayor|baron|baroness|count|countess|"
        r"duke|duchess|expedition leader|manager|bookkeeper|broker)", re.I)),
    ("combat", re.compile(
        r"attacks the|strikes at|charges at|has been knocked unconscious|"
        r"interrupted by|is fighting", re.I)),
    ("season", re.compile(
        r"^(spring|summer|autumn|winter) has (arrived|come)", re.I)),
    ("weather", re.compile(r"it has started raining|a snow storm has", re.I)),
    ("job_cancel", re.compile(r"cancels |needs .* but there (is|are) none", re.I)),
    ("petition", re.compile(r"petition", re.I)),
]


def classify(line: str) -> str:
    for category, pat in EVENT_PATTERNS:
        if pat.search(line):
            return category
    return "other"


@dataclass
class GamelogTailer:
    """Incremental reader for DF's gamelog.txt (CP437-encoded, append-only)."""

    path: Path
    offset: int = 0

    def read_new(self) -> list[str]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:  # truncated/rotated
            self.offset = 0
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()
        text = data.decode("cp437", errors="replace")
        return [l.strip() for l in text.splitlines() if l.strip()]

    def skip_to_end(self) -> None:
        if self.path.exists():
            self.offset = self.path.stat().st_size


@dataclass
class Ledger:
    """Append-only JSONL ledger of every gamelog event."""

    path: Path
    seq: int = 0
    _fh: object = field(default=None, repr=False)

    def __post_init__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def record(self, raw_line: str, game_date: dict | None,
               source: str = "gamelog") -> dict:
        entry = {
            "seq": self.seq,
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "game_date": game_date,
            "source": source,
            "category": classify(raw_line) if source == "gamelog" else source,
            "raw": raw_line,
        }
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.seq += 1
        return entry

    def record_many(self, lines: list[str], game_date: dict | None) -> list[dict]:
        return [self.record(l, game_date) for l in lines]

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
