"""Experimental starting conditions applied to a restored fortress save.

The baseline embark has ample supplies and rarely creates a meaningful choice
in a twelve-month reign. The scarcity profile physically reduces consumable
stacks in the run's private save and provides one documented basic still. This
creates pressure plus a real recovery path without hidden forbidden reserves
that a later policy action could silently restore.
"""

from __future__ import annotations

import json

from .dfhack_client import DFHackClient


SCENARIOS = ("baseline", "scarcity")


def _add_completed_workshop(client: DFHackClient, subtype: str,
                            excluded_item_ids: set[int] | None = None) -> dict:
    """Place and complete one documented wooden recovery workshop."""
    if subtype not in {"Still", "Fishery"}:
        raise ValueError(f"unsupported recovery workshop {subtype!r}")
    excluded = excluded_item_ids or set()
    excluded_lua = "{" + ",".join(f"[{int(i)}]=true" for i in excluded) + "}"
    created = json.loads(client.lua(f"""
        local json = require('json')
        local buildings = require('dfhack.buildings')
        local wagon, material = nil, nil
        local excluded = {excluded_lua}
        for _,b in ipairs(df.global.world.buildings.all) do
            if b:getType() == df.building_type.Wagon then wagon=b; break end
        end
        assert(wagon, 'scarcity setup requires the embark wagon')
        for _,i in ipairs(df.global.world.items.other.IN_PLAY) do
            if i:getType() == df.item_type.WOOD and not i.flags.in_job
                and not i.flags.forbid and not excluded[i.id] then
                material=i; break
            end
        end
        assert(material, 'scarcity setup requires another available log')
        local made, last_err = nil, nil
        for radius=4,28 do
            for dx=-radius,radius do
                for _,dy in ipairs({{-radius,radius}}) do
                    local b,err = buildings.constructBuilding{{
                        pos={{x=wagon.centerx+dx,y=wagon.centery+dy,z=wagon.z}},
                        type=df.building_type.Workshop,
                        subtype=df.workshop_type.{subtype},
                        items={{material}}}}
                    if b then made=b; break else last_err=err end
                end
                if made then break end
            end
            if made then break end
        end
        assert(made, 'could not place {subtype}: '..tostring(last_err))
        print(json.encode({{id=made.id,x=made.centerx,y=made.centery,z=made.z,
                           material_item=material.id, subtype={subtype!r}}}))
    """).strip())
    client.run_command("build-now")
    verified = json.loads(client.lua(f"""
        local json = require('json')
        local wanted_id = {int(created['id'])}
        local found = df.building.find(wanted_id)
        assert(found, '{subtype} missing after construction')
        assert(found:getType() == df.building_type.Workshop
               and found:getSubtype() == df.workshop_type.{subtype},
               'recovery workshop has wrong type')
        assert(found:getBuildStage() >= found:getMaxBuildStage(),
               '{subtype} was not completed')
        print(json.encode({{id=found.id, completed=true,
            build_stage=found:getBuildStage(),
            max_build_stage=found:getMaxBuildStage()}}))
    """).strip())
    created.update(verified)
    return created


def apply_scenario(client: DFHackClient, name: str) -> dict:
    if name == "baseline":
        return {"name": name, "mechanism": "unmodified restored embark"}
    if name != "scarcity":
        raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
    return apply_scarcity(client)


def apply_scarcity(client: DFHackClient, *, food_per_citizen: int = 1,
                   drink_per_citizen: int = 1) -> dict:
    """Create low supplies and a minimal brewing path in the private run copy.

    Excess starting consumable stacks are removed and the retained stack is
    shrunk to the exact target. Plant stacks are preferred for the food ration,
    so the governor can choose between eating and brewing them. A completed
    still is placed using one starting log. The pristine archived save is never
    touched; the harness has already restored a per-run copy.
    """
    if food_per_citizen < 0 or drink_per_citizen < 0:
        raise ValueError("per-citizen scarcity targets must be non-negative")
    out = client.lua(f"""
        local json = require('json')
        local pop = 0
        for _,u in ipairs(df.global.world.units.active) do
            if dfhack.units.isCitizen(u, true) then pop = pop + 1 end
        end
        local target = {{food=pop*{int(food_per_citizen)},
                         drink=pop*{int(drink_per_citizen)}}}
        local kept = {{food=0, drink=0}}
        local removed = {{food=0, drink=0}}
        local it = df.item_type
        local food_types = {{
            [it.MEAT]=true, [it.FISH]=true, [it.CHEESE]=true,
            [it.EGG]=true, [it.FOOD]=true, [it.PLANT]=true,
            [it.PLANT_GROWTH]=true,
        }}
        local candidates = {{food={{}}, drink={{}}}}
        for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
            local kind = nil
            local typ = item:getType()
            if typ == it.DRINK then kind = 'drink'
            elseif food_types[typ] then kind = 'food' end
            if kind and not item.flags.rotten and not item.flags.trader
                    and not item.flags.hostile then
                candidates[kind][#candidates[kind]+1] = item
            end
        end
        -- Keep brewable plants before meat/fish so scarcity presents a real
        -- eat-versus-brew choice at month zero.
        table.sort(candidates.food, function(a,b)
            local ap = a:getType() == it.PLANT and 1 or 0
            local bp = b:getType() == it.PLANT and 1 or 0
            return ap > bp
        end)
        for _,kind in ipairs({{'food', 'drink'}}) do
            local to_remove = {{}}
            for _,item in ipairs(candidates[kind]) do
                local n = math.max(1, item:getStackSize())
                local remaining = math.max(0, target[kind] - kept[kind])
                if remaining <= 0 then
                    to_remove[#to_remove+1] = item
                    removed[kind] = removed[kind] + n
                elseif n > remaining then
                    item:setStackSize(remaining)
                    item.flags.forbid = false
                    kept[kind] = kept[kind] + remaining
                    removed[kind] = removed[kind] + n - remaining
                else
                    item.flags.forbid = false
                    kept[kind] = kept[kind] + n
                end
            end
            for _,item in ipairs(to_remove) do dfhack.items.remove(item) end
        end
        print(json.encode({{name='scarcity', population=pop, target=target,
                           available=kept, removed=removed,
                           mechanism='physically reduce consumables'}}))
    """)
    try:
        result = json.loads(out.strip())
    except (json.JSONDecodeError, IndexError):
        result = {"name": "scarcity", "raw_result": out.strip()}
    # Explicit experimental affordances: one production path for drink and
    # one for the raw fish the embark's fisher reliably catches.
    still_meta = _add_completed_workshop(client, "Still")
    fishery_meta = _add_completed_workshop(
        client, "Fishery", {int(still_meta["material_item"])})
    result["recovery_affordance"] = {
        "type": "completed_workshops",
        "buildings": {"Still": still_meta, "Fishery": fishery_meta},
        "mechanism": "two starting logs + constructBuilding + build-now",
    }
    manager = json.loads(client.lua("""
        local json = require('json')
        local entity = df.historical_entity.find(df.global.plotinfo.group_id)
        assert(entity, 'fort entity not found')
        local manager_pos, leader_pos = nil, nil
        for _,pos in ipairs(entity.positions.own) do
            if pos.code == 'MANAGER' then manager_pos = pos.id end
            if pos.code == 'EXPEDITION_LEADER' then leader_pos = pos.id end
        end
        assert(manager_pos, 'fort has no MANAGER position')
        assert(leader_pos, 'fort has no EXPEDITION_LEADER position')
        local manager_assignment, manager_idx, leader_hf = nil, nil, nil
        for idx,assignment in ipairs(entity.positions.assignments) do
            if assignment.position_id == manager_pos then
                manager_assignment, manager_idx = assignment, idx
            elseif assignment.position_id == leader_pos then
                leader_hf = assignment.histfig
            end
        end
        assert(manager_assignment, 'fort has no manager assignment slot')
        assert(leader_hf and leader_hf ~= -1,
               'expedition leader has no historical figure')
        manager_assignment.histfig = leader_hf
        local indexed = false
        for _,assignment in ipairs(
                entity.assignments_by_type.MANAGE_PRODUCTION) do
            if assignment.id == manager_assignment.id then indexed = true end
        end
        if not indexed then
            entity.assignments_by_type.MANAGE_PRODUCTION:insert(
                '#', manager_assignment)
        end
        local figure = df.historical_figure.find(leader_hf)
        assert(figure, 'expedition leader historical figure missing')
        local linked = false
        for _,link in ipairs(figure.entity_links) do
            if df.histfig_entity_link_positionst:is_instance(link)
                and link.entity_id == entity.id
                and link.assignment_id == manager_assignment.id then
                linked = true
            end
        end
        if not linked then
            figure.entity_links:insert('#', {
                new=df.histfig_entity_link_positionst,
                entity_id=entity.id,
                link_strength=100,
                assignment_id=manager_assignment.id,
                assignment_vector_idx=manager_idx,
                start_year=df.global.cur_year,
            })
        end
        local unit_id = -1
        for _,u in ipairs(df.global.world.units.active) do
            if u.hist_figure_id == leader_hf then unit_id = u.id; break end
        end
        print(json.encode({position='MANAGER', position_id=manager_pos,
            assignment_id=manager_assignment.id, histfig=leader_hf,
            unit_id=unit_id, assigned=true,
            manager_index_count=#entity.assignments_by_type.MANAGE_PRODUCTION}))
    """).strip())
    result["recovery_affordance"]["manager"] = manager
    result["recovery_affordance"]["mechanism"] += (
        "; expedition leader assigned to the existing MANAGER office")
    result["food_per_citizen"] = food_per_citizen
    result["drink_per_citizen"] = drink_per_citizen
    return result


__all__ = ["SCENARIOS", "apply_scenario", "apply_scarcity"]
