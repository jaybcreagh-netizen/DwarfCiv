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


SCENARIOS = ("baseline", "scarcity", "injury", "dreamfort")


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
    if name == "injury":
        return apply_injury(client)
    if name == "dreamfort":
        from .dreamfort import apply_dreamfort
        return apply_dreamfort(client)
    if name != "scarcity":
        raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
    return apply_scarcity(client)


def apply_injury(client: DFHackClient, *, drop_height: int = 2) -> dict:
    """Injure exactly one deliberately selected citizen by a bounded fall.

    This is the controlled fixture behind clinical-treatment validation: a
    hospital chain cannot be marked live-verified until a real injured
    patient moves through recovery, diagnosis, and treatment. The mechanism
    is physical — the chosen unit is placed ``drop_height`` z-levels above a
    verified dry, reachable floor tile and takes DF's native fall damage
    when the simulation advances — so wounds, health flags, and medical jobs
    are all produced by the game, never written directly.

    Bounds: exactly one adult citizen; bottleneck roles (miner, woodcutter,
    fisher, manager, broker, doctor occupations) are excluded from
    selection; the height defaults to the minimum that reliably injures
    without lethal intent; the drop site must be reachable by another
    citizen so rescue is physically possible. Severity remains stochastic:
    the first post-fall observation must confirm ``needs_healthcare`` before
    any treatment claim, and a harmless landing is recorded as fixture
    ``no_effect``, not retried blindly.
    """
    if not (1 <= drop_height <= 3):
        raise ValueError("injury fixture drop height must be 1..3 z-levels")
    out = client.lua(f"""
        local json = require('json')
        local utils = require('utils')
        local manager = dfhack.units.getUnitByNobleRole('manager')
        local broker = dfhack.units.getUnitByNobleRole('broker')
        local occupied = {{}}
        for _,occ in ipairs(df.global.world.occupations.all) do
            if occ.unit_id and occ.unit_id ~= -1 then
                occupied[occ.unit_id] = true
            end
        end
        local critical = {{df.unit_labor.MINE, df.unit_labor.CUTWOOD,
                           df.unit_labor.FISH}}
        local function is_bottleneck(u)
            if (manager and manager.id == u.id)
                or (broker and broker.id == u.id)
                or occupied[u.id] then return true end
            for _,detail in ipairs(
                    df.global.plotinfo.labor_info.work_details) do
                if utils.binsearch(detail.assigned_units, u.id) then
                    for _,labor in ipairs(critical) do
                        if detail.allowed_labors[labor] then return true end
                    end
                end
            end
            return false
        end
        local subject, fallback = nil, nil
        for _,u in ipairs(df.global.world.units.active) do
            if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
                and dfhack.units.isAdult(u)
                and u.health and not u.health.flags.needs_healthcare then
                fallback = fallback or u
                if not is_bottleneck(u)
                    and (not subject or u.id < subject.id) then
                    subject = u
                end
            end
        end
        subject = subject or fallback
        assert(subject, 'no eligible healthy adult citizen for the fixture')
        local witness = nil
        for _,u in ipairs(df.global.world.units.active) do
            if u.id ~= subject.id and dfhack.units.isCitizen(u, true)
                and not dfhack.units.isDead(u)
                and dfhack.units.isAdult(u) then witness = u; break end
        end
        assert(witness, 'fixture requires a second citizen able to rescue')
        local anchor = nil
        for _,b in ipairs(df.global.world.buildings.all) do
            if b:getType() == df.building_type.Wagon then
                anchor = xyz2pos(b.centerx, b.centery, b.z); break
            end
        end
        anchor = anchor or witness.pos
        local height = {int(drop_height)}
        local function open_space(pos)
            local flags = dfhack.maps.getTileFlags(pos)
            if not flags or flags.hidden or flags.flow_size > 0 then
                return false
            end
            local tt = dfhack.maps.getTileType(pos)
            local shape = df.tiletype.attrs[tt].shape
            return df.tiletype_shape.attrs[shape].basic_shape
                == df.tiletype_shape_basic.Open
        end
        local function dry_floor(pos)
            local flags, occ = dfhack.maps.getTileFlags(pos)
            if not flags or not occ or flags.hidden
                or flags.flow_size > 0 or occ.building ~= 0 then
                return false
            end
            local tt = dfhack.maps.getTileType(pos)
            local shape = df.tiletype.attrs[tt].shape
            return df.tiletype_shape.attrs[shape].basic_shape
                == df.tiletype_shape_basic.Floor
        end
        local site = nil
        for radius = 3, 25 do
            for dy = -radius, radius do
                for dx = -radius, radius do
                    if math.abs(dx) == radius or math.abs(dy) == radius then
                        local pos = xyz2pos(anchor.x+dx, anchor.y+dy, anchor.z)
                        if dry_floor(pos)
                            and dfhack.maps.canWalkBetween(witness.pos, pos)
                        then
                            local clear = true
                            for dz = 1, height do
                                if not open_space(
                                        xyz2pos(pos.x, pos.y, pos.z+dz)) then
                                    clear = false; break
                                end
                            end
                            if clear then site = pos end
                        end
                    end
                    if site then break end
                end
                if site then break end
            end
            if site then break end
        end
        assert(site, 'no verified dry reachable drop site near the wagon')
        local from = {{x=subject.pos.x, y=subject.pos.y, z=subject.pos.z}}
        local wounds_before = #subject.body.wounds
        local ok = dfhack.units.teleport(
            subject, xyz2pos(site.x, site.y, site.z + height))
        assert(ok, 'teleport into the drop column failed')
        print(json.encode({{name='injury',
            mechanism='bounded native fall: one citizen, verified dry '
                .. 'reachable landing, damage resolved by DF physics',
            subject={{id=subject.id,
                name=dfhack.df2utf(dfhack.units.getReadableName(subject))}},
            from_pos=from,
            drop_site={{x=site.x, y=site.y, z=site.z}},
            drop_height=height,
            wounds_before=wounds_before,
            excluded_roles={{'miner','woodcutter','fisher','manager',
                             'broker','occupation_holder'}},
            postcondition='needs_healthcare must be observed after the '
                .. 'fall before any treatment claim'}}))
    """)
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError:
        return {"name": "injury", "raw_result": out.strip()}


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


__all__ = ["SCENARIOS", "apply_scenario", "apply_scarcity", "apply_injury"]
