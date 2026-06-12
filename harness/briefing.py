"""Build the per-month fortress briefing.

Input: the raw state dict dumped by dfhack-scripts/obs-state.lua plus the
ledger events collected since the previous briefing. Output: briefing JSON
(raw data, machine-checkable) and Markdown (~1-2k tokens, what a governing
agent will actually read).

Design: the Markdown leads with what needs attention (alerts), then status,
population, stocks, threats, events, pending matters. Numbers come straight
from the state dump; derived judgements (e.g. "LOW") are computed here with
explicit thresholds so they can be tuned without touching Lua.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# Survival thresholds, expressed per-capita where sensible. A dwarf drinks
# ~5 units and eats ~2-4 units of food per month.
DRINK_PER_DWARF_FLOOR = 2.0
FOOD_PER_DWARF_FLOOR = 2.0
SEEDS_FLOOR = 10
WOOD_FLOOR = 10

# Event categories surfaced individually (everything else is summarized).
HEADLINE_CATEGORIES = [
    "death", "siege", "megabeast", "ambush", "strange_mood", "tantrum",
    "artifact", "birth", "migrants", "caravan", "diplomacy", "mandate",
    "noble", "petition", "season",
]
MAX_HEADLINE_EVENTS = 40


def build(state: dict, events: list[dict], prev_state: dict | None,
          month_index: int) -> dict:
    """Assemble the briefing data structure (the JSON artifact)."""
    briefing = {
        "month_index": month_index,
        "date": state.get("date"),
        "population": state.get("population"),
        "idle_adults": state.get("idle_adults"),
        "open_jobs": state.get("open_jobs"),
        "stocks": state.get("stocks"),
        "threats": state.get("threats"),
        "squads": state.get("squads"),
        "mandates": state.get("mandates"),
        "open_petitions_approx": state.get("open_petitions_approx"),
        "dwarves": state.get("dwarves"),
        "fort": state.get("fort"),
        "state_errors": state.get("errors"),
        "alerts": [],
        "population_delta": None,
        "events": events,
    }

    pop = state.get("population") or 0
    stocks = state.get("stocks") or {}

    def alert(msg):
        briefing["alerts"].append(msg)

    if pop:
        drink = stocks.get("drink", 0)
        food = stocks.get("food_total", 0)
        if drink < DRINK_PER_DWARF_FLOOR * pop:
            alert(f"LOW DRINK: {drink} units for {pop} dwarves "
                  f"(< {DRINK_PER_DWARF_FLOOR}/dwarf)")
        if food < FOOD_PER_DWARF_FLOOR * pop:
            alert(f"LOW FOOD: {food} units for {pop} dwarves "
                  f"(< {FOOD_PER_DWARF_FLOOR}/dwarf)")
    if stocks.get("seeds", 0) < SEEDS_FLOOR:
        alert(f"LOW SEEDS: {stocks.get('seeds', 0)}")
    if stocks.get("wood", 0) < WOOD_FLOOR:
        alert(f"LOW WOOD: {stocks.get('wood', 0)}")

    threats = state.get("threats") or {}
    if threats.get("siege_active"):
        alert("SIEGE IN PROGRESS")
    hostiles = threats.get("hostiles") or []
    if hostiles:
        alert(f"{len(hostiles)} hostile creature(s) on the map")

    unhappy = [d for d in state.get("dwarves") or []
               if d.get("stress_category", 3) <= 1]
    if unhappy:
        alert(f"{len(unhappy)} dwarves unhappy or worse")
    moody = [d for d in state.get("dwarves") or [] if d.get("strange_mood")]
    if moody:
        alert(f"strange mood: {', '.join(d['name'] for d in moody)}")

    if prev_state and prev_state.get("population") is not None \
            and state.get("population") is not None:
        briefing["population_delta"] = (state["population"]
                                        - prev_state["population"])
    return briefing


# --------------------------------------------------------------------------
# Markdown rendering


def render_markdown(briefing: dict) -> str:
    date = briefing.get("date") or {}
    lines: list[str] = []
    add = lines.append

    add(f"# Fortress briefing — {date.get('pretty', 'date unknown')}")
    fort = briefing.get("fort") or {}
    if fort.get("site_name"):
        add(f"*{fort['site_name']}* — report #{briefing['month_index']}")
    add("")

    alerts = briefing.get("alerts") or []
    if alerts:
        add("## ⚠ Needs attention")
        for a in alerts:
            add(f"- {a}")
        add("")

    # -- status line ---------------------------------------------------------
    add("## Status")
    pop = briefing.get("population")
    delta = briefing.get("population_delta")
    delta_s = f" ({delta:+d} since last report)" if delta else ""
    add(f"- Population: **{pop}**{delta_s}; "
        f"idle adults: {briefing.get('idle_adults')}; "
        f"queued jobs: {briefing.get('open_jobs')}")
    stocks = briefing.get("stocks") or {}
    add(f"- Stocks: drink **{stocks.get('drink')}**, "
        f"food **{stocks.get('food_total')}** "
        f"(prepared/raw {stocks.get('food')}, plants {stocks.get('plants')}), "
        f"seeds {stocks.get('seeds')}, wood {stocks.get('wood')}, "
        f"stone {stocks.get('stone')}, metal bars {stocks.get('bars')}")
    squads = briefing.get("squads") or []
    if squads:
        sq = ", ".join(f"{s['alias'] or s['name']} ({s['members']} members)"
                       for s in squads)
        add(f"- Military: {sq}")
    else:
        add("- Military: no squads")
    add("")

    # -- threats ---------------------------------------------------------------
    threats = briefing.get("threats") or {}
    hostiles = threats.get("hostiles") or []
    add("## Threats")
    if threats.get("siege_active"):
        add("- **SIEGE ACTIVE**")
    if hostiles:
        for h in hostiles[:15]:
            tags = []
            if h.get("invader"):
                tags.append("invader")
            if h.get("great_danger"):
                tags.append("GREAT DANGER")
            tag_s = f" [{', '.join(tags)}]" if tags else ""
            add(f"- {h['name']}{tag_s}")
        if len(hostiles) > 15:
            add(f"- …and {len(hostiles) - 15} more hostiles")
    elif not threats.get("siege_active"):
        add("- None visible")
    add("")

    # -- events ------------------------------------------------------------------
    add("## Events since last report (newest first)")
    lines.extend(_render_events(briefing.get("events") or []))
    add("")

    # -- pending --------------------------------------------------------------
    add("## Pending matters")
    mandates = briefing.get("mandates") or []
    if mandates:
        for m in mandates:
            add(f"- Mandate: {m.get('noble')} demands "
                f"{m.get('mode', '?')} {m.get('amount_total')}x "
                f"{m.get('item_type')} (time left: {m.get('timeout_left')})")
    petitions = briefing.get("open_petitions_approx")
    if petitions:
        add(f"- Open petitions/agreements (approx): {petitions}")
    if not mandates and not petitions:
        add("- Nothing pending")
    add("")

    # -- roster ----------------------------------------------------------------
    add("## Dwarves")
    dwarves = briefing.get("dwarves") or []
    for d in sorted(dwarves, key=lambda d: d.get("stress_category", 3)):
        flags = []
        if d.get("strange_mood"):
            flags.append(f"STRANGE MOOD ({d['strange_mood']})")
        cat = d.get("stress_category", 3)
        if cat <= 1:
            flags.append(d.get("stress_label", "unhappy").upper())
        elif cat >= 5:
            flags.append(d.get("stress_label", "happy"))
        if not d.get("adult"):
            flags.append("child")
        flag_s = f" — {'; '.join(flags)}" if flags else ""
        job = f" (now: {d['current_job']})" if d.get("current_job") else ""
        add(f"- {d.get('name')} — {d.get('profession')}{job}{flag_s}")
    add("")

    errors = briefing.get("state_errors") or []
    if errors:
        add("## Harness warnings")
        for e in errors:
            add(f"- state collection error: {e}")
        add("")
    return "\n".join(lines)


def _render_events(events: list[dict]) -> list[str]:
    """Deduplicate and summarize ledger events, newest first."""
    out: list[str] = []
    if not events:
        return ["- (no events recorded)"]

    by_cat: dict[str, list[dict]] = {}
    for e in events:
        by_cat.setdefault(e.get("category", "other"), []).append(e)

    # Aggregate noise categories into counts.
    noisy = {"job_cancel", "combat", "weather", "other"}
    headline: list[dict] = []
    for cat in HEADLINE_CATEGORIES:
        headline.extend(by_cat.get(cat, []))
    headline.sort(key=lambda e: e.get("seq", 0), reverse=True)

    seen: Counter = Counter()
    shown = 0
    for e in headline:
        raw = e.get("raw", "")
        seen[raw] += 1
        if seen[raw] > 1:
            continue
        if shown >= MAX_HEADLINE_EVENTS:
            out.append(f"- …and {len(headline) - shown} more notable events "
                       "(see ledger.jsonl)")
            break
        date = (e.get("game_date") or {}).get("pretty", "")
        cat = e.get("category", "")
        out.append(f"- [{cat}] {raw}" + (f" *(~{date})*" if date else ""))
        shown += 1
    for raw, n in seen.items():
        if n > 1:
            out.append(f"- (x{n}) {raw}")

    noise_bits = []
    for cat in sorted(noisy):
        n = len(by_cat.get(cat, []))
        if n:
            noise_bits.append(f"{cat}: {n}")
    if noise_bits:
        out.append(f"- Suppressed routine lines — {', '.join(noise_bits)} "
                   "(all in ledger.jsonl)")
    if not out:
        out.append("- (no events recorded)")
    return out


def write_briefing(run_dir: Path, month_index: int, briefing: dict) -> tuple[Path, Path]:
    stem = f"briefing-{month_index:03d}"
    json_path = run_dir / f"{stem}.json"
    md_path = run_dir / f"{stem}.md"
    json_path.write_text(json.dumps(briefing, indent=2, ensure_ascii=False))
    md_path.write_text(render_markdown(briefing))
    return json_path, md_path
