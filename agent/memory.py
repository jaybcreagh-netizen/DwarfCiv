"""What the governor carries between months.

The governing model is re-prompted every month, so "memory" is whatever we
choose to put back in front of it. That choice is a research parameter, not an
implementation detail: a model that cannot recall its founding year cannot be
held to what it said then, and any drift we measure would be an artifact of the
context window rather than a finding about the model.

So the policy here is **diaries and probe answers always survive; briefings are
what gets dropped**:

  * every prior diary entry, verbatim,
  * every prior in-situ probe answer (the self-report record drift is scored
    against),
  * the last ``MAX_BRIEFINGS`` monthly briefings, oldest dropped first,
  * this month's briefing, always.

If diaries alone exceed the budget on a very long reign, the *middle* of the
diary record is elided rather than its start — the founding year and the recent
past are the two ends drift is measured between, so both are kept.

The budget is in characters (~4 per token) because it is a crude guard against
an unbounded context, not an accounting system; the real token count is
reported per call by ``agent.client``.
"""

from __future__ import annotations

# ~30k tokens of history before eliding, leaving room for the briefing itself.
DEFAULT_CHAR_BUDGET = 120_000
MAX_BRIEFINGS = 3

_ELISION = ("\n\n[... earlier diary entries elided to fit the context; the "
            "founding months and the recent months are shown ...]\n\n")


def _diary_blocks(entries: list[dict]) -> list[str]:
    out = []
    for e in entries:
        if e.get("tag") != "diary" or not (e.get("text") or "").strip():
            continue
        date = (e.get("date") or {}).get("pretty", "")
        head = f"### Month {e.get('month_index')}" + (f" ({date})" if date else "")
        out.append(f"{head}\n{e['text'].strip()}")
    return out


def _probe_blocks(entries: list[dict]) -> list[str]:
    out = []
    for e in entries:
        if e.get("tag") != "in_situ":
            continue
        qa = "\n".join(f"Q: {x.get('question')}\nA: {(x.get('answer') or '').strip()}"
                       for x in e.get("qa") or [])
        out.append(f"### Year {e.get('year')}\n{qa}")
    return out


def _fit(blocks: list[str], budget: int) -> list[str]:
    """Keep both ends of the record, eliding the middle, within `budget`."""
    if not blocks:
        return []
    total = sum(len(b) for b in blocks)
    if total <= budget:
        return blocks
    head, tail, used = [], [], len(_ELISION)
    lo, hi = 0, len(blocks) - 1
    # Alternate taking from the founding end and the recent end so both survive.
    take_head = True
    while lo <= hi:
        idx = lo if take_head else hi
        if used + len(blocks[idx]) > budget:
            break
        used += len(blocks[idx])
        (head if take_head else tail).append(blocks[idx])
        if take_head:
            lo += 1
        else:
            hi -= 1
        take_head = not take_head
    if lo > hi:                      # everything fit after all
        return head + list(reversed(tail))
    return head + [_ELISION.strip()] + list(reversed(tail))


def build(account_entries: list[dict], briefings: list[str],
          current_briefing: str, *,
          max_briefings: int = MAX_BRIEFINGS,
          char_budget: int = DEFAULT_CHAR_BUDGET) -> str:
    """Assemble the governor's context for one month.

    ``account_entries`` is ``AccountRecord.entries`` (diary + reasoning +
    in_situ, in order); ``briefings`` is the markdown of every *prior* month's
    briefing, oldest first; ``current_briefing`` is this month's.
    """
    recent = [b for b in briefings[-max_briefings:] if b.strip()]
    diaries = _diary_blocks(account_entries)
    probes = _probe_blocks(account_entries)

    fixed = len(current_briefing) + sum(len(p) for p in probes)
    diaries = _fit(diaries, max(0, char_budget - fixed))

    # Briefings take whatever budget is left after the account material.
    spent = fixed + sum(len(d) for d in diaries)
    kept: list[str] = []
    for b in reversed(recent):            # newest-first while spending
        if spent + len(b) > char_budget:
            break
        kept.append(b)
        spent += len(b)
    kept.reverse()

    parts: list[str] = []
    if diaries:
        parts.append("# Your own diary, from the beginning of your rule\n\n"
                     + "\n\n".join(diaries))
    if probes:
        parts.append("# What you have said your priorities were\n\n"
                     + "\n\n".join(probes))
    if kept:
        parts.append("# The briefings of recent months\n\n"
                     + "\n\n---\n\n".join(kept))
    parts.append("# This month's briefing\n\n" + current_briefing)
    return "\n\n".join(parts)
