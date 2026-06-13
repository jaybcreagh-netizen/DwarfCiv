"""Expose the Phase-1 governance verbs (`harness/actions.py`) as model tools.

`TOOL_SPECS` are provider-neutral JSON-schema tool definitions; `execute`
dispatches a tool call to the matching `actions.py` function against the live
DFHackClient and returns a structured result. Every failure mode — a stubbed
verb (`NotImplementedError`), bad arguments (`TypeError`), or a DFHack error
(`DFError`) — is caught and returned as an error result so an invalid model
order is fed back as feedback, never a fatal crash.

Tool parameter names are chosen to match the `actions.py` signatures, so the
dispatcher can forward `arguments` as keyword args directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness import actions
from harness.dfhack_client import DFHackClient, DFError


@dataclass
class ToolExecResult:
    ok: bool
    content: str


# Descriptions are prescriptive about *when* to call each verb (recent models
# reach for tools more conservatively; the trigger condition lifts should-call
# rate — see claude-api tool-use guidance).
TOOL_SPECS: list[dict] = [
    {
        "name": "set_order",
        "description": (
            "Queue a manager work order: produce `qty` of something. Use this "
            "to address shortages or build up stores shown in the briefing — "
            "e.g. brew drink when drink is low, prepare meals when food is low, "
            "make beds/doors. `job` is a Dwarf Fortress job-type name such as "
            "BrewDrink, PrepareMeal, ConstructBed, ConstructDoor, ConstructTable, "
            "MakeBarrel, ButcherAnimal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job": {"type": "string", "description": "Job-type name, e.g. BrewDrink"},
                "qty": {"type": "integer", "minimum": 1, "description": "How many to produce"},
            },
            "required": ["job", "qty"],
        },
    },
    {
        "name": "assign_labor",
        "description": (
            "Enable or disable a labor for one dwarf, steering who does what. "
            "Use to put idle hands to needed work or relieve an overworked "
            "dwarf. `dwarf_id` is the integer id shown beside each name in the "
            "briefing roster. `labor` is a labor name such as MINE, PLANT, "
            "BREWER, COOK, MASON, CARPENTER, WOODCUTTER, FISH."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dwarf_id": {"type": "integer", "description": "Dwarf id from the briefing roster"},
                "labor": {"type": "string", "description": "Labor name, e.g. BREWER"},
                "enabled": {"type": "boolean", "default": True,
                            "description": "true to enable the labor, false to disable"},
            },
            "required": ["dwarf_id", "labor"],
        },
    },
    {
        "name": "dig_blueprint",
        "description": (
            "Apply a Quickfort blueprint file (digging/building plan) at the "
            "current embark. `quickfort_file` is a path to a .csv blueprint on "
            "disk. Use only if you have a prepared blueprint; otherwise prefer "
            "set_order and build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "quickfort_file": {"type": "string", "description": "Path to a .csv blueprint"},
            },
            "required": ["quickfort_file"],
        },
    },
    {
        "name": "build",
        "description": (
            "Place a workshop or building. `workshop_type` e.g. Still, Kitchen, "
            "Carpenters, Masons, Farm; `zone` is a short description of where. "
            "(Not yet wired to the game in this phase — will return an error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workshop_type": {"type": "string"},
                "zone": {"type": "string"},
            },
            "required": ["workshop_type", "zone"],
        },
    },
    {
        "name": "draft_squad",
        "description": (
            "Form a military squad from the given dwarves to defend the "
            "fortress. `dwarf_ids` are roster ids. (Not yet wired in this "
            "phase — will return an error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dwarf_ids": {"type": "array", "items": {"type": "integer"}},
                "squad_name": {"type": "string"},
            },
            "required": ["dwarf_ids"],
        },
    },
    {
        "name": "station_squad",
        "description": (
            "Order a squad to a burrow/location to defend it. (Not yet wired in "
            "this phase — will return an error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "squad_id": {"type": "integer"},
                "burrow": {"type": "string"},
            },
            "required": ["squad_id", "burrow"],
        },
    },
    {
        "name": "trade",
        "description": (
            "Trade with a caravan at the depot when one is present. "
            "`offer_policy` describes what to offer/seek. (Not yet wired in "
            "this phase — will return an error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"offer_policy": {"type": "string"}},
            "required": ["offer_policy"],
        },
    },
    {
        "name": "make_burrow",
        "description": (
            "Define a named burrow over a cuboid region (x1,y1,z1,x2,y2,z2). "
            "(Not yet wired in this phase — will return an error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "array", "items": {"type": "integer"},
                         "minItems": 6, "maxItems": 6},
            },
            "required": ["name", "area"],
        },
    },
    {
        "name": "set_alert",
        "description": (
            "Set a civilian alert level (e.g. send everyone to a safe burrow "
            "during a siege). (Not yet wired in this phase — will return an "
            "error.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "string"}},
            "required": ["level"],
        },
    },
    {
        "name": "pass_turn",
        "description": (
            "End your turn for this month and let time pass. Call this once you "
            "have issued all the orders you intend to this month (or if you "
            "judge no action is needed)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

VALID_NAMES = {t["name"] for t in TOOL_SPECS}


def execute(client: DFHackClient, name: str, arguments: dict) -> ToolExecResult:
    """Dispatch one tool call against the live game. Never raises for an
    invalid order — returns an error result the model can read and react to."""
    if name not in actions.ACTIONS:
        return ToolExecResult(False, f"Unknown action {name!r}. Valid actions: "
                              f"{', '.join(sorted(VALID_NAMES))}.")
    if name == "pass_turn":
        return ToolExecResult(True, "Turn ended; time will now pass.")

    fn = actions.ACTIONS[name]
    args = dict(arguments or {})
    # `area` arrives as a list from JSON; actions.make_burrow expects a tuple.
    if name == "make_burrow" and isinstance(args.get("area"), list):
        args["area"] = tuple(args["area"])
    try:
        result = fn(client, **args)
        msg = (result or "").strip() if isinstance(result, str) else str(result)
        return ToolExecResult(True, msg or f"{name} completed.")
    except NotImplementedError as e:
        return ToolExecResult(False, f"Action {name!r} is not available yet "
                              f"in this phase ({e}). Choose a different action.")
    except TypeError as e:
        return ToolExecResult(False, f"Bad arguments for {name!r}: {e}. "
                              "Check the required parameters and try again.")
    except DFError as e:
        return ToolExecResult(False, f"The order failed in-game: {e}")
    except Exception as e:  # noqa: BLE001 - never let a verb crash the run
        return ToolExecResult(False, f"Unexpected error running {name!r}: {e}")
