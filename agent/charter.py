"""Charter loading (Workstream B).

A charter is the founding constitution injected into the governor's system
context. They live as Markdown files in config/charters/. The leading HTML
comment in each file records the *intended tension* (the design rule: a
charter must collide with material pressure or contain internal tension);
it is stripped before the text reaches the model so the model never sees the
authoring note.

`neutral` is the value-light control and must always be available — it is the
baseline that separates real governance from performed morality.
"""

from __future__ import annotations

import re
from pathlib import Path

CHARTERS_DIR = Path(__file__).resolve().parents[1] / "config" / "charters"

NEUTRAL = "neutral"

_COMMENT = re.compile(r"<!--.*?-->", re.S)


class Charter:
    def __init__(self, charter_id: str, text: str, intended_tension: str,
                 path: Path):
        self.id = charter_id
        self.text = text                       # what the model sees
        self.intended_tension = intended_tension
        self.path = path

    def __repr__(self) -> str:
        return f"<Charter {self.id!r}>"


def _intended_tension(raw: str) -> str:
    m = _COMMENT.search(raw)
    if not m:
        return ""
    body = m.group(0)[4:-3]
    body = re.sub(r"^\s*Intended tension:\s*", "", body.strip(),
                  flags=re.I)
    return " ".join(body.split())


def available(directory: Path | None = None) -> list[str]:
    """Sorted charter ids found on disk (neutral first)."""
    d = Path(directory) if directory else CHARTERS_DIR
    ids = sorted(p.stem for p in d.glob("*.md"))
    if NEUTRAL in ids:
        ids = [NEUTRAL] + [i for i in ids if i != NEUTRAL]
    return ids


def load(charter_id: str, directory: Path | None = None) -> Charter:
    """Load one charter by id, with its authoring comment stripped from text."""
    d = Path(directory) if directory else CHARTERS_DIR
    path = d / f"{charter_id}.md"
    if not path.exists():
        have = ", ".join(available(d)) or "(none)"
        raise FileNotFoundError(
            f"no charter {charter_id!r} in {d} (available: {have})")
    raw = path.read_text(encoding="utf-8")
    tension = _intended_tension(raw)
    text = _COMMENT.sub("", raw).strip() + "\n"
    return Charter(charter_id, text, tension, path)
