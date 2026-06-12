"""Governance action vocabulary (phase-1: partially implemented).

Each verb is a typed function taking the DFHackClient first. Implemented
now: dig_blueprint (quickfort), set_order (workorder), assign_labor,
pass_turn. The rest are stubs that raise NotImplementedError and document
the DFHack command/API to use, so wiring them up later is mechanical.

DF v50 note on labors: the work-details UI is the player-facing labor
system, but unit.status.labors[] is still the authoritative store that
jobs check; work details write into it. We set the flag directly, which
holds unless something later rewrites that unit's work-detail membership.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .dfhack_client import DFHackClient, DFError


# --------------------------------------------------------------------------
# implemented


def dig_blueprint(client: DFHackClient, quickfort_file: str | Path,
                  start_comment: str | None = None) -> str:
    """Apply a quickfort .csv/.xlsx blueprint at the current cursor/start.

    Copies the blueprint into df/dfhack-config/blueprints/ and runs
    `quickfort run <name>` (see DFHack quickfort docs).
    """
    src = Path(quickfort_file)
    if not src.exists():
        raise DFError(f"blueprint not found: {src}")
    dest_dir = client.df_dir / "dfhack-config" / "blueprints"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest_dir / src.name)
    return client.run_command("quickfort", "run", src.name, timeout=120)


def set_order(client: DFHackClient, job: str, qty: int) -> str:
    """Queue a manager work order, e.g. set_order("BrewDrink", 10).

    Uses DFHack's `workorder` script; `job` is a df.job_type name
    (ConstructBed, BrewDrink, PrepareMeal, ...).
    """
    return client.run_command("workorder", job, str(qty))


def assign_labor(client: DFHackClient, dwarf_id: int, labor: str,
                 enabled: bool = True) -> str:
    """Toggle a labor on a unit. `labor` is a df.unit_labor name, e.g.
    MINE, PLANT, BREWER, MASON (without the UNIT_LABOR_ prefix)."""
    return client.lua(f"""
        local u = df.unit.find({dwarf_id})
        assert(u, 'no unit with id {dwarf_id}')
        local labor = df.unit_labor.{labor}
        assert(labor, 'unknown labor: {labor}')
        u.status.labors[labor] = {str(enabled).lower()}
        print(('labor %s -> %s for %s'):format(
            '{labor}', tostring({str(enabled).lower()}),
            dfhack.units.getReadableName(u)))
    """)


def pass_turn(client: DFHackClient) -> str:
    """Explicit no-op: let the month elapse with no intervention."""
    return "pass"


# --------------------------------------------------------------------------
# stubs — to be implemented in the agent phase


def build(client: DFHackClient, workshop_type: str, zone: str):
    """TODO: place a workshop/building.

    Plan: express the building as a one-cell quickfort #build blueprint at
    the zone's anchor and apply via `quickfort run`, which handles material
    selection. (Direct API alternative: dfhack.buildings.constructBuilding.)
    """
    raise NotImplementedError("build: use quickfort #build blueprint or "
                              "dfhack.buildings.constructBuilding")


def draft_squad(client: DFHackClient, dwarf_ids: list[int],
                squad_name: str = ""):
    """TODO: create a military squad from the given dwarves.

    Plan: Lua dfhack.military.makeSquad(assignment_id) + set positions'
    occupants (see DFHack Lua API, dfhack.military.*).
    """
    raise NotImplementedError("draft_squad: dfhack.military.makeSquad + "
                              "position occupant assignment")


def station_squad(client: DFHackClient, squad_id: int, burrow: str):
    """TODO: station/move a squad.

    Plan: write a squad_order_movest onto df.squad.find(id).orders, or
    drive the v50 squad UI; `burrow`-based defense via `gui/civ-alert`.
    """
    raise NotImplementedError("station_squad: squad_order_movest or "
                              "gui/civ-alert")


def trade(client: DFHackClient, offer_policy: str):
    """TODO: trade at the depot when a caravan is present.

    Plan: bring goods with `dfhack.items.markForTrade` (or the `trade`
    overlay's Lua), then drive the trade screen; policy decides margins.
    """
    raise NotImplementedError("trade: items.markForTrade + trade screen "
                              "automation")


def make_burrow(client: DFHackClient, name: str,
                area: tuple[int, int, int, int, int, int]):
    """TODO: define a burrow over (x1,y1,z1)-(x2,y2,z2).

    Plan: `burrow define` / dfhack.burrows.* (setTilesInCuboid), then
    name it.
    """
    raise NotImplementedError("make_burrow: dfhack.burrows.setTilesInCuboid")


def set_alert(client: DFHackClient, level: str):
    """TODO: civilian alert (e.g. 'everyone to the safety burrow').

    Plan: `gui/civ-alert` exposes a Lua API (set_civ_alert) once a burrow
    is registered.
    """
    raise NotImplementedError("set_alert: gui/civ-alert")


ACTIONS = {
    "dig_blueprint": dig_blueprint,
    "build": build,
    "assign_labor": assign_labor,
    "set_order": set_order,
    "draft_squad": draft_squad,
    "station_squad": station_squad,
    "trade": trade,
    "make_burrow": make_burrow,
    "set_alert": set_alert,
    "pass_turn": pass_turn,
}
