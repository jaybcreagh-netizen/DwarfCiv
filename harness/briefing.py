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

from .survival import derive_survival_metrics

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
        "operations": state.get("operations"),
        "logistics": state.get("logistics"),
        "trade": state.get("trade"),
        "healthcare": state.get("healthcare"),
        "water": state.get("water"),
        "agriculture": state.get("agriculture"),
        "threats": state.get("threats"),
        "squads": state.get("squads"),
        "mandates": state.get("mandates"),
        "open_petitions_approx": state.get("open_petitions_approx"),
        "dwarves": state.get("dwarves"),
        "fort": state.get("fort"),
        "state_errors": state.get("errors"),
        "survival": derive_survival_metrics(state, prev_state),
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
    available_wood = stocks.get("available_wood", stocks.get("wood", 0))
    if available_wood < WOOD_FLOOR:
        alert(f"LOW AVAILABLE WOOD: {available_wood}")

    threats = state.get("threats") or {}
    if threats.get("siege_active"):
        alert("SIEGE IN PROGRESS")
    hostiles = threats.get("hostiles") or []
    if hostiles:
        alert(f"{len(hostiles)} visible dangerous creature(s) on the map")

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
        f"brewable plants {stocks.get('brewable_plants')} total/"
        f"{stocks.get('available_brewable_plants')} available, "
        f"unclean fish {stocks.get('raw_fish')} total/"
        f"{stocks.get('available_raw_fish')} available, "
        f"seeds {stocks.get('seeds')}, wood {stocks.get('wood')} total/"
        f"{stocks.get('available_wood')} available, "
        f"stone {stocks.get('stone')}, metal bars {stocks.get('bars')}, "
        f"empty barrels {stocks.get('empty_barrels')}, empty food-storage "
        f"containers {stocks.get('empty_food_containers')}")
    survival = briefing.get("survival") or {}
    runway = survival.get("estimated_runway_months") or {}
    risk = survival.get("risk") or {}
    add(f"- Planning estimate: food runway **{runway.get('food')} months** "
        f"({risk.get('food', 'unknown')}), drink runway "
        f"**{runway.get('drink')} months** "
        f"({risk.get('drink', 'unknown')}); estimates are tunable and must "
        f"be checked against observed stock deltas")
    operations = briefing.get("operations") or {}
    workshops = operations.get("completed_workshops") or {}
    designations = operations.get("active_designations") or {}
    orders = operations.get("manager_orders") or []
    add(f"- Recovery capacity: completed stills "
        f"{workshops.get('Still', 0)}; completed fisheries "
        f"{workshops.get('Fishery', 0)}; active plant-gather designations "
        f"{designations.get('plants', 0)}; completed farm plots "
        f"{operations.get('completed_farm_plots', 0)}")
    add(f"- Production management: {operations.get('manager')}; manager "
        f"orders need an appointed manager before native validation")
    workshop_details = operations.get("workshops") or []
    incomplete_workshops = [w for w in workshop_details
                            if not w.get("complete")]
    if incomplete_workshops:
        add("- Incomplete workshops: " + ", ".join(
            f"id {w.get('id')} {w.get('subtype')} stage "
            f"{w.get('build_stage')}/{w.get('max_build_stage')} "
            f"job={w.get('construction_job')}"
            for w in incomplete_workshops[:8]))
    if orders:
        add("- Manager orders: " + ", ".join(
            f"{o.get('job')} {o.get('amount_left')}/{o.get('amount_total')}"
            for o in orders[:12]))
    else:
        add("- Manager orders: none")
    logistics = briefing.get("logistics") or {}
    stockpile_details = logistics.get("stockpiles") or []
    input_reachability = logistics.get("inputs") or {}
    add(f"- Logistics: {len(stockpile_details)} typed/other stockpiles; "
        f"reachable inputs wood "
        f"{(input_reachability.get('available_wood') or {}).get('reachable')}/"
        f"{(input_reachability.get('available_wood') or {}).get('total')}, "
        f"brewable plants "
        f"{(input_reachability.get('brewable_plants') or {}).get('reachable')}/"
        f"{(input_reachability.get('brewable_plants') or {}).get('total')}, "
        f"raw fish "
        f"{(input_reachability.get('raw_fish') or {}).get('reachable')}/"
        f"{(input_reachability.get('raw_fish') or {}).get('total')}, "
        f"empty containers "
        f"{(input_reachability.get('empty_food_containers') or {}).get('reachable')}/"
        f"{(input_reachability.get('empty_food_containers') or {}).get('total')}")
    if stockpile_details:
        add("- Stockpiles: " + ", ".join(
            f"id {p.get('id')} {p.get('name') or 'unnamed'} "
            f"{p.get('width')}x{p.get('height')} categories="
            f"{'/'.join(p.get('categories') or [])} reachable="
            f"{p.get('reachable')} outside={p.get('outside_tiles')}/"
            f"{p.get('total_tiles')} contents={p.get('contents')}"
            for p in stockpile_details[:12]))
    trade = briefing.get("trade") or {}
    depots = trade.get("depots") or []
    caravans = trade.get("caravans") or []
    export_candidates = trade.get("safe_export_candidates") or []
    import_candidates = trade.get("survival_import_candidates") or []
    eligible_exports = [item for item in export_candidates
                        if item.get("eligible")]
    active_caravans = [car for car in caravans if car.get("active")]
    add(f"- Trade readiness: depots {len(depots)}; global wagon access "
        f"{trade.get('wagon_access_global')}; safe eligible exports "
        f"{len(eligible_exports)}; marked haul jobs "
        f"{trade.get('marked_haul_jobs', 0)}; active caravans "
        f"{len(active_caravans)}; broker {trade.get('broker')}")
    if depots:
        add("- Trade depots: " + ", ".join(
            f"id {d.get('id')} stage {d.get('build_stage')}/"
            f"{d.get('max_build_stage')} complete={d.get('complete')} "
            f"citizen_reachable={d.get('citizen_reachable')} "
            f"wagon_access={d.get('wagon_access_global')} "
            f"trader_requested={d.get('trader_requested')} "
            f"request_mode={d.get('trader_request_mode')} "
            f"goods={d.get('goods_at_depot')} jobs={d.get('jobs')}"
            for d in depots[:4]))
    if eligible_exports:
        add("- Safe export candidates: " + ", ".join(
            f"id {item.get('id')} {item.get('description')} "
            f"value={item.get('value')} at_depot={item.get('at_depot')} "
            f"haul={item.get('haul')}"
            for item in eligible_exports[:12]))
    if import_candidates:
        add("- Survival import candidates: " + ", ".join(
            f"id {item.get('id')} {item.get('description')} "
            f"role={item.get('survival_role')} value={item.get('value')} "
            f"stack={item.get('stack_size')}"
            for item in import_candidates[:16]))
    if active_caravans:
        add("- Active caravans: " + ", ".join(
            f"{car.get('entity_name') or car.get('entity_id')} "
            f"{car.get('trade_state')} days_left={car.get('days_remaining')}"
            for car in active_caravans[:4]))
    healthcare = briefing.get("healthcare") or {}
    hospital_project = healthcare.get("room_project")
    hospital_locations = healthcare.get("locations") or []
    add(f"- Healthcare readiness: locations {len(hospital_locations)}; "
        f"room_project={hospital_project}; furnishings="
        f"{healthcare.get('furnishings')}; supplies="
        f"{healthcare.get('supplies')}; available furniture="
        f"{healthcare.get('available_furniture')}; patients="
        f"{len(healthcare.get('patients') or [])}; active medical jobs="
        f"{len(healthcare.get('medical_jobs') or [])}")
    if hospital_locations:
        add("- Hospitals: " + ", ".join(
            f"location {loc.get('id')} zones={loc.get('zones')} "
            f"occupations={loc.get('occupations')} "
            f"need_more={loc.get('need_more')}"
            for loc in hospital_locations[:4]))
    water = briefing.get("water") or {}
    if water:
        tiles = water.get("visible_tiles") or {}
        components = water.get("components") or {}
        add(f"- Water: visible tiles fresh={tiles.get('fresh', 0)} "
            f"salt={tiles.get('salt', 0)} stagnant={tiles.get('stagnant', 0)}; "
            f"wells={len(water.get('wells') or [])}; reachable fresh-edge "
            f"sample={len(water.get('fresh_access_sample') or [])}; "
            "well components available: " + ", ".join(
                f"{kind}={len(ids)}"
                for kind, ids in sorted(components.items())))
    agriculture = briefing.get("agriculture") or {}
    seed_types = agriculture.get("available_seed_types") or []
    farms = operations.get("farms") or []
    if seed_types:
        add("- Seed types: " + ", ".join(
            f"{s.get('plant_id')}={s.get('count')} "
            f"({s.get('environment')}; {'/'.join(s.get('seasons') or [])})"
            for s in seed_types[:12]))
    else:
        add("- Seed types: none available")
    protection = agriculture.get("seed_protection") or {}
    protection_targets = protection.get("targets") or []
    if protection.get("available"):
        add(f"- Seed protection: enabled={protection.get('enabled')}; "
            f"{protection.get('target_count', len(protection_targets))} "
            "configured, relevant targets: " +
            (", ".join(
                f"{t.get('plant_id')}={t.get('available_seeds')}/"
                f"{t.get('minimum')}"
                for t in protection_targets[:12]) or "no targets"))
    farm_room = agriculture.get("farm_room_project")
    if farm_room:
        add("- Farm-room project: "
            f"{farm_room.get('status')}; room={farm_room.get('room')}; "
            f"hidden={farm_room.get('hidden_tiles')}/"
            f"{farm_room.get('total_tiles')}, active dig designations="
            f"{farm_room.get('active_designations')}, visible suitable="
            f"{farm_room.get('visible_suitable_tiles')}/"
            f"{farm_room.get('total_tiles')}")
    if farms:
        add("- Farms: " + ", ".join(
            f"id {f.get('id')} {f.get('width')}x{f.get('height')} "
            f"{f.get('environment')} stage "
            f"{f.get('build_stage')}/{f.get('max_build_stage')} "
            f"job={f.get('construction_job')} crops={f.get('crops')}"
            for f in farms[:8]))
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
    hostiles = threats.get("visible_dangers") or threats.get("hostiles") or []
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
            add(f"- …and {len(hostiles) - 15} more visible dangers")
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
        enabled_labors = [name for name, enabled in
                          (d.get("labors") or {}).items() if enabled]
        labors = (f" [supported labors: {', '.join(enabled_labors)}]"
                  if enabled_labors else " [supported labors: none]")
        # getReadableName already ends with the profession; don't repeat it.
        name = d.get("name") or "?"
        prof = d.get("profession") or ""
        name_s = name if prof and name.endswith(prof) else f"{name} — {prof}"
        add(f"- id {d.get('id')}: {name_s}{job}{flag_s}{labors}")
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
