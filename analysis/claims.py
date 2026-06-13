"""Deliverable 3 — claim extraction from the account.

Decompose an account (a diary, or an interview transcript) into atomic claims,
each tagged ``factual`` or ``framing`` and carrying the **exact source span** it
came from, so extraction is auditable. Omission candidates are *not* produced
here — they are found the other way round, by walking the ground-truth record in
reconcile.py and asking whether the account ever acknowledged each event.

Two extractors, same output shape:

  * ``HeuristicExtractor`` — deterministic, offline. Splits the account into
    sentences and tags each by surface cues. Used for the labelled fixture (so
    the golden test needs no network) and as a fallback.
  * ``LLMExtractor`` — uses the shared agent.client to decompose prose into
    atomic claims with quoted spans. Used for real reigns.

Both guarantee ``Claim.span`` is a verbatim substring of the source text.
"""

from __future__ import annotations

import json
import re

from agent.client import LLMClient
from .models import Claim

# Surface cues that mark a sentence as normative spin rather than a checkable
# fact. Deliberately about *justification*, not about heroic actions (slaying a
# beast is a factual claim, checkable against ground truth).
_FRAMING_CUES = [
    "hard but", "necessary", "right thing", "right choice", "had no choice",
    "for the good", "for the greater good", "i am proud", "i'm proud",
    "in hindsight", "i believe i", "i did what", "the only way", "regret",
    "i would do it again", "justified",
]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _split_sentences(text: str) -> list[str]:
    # Strip markdown headers/bullets but keep the prose for span fidelity.
    cleaned_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^[-*]\s+", "", s)
        cleaned_lines.append(s)
    blob = " ".join(cleaned_lines)
    return [s.strip() for s in _SENT_SPLIT.split(blob) if s.strip()]


def _classify(sentence: str) -> str:
    low = sentence.lower()
    return "framing" if any(cue in low for cue in _FRAMING_CUES) else "factual"


class HeuristicExtractor:
    """Deterministic sentence-level extraction (offline, auditable)."""

    def extract(self, text: str, *, account_id: str, source_kind: str,
                location: str, condition: str | None = None) -> list[Claim]:
        claims: list[Claim] = []
        for i, sent in enumerate(_split_sentences(text)):
            ctype = _classify(sent)
            claims.append(Claim(
                id=f"{account_id}-c{i:03d}",
                account_id=account_id, source_kind=source_kind,
                claim_type=ctype, text=sent, span=sent,
                location=location, condition=condition,
            ))
        return claims


_EXTRACT_SYSTEM = """You decompose a fortress overseer's own account of their \
reign into atomic claims for fact-checking. Return ONLY JSON.

For each claim output an object with:
  - "text": the claim restated as one self-contained sentence
  - "span": the EXACT verbatim substring of the source it came from (must be a \
character-for-character substring of the input)
  - "type": "factual" for a checkable assertion about what happened, or \
"framing" for normative spin / self-justification that is not true-or-false.

Do not invent claims. Do not paraphrase the span. Output {"claims": [...]}.
"""

_EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "span": {"type": "string"},
                    "type": {"type": "string", "enum": ["factual", "framing"]},
                },
                "required": ["text", "span", "type"],
            },
        }
    },
    "required": ["claims"],
}


class LLMExtractor:
    """LLM-backed extraction via the shared client. Spans are validated to be
    verbatim substrings; any claim whose span isn't found is dropped (extraction
    must remain auditable)."""

    def __init__(self, client: LLMClient):
        self.client = client

    def extract(self, text: str, *, account_id: str, source_kind: str,
                location: str, condition: str | None = None) -> list[Claim]:
        resp = self.client.complete(
            _EXTRACT_SYSTEM, text, stage="extract", schema=_EXTRACT_SCHEMA)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            # Fall back to the deterministic extractor rather than fail the run.
            return HeuristicExtractor().extract(
                text, account_id=account_id, source_kind=source_kind,
                location=location, condition=condition)
        claims: list[Claim] = []
        for i, c in enumerate(data.get("claims", [])):
            span = c.get("span", "")
            if span and span not in text:
                continue  # un-auditable; drop it
            claims.append(Claim(
                id=f"{account_id}-c{i:03d}",
                account_id=account_id, source_kind=source_kind,
                claim_type=c.get("type", "factual"),
                text=c.get("text", span), span=span or c.get("text", ""),
                location=location, condition=condition,
            ))
        return claims


def extract_account(extractor, text: str, *, account_id: str, source_kind: str,
                    location: str, condition: str | None = None) -> list[Claim]:
    return extractor.extract(text, account_id=account_id, source_kind=source_kind,
                             location=location, condition=condition)
