"""The steward's governance tools — LLM-facing schemas over `harness.actions`.

This module is the seam between the model's tool calls and the Phase 1 action
vocabulary. The JSON schemas here are what the model sees; `dispatch` validates
arguments, calls the matching `actions.py` verb against the live fort, and
returns a human-readable result string (or a clear error). Invalid orders are
returned as errors — never raised — so a bad call costs the steward a turn, not
the run.

We expose the verbs that are implemented and usable from a briefing alone:
`set_order`, `assign_labor`, `dig_blueprint`, and `pass_turn`. The Phase 1
stubs (build, draft_squad, trade, …) are deliberately left out of the
vocabulary until they are implemented — exposing a verb that always errors only
wastes the steward's attention.
"""

from __future__ import annotations

from pathlib import Path

from harness import actions
from harness.dfhack_client import DFError

REPO_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_DIR = REPO_ROOT / "config" / "blueprints"

# Curated hints surfaced in the schemas so the model issues valid orders. These
# are guidance, not hard enums — the underlying DFHack commands accept more.
COMMON_JOBS = [
    "BrewDrink", "PrepareMeal", "ConstructBed", "ConstructTable",
    "ConstructThrone", "ConstructDoor", "ConstructBin", "ConstructBarrel",
    "ConstructBucket", "MakeCharcoal", "SmeltOre", "MakeWeapon", "MakeArmor",
    "ConstructCrafts", "MillPlants", "ProcessPlants", "TanHide", "MakeCloth",
]
COMMON_LABORS = [
    "MINE", "WOODCUTTING", "CARPENTRY", "MASONRY", "PLANT", "BREWER", "COOK",
    "FISH", "HERBALIST", "BUTCHER", "TANNER", "LEATHER", "WEAVER", "CLOTHESMAKER",
    "METAL_CRAFT", "GEM_CUTTING", "HAUL_STONE", "HAUL_WOOD", "HAUL_FOOD",
    "HEALTHCARE", "ANIMALCARE", "FORGE_WEAPON", "FORGE_ARMOR", "WEAVING",
]

PASS_TURN = "pass_turn"


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "set_order",
        "description": (
            "Queue a manager work order: have the fortress produce a quantity "
            "of some item or job. Example: brew 20 drinks, build 4 beds. Use "
            "this to direct production toward what the settlement needs."),
        "parameters": {
            "type": "object",
            "properties": {
                "job": {
                    "type": "string",
                    "description": (
                        "The job/item type to produce (a DF job_type name). "
                        "Common values: " + ", ".join(COMMON_JOBS)),
                },
                "quantity": {
                    "type": "integer",
                    "description": "How many to make (1-100).",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["job", "quantity"],
            "additionalProperties": False,
        },
    },
    {
        "name": "assign_labor",
        "description": (
            "Enable or disable a labor for a specific dwarf, by their id from "
            "the briefing's roster. Use this to put dwarves to work where they "
            "are needed — e.g. assign a second brewer, or pull someone off "
            "hauling to mine."),
        "parameters": {
            "type": "object",
            "properties": {
                "dwarf_id": {
                    "type": "integer",
                    "description": "The dwarf's id, as listed in the briefing.",
                },
                "labor": {
                    "type": "string",
                    "description": (
                        "The labor to toggle (a DF unit_labor name). Common "
                        "values: " + ", ".join(COMMON_LABORS)),
                },
                "enabled": {
                    "type": "boolean",
                    "description": "True to enable the labor, false to disable.",
                },
            },
            "required": ["dwarf_id", "labor", "enabled"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dig_blueprint",
        "description": (
            "Apply a saved quickfort blueprint (digging, building, or zoning) "
            "by name. Blueprints must already exist in the settlement's plan "
            "library; if you are unsure what is available, the tool will list "
            "the names."),
        "parameters": {
            "type": "object",
            "properties": {
                "blueprint": {
                    "type": "string",
                    "description": "The blueprint file name to apply.",
                },
            },
            "required": ["blueprint"],
            "additionalProperties": False,
        },
    },
    {
        "name": PASS_TURN,
        "description": (
            "End your turn for this month, letting it run its course with no "
            "further orders. Call this once you have issued every order you "
            "intend to for the month."),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def tool_names() -> list[str]:
    return [t["name"] for t in TOOL_SCHEMAS]


def _available_blueprints() -> list[str]:
    if not BLUEPRINT_DIR.exists():
        return []
    return sorted(p.name for p in BLUEPRINT_DIR.iterdir() if p.is_file())


def dispatch(client, name: str, arguments: dict) -> tuple[str, bool]:
    """Execute one tool call. Returns (result_text, is_error)."""
    try:
        if name == "set_order":
            job = str(arguments["job"])
            qty = int(arguments["quantity"])
            out = actions.set_order(client, job, qty)
            return (f"Queued work order: {qty}x {job}. {out.strip()}".strip(),
                    False)

        if name == "assign_labor":
            dwarf_id = int(arguments["dwarf_id"])
            labor = str(arguments["labor"]).upper()
            enabled = bool(arguments.get("enabled", True))
            out = actions.assign_labor(client, dwarf_id, labor, enabled)
            return (out.strip() or f"labor {labor} -> {enabled} for {dwarf_id}",
                    False)

        if name == "dig_blueprint":
            bp = str(arguments["blueprint"])
            path = BLUEPRINT_DIR / bp
            if not path.exists():
                avail = _available_blueprints()
                listing = ", ".join(avail) if avail else "(none available)"
                return (f"No blueprint named {bp!r}. Available blueprints: "
                        f"{listing}.", True)
            out = actions.dig_blueprint(client, path)
            return (f"Applied blueprint {bp}. {out.strip()}".strip(), False)

        if name == PASS_TURN:
            return ("Turn passed; the month will run with no further orders.",
                    False)

        return (f"Unknown order {name!r}. Valid orders: "
                f"{', '.join(tool_names())}.", True)

    except KeyError as e:
        return (f"Missing required argument {e} for {name}.", True)
    except (ValueError, TypeError) as e:
        return (f"Invalid argument for {name}: {e}", True)
    except DFError as e:
        return (f"The order could not be carried out: {e}", True)
    except NotImplementedError as e:
        return (f"That capability is not available yet: {e}", True)
