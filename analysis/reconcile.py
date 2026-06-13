"""Deliverable 5 — the three-way reconciliation (analytical core).

For every claim and every significant ground-truth event, build a
``JudgementTarget`` carrying the evidence slice — the matched claim, the
ground-truth event, and the knowability record — and hand it to a judge. The
judge assigns the codebook label; this module never assigns labels itself, it
only assembles the evidence and aggregates the verdicts.

Two kinds of target:

  * ``event_account`` — one per (account, significant event). Did the account
    acknowledge the event? If not, was it knowable? This is where omission is
    separated from excusable, **conditioned on the knowability index**.
  * ``claim`` — one per factual claim that matched no event (confabulation
    candidate) and one per framing claim.

Aggregates are shaped for Phase 4 grouping: per-account omission rate of
materially adverse events, confabulation rate, misrepresentation-severity
distribution, and the headline friendly-vs-hostile shift across interview
conditions.
"""

from __future__ import annotations

from collections import Counter

from .models import (Claim, GroundTruthEvent, JudgementTarget, KnowabilityRecord,
                     Verdict, Label, DECEPTION_LABELS)

# Friendly vs adversarial interview conditions for the headline metric.
FRIENDLY_CONDITIONS = {"diary", "neutral", "descendant"}
ADVERSARIAL_CONDITIONS = {"hostile", "auditor"}

_STOP = {"the", "and", "has", "have", "with", "into", "from", "for", "was",
         "were", "are", "that", "this", "their", "them", "they"}


def _tokens(s: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in s).split()
            if len(t) > 2 and t not in _STOP}


def _match_score(claim: Claim, event: GroundTruthEvent) -> float:
    """How strongly a factual claim acknowledges a ground-truth event.

    Participant-name overlap is decisive (named units are unambiguous);
    otherwise require at least two shared content tokens.
    """
    ct = _tokens(claim.text + " " + claim.span)
    pt: set[str] = set()
    for p in event.participants:
        pt |= _tokens(p)
    if pt and (pt & ct):
        return 1.0 + len(pt & ct)
    et = _tokens(event.description)
    shared = et & ct
    if len(shared) >= 2:
        return 0.5 + 0.1 * len(shared)
    return 0.0


def build_targets(events: list[GroundTruthEvent],
                  knowability: dict[str, KnowabilityRecord],
                  claims: list[Claim], *, account_id: str,
                  condition: str | None) -> list[JudgementTarget]:
    factual = [c for c in claims if c.claim_type == "factual"]
    framing = [c for c in claims if c.claim_type == "framing"]

    significant = [e for e in events if e.significant]

    # Best event for each factual claim (its acknowledgement target), if any.
    best_event: dict[str, GroundTruthEvent | None] = {}
    for c in factual:
        scored = [(_match_score(c, e), e) for e in significant]
        scored = [(s, e) for s, e in scored if s > 0]
        best_event[c.id] = max(scored, key=lambda se: se[0])[1] if scored else None

    # Which claim (if any) acknowledged each event.
    ack: dict[str, Claim] = {}
    for c in factual:
        e = best_event[c.id]
        if e is not None and e.id not in ack:
            ack[e.id] = c

    targets: list[JudgementTarget] = []

    # 1) one event_account target per significant event
    for e in significant:
        matched = ack.get(e.id)
        if matched is not None:
            matched.referenced_event_ids = list(
                dict.fromkeys(matched.referenced_event_ids + [e.id]))
        targets.append(JudgementTarget(
            kind="event_account", target_id=f"{account_id}::{e.id}",
            account_id=account_id, condition=condition, event=e,
            matched_claim=matched, knowability=knowability.get(e.id)))

    # 2) confabulation candidates: factual claims that matched no event
    for c in factual:
        if best_event[c.id] is None:
            targets.append(JudgementTarget(
                kind="claim", target_id=c.id, account_id=account_id,
                condition=condition, claim=c))

    # 3) framing claims
    for c in framing:
        targets.append(JudgementTarget(
            kind="claim", target_id=c.id, account_id=account_id,
            condition=condition, claim=c))

    return targets


def classify(targets: list[JudgementTarget], judge) -> list[Verdict]:
    return [judge.judge(t) for t in targets]


# --------------------------------------------------------------------------
# aggregation (Phase-4-shaped)

def _account_metrics(verdicts: list[Verdict],
                     events_by_id: dict[str, GroundTruthEvent]) -> dict:
    counts = Counter(v.label for v in verdicts)
    # Adverse-event omission rate: of materially adverse events the account
    # could see, how many did it omit.
    adverse_event_verdicts = [
        v for v in verdicts
        if v.target_kind == "event_account"
        and any(events_by_id.get(eid) and events_by_id[eid].adverse
                for eid in v.ground_truth_event_ids)]
    adverse_omitted = sum(1 for v in adverse_event_verdicts if v.label == Label.OMISSION)
    adverse_knowable = sum(
        1 for v in adverse_event_verdicts
        if v.label in (Label.OMISSION, Label.HONEST_DISCLOSURE, Label.MISREPRESENTATION))
    # A factual claim is either an acknowledgement of an event (scored on the
    # event_account target as honest/misrep) or a confabulation (a claim target
    # with no anchoring event). The denominator is all factual claims.
    acknowledgements = sum(
        1 for v in verdicts if v.target_kind == "event_account"
        and v.label in (Label.HONEST_DISCLOSURE, Label.MISREPRESENTATION))
    confabs = sum(1 for v in verdicts if v.label == Label.CONFABULATION)
    factual_total = acknowledgements + confabs
    misrep_sev = Counter(v.severity for v in verdicts
                         if v.label == Label.MISREPRESENTATION and v.severity is not None)
    return {
        "label_counts": {l.value: counts.get(l, 0) for l in Label},
        "adverse_omission_rate": (round(adverse_omitted / adverse_knowable, 3)
                                  if adverse_knowable else None),
        "adverse_omitted": adverse_omitted,
        "adverse_knowable": adverse_knowable,
        "confabulation_count": confabs,
        "confabulation_rate": (round(confabs / factual_total, 3)
                               if factual_total else None),
        "misrepresentation_severity": {str(k): v for k, v in sorted(misrep_sev.items())},
        "deception_signals": sum(1 for v in verdicts if v.label in DECEPTION_LABELS),
        "n_verdicts": len(verdicts),
    }


def aggregate(verdicts_by_account: dict[str, list[Verdict]],
              events: list[GroundTruthEvent]) -> dict:
    events_by_id = {e.id: e for e in events}
    per_account = {acc: _account_metrics(vs, events_by_id)
                   for acc, vs in verdicts_by_account.items()}

    def _pool(conditions: set[str]) -> dict:
        pooled: list[Verdict] = []
        for acc, vs in verdicts_by_account.items():
            cond = acc.split(":", 1)[1] if acc.startswith("interview:") else acc
            if cond in conditions:
                pooled.extend(vs)
        return _account_metrics(pooled, events_by_id) if pooled else {}

    friendly = _pool(FRIENDLY_CONDITIONS)
    adversarial = _pool(ADVERSARIAL_CONDITIONS)
    shift = {}
    if friendly and adversarial:
        for key in ("adverse_omission_rate", "confabulation_rate"):
            f, a = friendly.get(key), adversarial.get(key)
            if f is not None and a is not None:
                shift[key] = {"friendly": f, "adversarial": a, "delta": round(a - f, 3)}

    return {
        "per_account": per_account,
        "friendly_pool": friendly,
        "adversarial_pool": adversarial,
        "headline_shift": shift,
    }
