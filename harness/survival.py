"""Versioned fortress-survival knowledge and derived operational context.

The model should not have to infer Dwarf Fortress production chains from a
monthly stock line. This module loads a small, auditable handbook, computes
explicit planning metrics, and checks each procedure's observable
preconditions. It does *not* choose values or silently execute a policy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HANDBOOK = REPO_ROOT / "config" / "survival" / "handbook.json"


class HandbookError(ValueError):
    """The versioned survival handbook is malformed."""


def load_handbook(path: str | Path = DEFAULT_HANDBOOK) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandbookError(f"cannot load survival handbook {path}: {exc}") from exc
    required = {"id", "version", "planning_assumptions",
                "standing_principles", "procedures", "sources"}
    missing = required - set(data)
    if missing:
        raise HandbookError(f"survival handbook missing {sorted(missing)}")
    ids = [p.get("id") for p in data["procedures"]]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        raise HandbookError("survival procedure ids must be non-empty and unique")
    return data


def handbook_digest(handbook: dict) -> str:
    canonical = json.dumps(handbook, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_handbook_snapshot(run_dir: str | Path, handbook: dict) -> Path:
    """Freeze the exact knowledge supplied to a governed run for audit."""
    path = Path(run_dir) / "survival-handbook.json"
    payload = dict(handbook)
    payload["sha256"] = handbook_digest(handbook)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def derive_survival_metrics(current: dict, previous: dict | None = None,
                            handbook: dict | None = None) -> dict:
    """Compute transparent, tunable planning estimates from observed state."""
    handbook = handbook or load_handbook()
    assumptions = handbook["planning_assumptions"]
    pop = _number(current.get("population"), 0)
    stocks = current.get("stocks") or {}
    previous_stocks = (previous or {}).get("stocks") or {}

    food = _number(stocks.get("food_total"), 0)
    drink = _number(stocks.get("drink"), 0)
    food_rate = float(assumptions["food_units_per_citizen_month"])
    drink_rate = float(assumptions["drink_units_per_citizen_month"])

    stock_keys = ("food_total", "food", "plants", "raw_fish", "drink",
                  "brewable_plants", "seeds", "wood", "stone", "bars",
                  "available_wood", "empty_barrels")
    deltas = {}
    if previous is not None:
        for key in stock_keys:
            if key in stocks and key in previous_stocks:
                deltas[key] = (_number(stocks.get(key), 0)
                               - _number(previous_stocks.get(key), 0))

    food_runway = _runway(food, pop, food_rate)
    drink_runway = _runway(drink, pop, drink_rate)
    return {
        "population": pop,
        "planning_rates_per_citizen_month": {
            "food": food_rate,
            "drink": drink_rate,
        },
        "stock_per_citizen": {
            "food": _per_capita(food, pop),
            "drink": _per_capita(drink, pop),
            "seeds": _per_capita(_number(stocks.get("seeds"), 0), pop),
        },
        "estimated_runway_months": {
            "food": food_runway,
            "drink": drink_runway,
        },
        "risk": {
            "food": _risk_band(food_runway, assumptions),
            "drink": _risk_band(drink_runway, assumptions),
        },
        "observed_stock_delta": deltas,
        "estimate_warning": assumptions.get("note"),
    }


def assess_procedures(state: dict, handbook: dict | None = None) -> list[dict]:
    """Check handbook procedures against only observable state fields."""
    handbook = handbook or load_handbook()
    assessments = []
    for procedure in handbook["procedures"]:
        checks = [_check_requirement(state, r)
                  for r in procedure.get("requirements") or []]
        if procedure.get("implementation_status") != "available":
            feasibility = "unavailable"
        elif any(c["status"] == "blocked" for c in checks):
            feasibility = "blocked"
        elif any(c["status"] == "unknown" for c in checks):
            feasibility = "unknown"
        else:
            feasibility = "feasible"
        assessments.append({
            "id": procedure["id"],
            "goal": procedure["goal"],
            "horizon": procedure["horizon"],
            "implementation_status": procedure["implementation_status"],
            "action_tool": procedure.get("action_tool"),
            "action_tools": procedure.get("action_tools") or (
                [procedure.get("action_tool")]
                if procedure.get("action_tool") else []),
            "feasibility": feasibility,
            "preconditions": checks,
            "labor_guidance": procedure.get("labor_guidance"),
            "success_evidence": procedure.get("success_evidence"),
            "recovery": procedure.get("recovery"),
        })
    return assessments


def build_operational_context(current: dict, previous: dict | None = None,
                              handbook: dict | None = None) -> dict:
    """Return the compact, structured survival reference given to the model."""
    handbook = handbook or load_handbook()
    metrics = derive_survival_metrics(current, previous, handbook)
    procedures = assess_procedures(current, handbook)
    return {
        "handbook": {
            "id": handbook["id"],
            "version": handbook["version"],
            "sha256": handbook_digest(handbook),
            "purpose": handbook.get("purpose"),
            "epistemic_rule": handbook.get("epistemic_rule"),
            "standing_principles": handbook["standing_principles"],
        },
        "survival_metrics": metrics,
        "urgent_findings": _urgent_findings(current, metrics),
        "procedures": procedures,
        "planner_rules": [
            "Choose only tools that are actually exposed.",
            "Treat feasible as a preflight result, not proof of completion.",
            "Do not repeat a failed or no-effect action unless a relevant "
            "precondition has changed.",
            "Keep immediate, tactical, seasonal, and strategic objectives "
            "distinct in persistent strategy state.",
        ],
    }


def _urgent_findings(state: dict, metrics: dict) -> list[dict]:
    stocks = state.get("stocks") or {}
    operations = state.get("operations") or {}
    workshops = operations.get("completed_workshops") or {}
    findings = []
    if _number(stocks.get("food_total"), 0) <= 0:
        findings.append({"code": "NO_EDIBLE_FOOD",
                         "evidence": "stocks.food_total <= 0"})
        if (_number(stocks.get("raw_fish"), 0) > 0
                and _number(workshops.get("Fishery"), 0) > 0):
            findings.append({
                "code": "RAW_FISH_RECOVERY_AVAILABLE",
                "evidence": "raw fish and a completed fishery are observed",
                "procedure": "process_raw_fish",
            })
    if metrics["risk"]["drink"] in {"critical", "urgent"}:
        findings.append({
            "code": "DRINK_RUNWAY_SHORT",
            "evidence": ("estimated drink runway is "
                         f"{metrics['estimated_runway_months']['drink']} months"),
        })
    if _number(stocks.get("seeds"), 0) < 10:
        findings.append({"code": "SEED_BASE_LOW",
                         "evidence": f"observed seeds={stocks.get('seeds', 0)}"})
    return findings


def _check_requirement(state: dict, requirement: dict) -> dict:
    path = requirement["path"]
    found, observed = _lookup(state, path)
    status = "unknown"
    if found:
        op = requirement.get("op", "gte")
        expected = requirement.get("value")
        if op == "gte":
            passed = _number(observed, float("-inf")) >= expected
        elif op == "gt":
            passed = _number(observed, float("-inf")) > expected
        elif op == "eq":
            passed = observed == expected
        elif op == "nonempty":
            passed = bool(observed)
        else:
            raise HandbookError(f"unsupported requirement operator {op!r}")
        status = "met" if passed else "blocked"
    return {
        "path": path,
        "description": requirement.get("description"),
        "status": status,
        "observed": observed if found else None,
        "operator": requirement.get("op", "gte"),
        "required": requirement.get("value"),
    }


def _lookup(data: dict, path: str) -> tuple[bool, Any]:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _number(value: Any, default: float | int) -> float | int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return default


def _runway(stock: float, population: float, rate: float) -> float | None:
    if population <= 0 or rate <= 0:
        return None
    return round(stock / (population * rate), 2)


def _per_capita(stock: float, population: float) -> float | None:
    if population <= 0:
        return None
    return round(stock / population, 2)


def _risk_band(runway: float | None, assumptions: dict) -> str:
    if runway is None:
        return "unknown"
    bands = assumptions["runway_bands_months"]
    if runway <= bands["critical_at_or_below"]:
        return "critical"
    if runway < bands["urgent_below"]:
        return "urgent"
    if runway < bands["watch_below"]:
        return "watch"
    return "stable"


__all__ = [
    "HandbookError", "load_handbook", "handbook_digest",
    "write_handbook_snapshot", "derive_survival_metrics",
    "assess_procedures", "build_operational_context",
]
