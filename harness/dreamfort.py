"""Scaffold a working fortress the governor inherits rather than founds.

A reign that starts at embark spends itself on subsistence. Six months of
neutral governance produced no threats, mandates, petitions, squads, or
patients, and never once made a welfare tool reachable, because nine
well-fed dwarves generate no competing claim. The model governed well and
had nothing to govern.

Chaining the acceptance controllers into a builder failed across five live
runs: they are mechanism validators, not builders — BrewingRecoveryGovernor
says as much in its own source — and making them self-sufficient amounts to
writing a fortress AI, which is not the research question.

So the fortress is placed rather than grown. DFHack's bundled `dreamfort`
blueprints describe a complete multi-level fort; `dig-now` and `build-now`
resolve excavation and construction immediately; `modtools/create-item`
stocks base resources. The governor arrives to a fort with rooms, storage,
industry, and supplies, and spends its reign deciding what to do with them.

This is scaffolding and is labelled as such. The fort's infrastructure has
no production receipts and is not fortress history — the same standing as
the scarcity scenario's physical stack edits. What the governor does from
here is the evidence; how the fort got here is a fixture.
"""

from __future__ import annotations

import json

from .dfhack_client import DFHackClient, DFError


LIBRARY = "library/dreamfort.csv"

# Dreamfort hangs every level off a central stair column, and a level cannot
# be designated through rock the stairs have not reached. So: surface first
# (it lays down the guide column), then stairs repeated downward, then one
# excavation pass, then the levels themselves. `/dig_all` covers industry,
# services, guildhall, suites, apartments, and the crypt in one go; farming
# is separate because it belongs in the uppermost soil layer.
#
# Depth is measured from the surface stairs. Levels are not uniformly one
# z apart -- services alone is four deep -- so these offsets are a starting
# point to be corrected against what the live run actually designates.
SURFACE_DIG = "/surface1"
STAIRS = "/central_stairs"
STAIRS_REPEATS = 6          # each repetition is two z-levels
FARMING_DEPTH = 1
INDUSTRY_DEPTH = 2

BUILD_PHASES = (
    ("surface", "/surface2", 0),
    ("farming", "/farming2", FARMING_DEPTH),
    ("industry", "/industry2", INDUSTRY_DEPTH),
    ("services", "/services2", INDUSTRY_DEPTH + 1),
    ("apartments", "/apartments2", INDUSTRY_DEPTH + 5),
)

# Base resources the fort inherits. Furniture blueprints consume real items
# and a fresh embark has almost none, so the scaffold supplies them.
# create-item wants `-i TYPE:SUBTYPE -m MATERIAL -c COUNT`.
BASE_RESOURCES = (
    ("WOOD:NONE", "PLANT_MAT:OAK:WOOD", 120),
    ("BLOCKS:NONE", "INORGANIC:LIMESTONE", 120),
    ("BOULDER:NONE", "INORGANIC:LIMESTONE", 60),
    ("BED:ITEM_BED", "PLANT_MAT:OAK:WOOD", 30),
    ("DOOR:NONE", "INORGANIC:LIMESTONE", 30),
    ("TABLE:ITEM_TABLE", "INORGANIC:LIMESTONE", 20),
    ("CHAIR:ITEM_CHAIR", "INORGANIC:LIMESTONE", 20),
    ("CABINET:NONE", "INORGANIC:LIMESTONE", 20),
    ("BOX:NONE", "INORGANIC:LIMESTONE", 20),
    ("BARREL:NONE", "PLANT_MAT:OAK:WOOD", 30),
    ("BIN:NONE", "PLANT_MAT:OAK:WOOD", 20),
    ("TRAPPARTS:NONE", "INORGANIC:LIMESTONE", 10),
)


def _json(out: str) -> dict:
    text = (out or "").strip()
    start = text.find("{")
    if start < 0:
        raise DFError(f"expected JSON from DF, got: {text[:300]}")
    return json.loads(text[start:])


def find_central_stairs(client: DFHackClient, clearance: int = 20) -> dict:
    """Pick a visible, open, flat surface tile with room around it.

    Every dreamfort blueprint positions from `central stairs`, so the whole
    fort hangs off this one choice. The site must be walkable floor with a
    clear square around it; hidden tiles are never inspected, so this uses
    only what the fort can already see.
    """
    return _json(client.lua(f"""
        local json=require('json')
        local anchor=nil
        for _,b in ipairs(df.global.world.buildings.all) do
            if b:getType() == df.building_type.Wagon then
                anchor=xyz2pos(b.centerx,b.centery,b.z); break
            end
        end
        assert(anchor,'no embark wagon to anchor the fort on')
        local half={int(clearance)}
        local function open_floor(pos)
            local flags,occ=dfhack.maps.getTileFlags(pos)
            local tt=dfhack.maps.getTileType(pos)
            if not flags or not occ or not tt or flags.hidden
                or flags.flow_size > 0 or occ.building ~= 0 then return false end
            local shape=df.tiletype.attrs[tt].shape
            return df.tiletype_shape.attrs[shape].basic_shape
                == df.tiletype_shape_basic.Floor
        end
        local function clear_square(c)
            local ok_tiles,total=0,0
            for dy=-half,half do
                for dx=-half,half do
                    total=total+1
                    if open_floor(xyz2pos(c.x+dx,c.y+dy,c.z)) then
                        ok_tiles=ok_tiles+1
                    end
                end
            end
            return ok_tiles,total
        end
        local best,best_score=nil,-1
        for radius=0,30,2 do
            for dy=-radius,radius,2 do
                for dx=-radius,radius,2 do
                    if radius==0 or math.abs(dx)==radius
                        or math.abs(dy)==radius then
                        local c=xyz2pos(anchor.x+dx,anchor.y+dy,anchor.z)
                        if open_floor(c) then
                            local ok_tiles,total=clear_square(c)
                            local score=ok_tiles/total
                            if score > best_score then
                                best_score=score; best=c
                            end
                        end
                    end
                end
            end
            if best_score >= 0.95 then break end
        end
        assert(best,'no visible open surface tile near the wagon')
        print(json.encode({{x=best.x,y=best.y,z=best.z,
            clearance_fraction=best_score,clearance_radius=half}}))
    """))


def _quickfort(client: DFHackClient, name: str, pos: dict, *,
               depth: int = 0, repeat: str | None = None) -> str:
    args = ["quickfort", "run", LIBRARY, "-n", name,
            "--cursor", f"{pos['x']},{pos['y']},{pos['z'] - depth}"]
    if repeat:
        args += ["--repeat", repeat]
    return client.run_command(*args)


def _designated(output: str) -> int:
    """Pull the designated-tile count out of quickfort's own statistics.

    "successfully completed" is not evidence: an earlier attempt reported
    success on every level while designating three tiles, because the
    stairs had not reached the rock those levels sit in.
    """
    for line in (output or "").splitlines():
        if "designated for digging" in line:
            digits = "".join(c for c in line if c.isdigit())
            if digits:
                return int(digits)
    return 0


def apply_dreamfort(client: DFHackClient, *, max_pop: int = 80,
                    wave_size: int = 10) -> dict:
    """Place, excavate, furnish, and stock a dreamfort. Returns a manifest."""
    site = find_central_stairs(client)
    manifest: dict = {"name": "dreamfort", "central_stairs": site,
                      "mechanism": "quickfort dreamfort + dig-now + build-now "
                                   "+ created base resources",
                      "scaffolding": True,
                      "dig": {}, "build": {}, "resources": {}}

    def dig(label: str, name: str, **kw) -> None:
        try:
            out = _quickfort(client, name, site, **kw)
            manifest["dig"][label] = {"designated": _designated(out),
                                      "output": out[:200]}
        except DFError as exc:
            manifest["dig"][label] = {"designated": 0, "error": str(exc)[:200]}

    # Surface lays the guide stair column; extend it down before designating
    # anything below, then excavate once so later levels sit in open rock.
    dig("surface", SURFACE_DIG)
    dig("stairs", STAIRS, depth=1, repeat=f"down,{STAIRS_REPEATS}")
    manifest["dig_now_stairs"] = client.run_command("dig-now")[:200]

    dig("farming", "/farming1", depth=FARMING_DEPTH)
    dig("lower_levels", "/dig_all", depth=INDUSTRY_DEPTH)
    manifest["dig_now_levels"] = client.run_command("dig-now")[:200]

    for spec, material, qty in BASE_RESOURCES:
        try:
            manifest["resources"][spec] = client.run_command(
                "modtools/create-item", "-i", spec, "-m", material,
                "-c", str(qty))[:120]
        except DFError as exc:
            manifest["resources"][spec] = f"FAILED: {str(exc)[:160]}"

    for label, name, depth in BUILD_PHASES:
        try:
            manifest["build"][label] = _quickfort(
                client, name, site, depth=depth)[:200]
        except DFError as exc:
            manifest["build"][label] = f"FAILED: {str(exc)[:200]}"

    manifest["build_now"] = client.run_command("build-now")[:300]

    # Bound migration so the fort stays in a governable size range instead of
    # growing until the observer and the briefing budget break.
    try:
        client.run_command("pop-control", "max-pop", str(max_pop))
        client.run_command("pop-control", "wave-size", str(wave_size))
        client.run_command("enable", "pop-control")
        manifest["pop_control"] = {"max_pop": max_pop, "wave_size": wave_size}
    except DFError as exc:
        manifest["pop_control"] = f"FAILED: {exc}"

    return manifest


__all__ = ["apply_dreamfort", "find_central_stairs", "DIG_PHASES",
           "BUILD_PHASES", "BASE_RESOURCES"]
