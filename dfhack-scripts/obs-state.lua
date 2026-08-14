-- obs-state <output.json>: dump fortress state for the harness briefing.
-- Every section is pcall-guarded: a struct mismatch in one section must not
-- take down the whole dump (its absence is recorded in state.errors).
--@ module = false

local json = require('json')

-- DF strings are CP437; convert to UTF-8 for the JSON boundary.
local function U(s)
    if type(s) == 'string' then return dfhack.df2utf(s) end
    return s
end

local out_path = ({...})[1]
if not out_path then qerror('usage: obs-state <output.json>') end

local state = {errors = {}}

local function section(name, fn)
    local ok, err = pcall(fn)
    if not ok then
        state.errors[#state.errors + 1] = name .. ': ' .. tostring(err)
    end
end

-- ---- date ------------------------------------------------------------------
local MONTHS = {'Granite', 'Slate', 'Felsite', 'Hematite', 'Malachite',
                'Galena', 'Limestone', 'Sandstone', 'Timber', 'Moonstone',
                'Opal', 'Obsidian'}
local SEASONS = {'spring', 'summer', 'autumn', 'winter'}

section('date', function()
    local year = df.global.cur_year
    local tick = df.global.cur_year_tick
    local month = tick // 33600          -- 0..11, 28 days of 1200 ticks
    local day = (tick % 33600) // 1200 + 1
    state.date = {
        year = year,
        tick_of_year = tick,
        absolute_tick = year * 403200 + tick,
        month = MONTHS[month + 1],
        day = day,
        season = SEASONS[tick // 100800 + 1],
        pretty = ('%d %s, year %d (%s)'):format(day, MONTHS[month + 1], year,
                                                SEASONS[tick // 100800 + 1]),
    }
end)

section('paused', function()
    state.paused = df.global.pause_state
end)

-- ---- fort identity -----------------------------------------------------------
section('fort', function()
    local site = dfhack.world.getCurrentSite()
    state.fort = {
        site_name = site and U(dfhack.translation.translateName(site.name, true)) or nil,
        group_id = df.global.plotinfo.group_id,
        civ_id = df.global.plotinfo.civ_id,
    }
end)

-- ---- population --------------------------------------------------------------
local STRESS_LABEL = {
    [0] = 'miserable', [1] = 'unhappy', [2] = 'displeased',
    [3] = 'content', [4] = 'pleased', [5] = 'happy', [6] = 'ecstatic',
}

section('dwarves', function()
    local list = {}
    for _, u in ipairs(df.global.world.units.active) do
        -- isCitizen() remains true for dead fortress members while their
        -- units are still in world.units.active. Exclude them explicitly so
        -- population, stress alerts, and model-targetable ids describe the
        -- living fort instead of resurrecting recent casualties.
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u) then
            local entry = {
                id = u.id,
                name = U(dfhack.units.getReadableName(u)),
                profession = U(dfhack.units.getProfessionName(u)),
                stress_category = dfhack.units.getStressCategory(u),
                stress_label = STRESS_LABEL[dfhack.units.getStressCategory(u)]
                    or tostring(dfhack.units.getStressCategory(u)),
                adult = dfhack.units.isAdult(u),
                labors = {
                    MINE = u.status.labors[df.unit_labor.MINE],
                    HERBALIST = u.status.labors[df.unit_labor.HERBALIST],
                    CUTWOOD = u.status.labors[df.unit_labor.CUTWOOD],
                    BREWER = u.status.labors[df.unit_labor.BREWER],
                    FISH = u.status.labors[df.unit_labor.FISH],
                    CLEAN_FISH = u.status.labors[df.unit_labor.CLEAN_FISH],
                    PLANT = u.status.labors[df.unit_labor.PLANT],
                    CARPENTER = u.status.labors[df.unit_labor.CARPENTER],
                    WOOD_CRAFT = u.status.labors[df.unit_labor.WOOD_CRAFT],
                },
            }
            if u.mood ~= df.mood_type.None then
                entry.strange_mood = df.mood_type[u.mood]
            end
            local job = u.job.current_job
            entry.current_job = job and U(dfhack.job.getName(job)) or nil
            list[#list + 1] = entry
        end
    end
    state.dwarves = list
    state.population = #list
end)

section('idlers', function()
    local n = 0
    for _, u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u)
            and u.job.current_job == nil and u.military.squad_id == -1 then
            n = n + 1
        end
    end
    state.idle_adults = n
end)

-- ---- stocks ------------------------------------------------------------------
section('stocks', function()
    local it = df.item_type
    local buckets = {
        food = {[it.MEAT] = true, [it.FISH] = true, [it.CHEESE] = true,
                [it.EGG] = true, [it.FOOD] = true},
        plants = {[it.PLANT] = true, [it.PLANT_GROWTH] = true},
        drink = {[it.DRINK] = true},
        raw_fish = {[it.FISH_RAW] = true},
        seeds = {[it.SEEDS] = true},
        wood = {[it.WOOD] = true},
        stone = {[it.BOULDER] = true},
        bars = {[it.BAR] = true},
    }
    local counts = {food = 0, plants = 0, brewable_plants = 0,
                    available_brewable_plants = 0,
                    drink = 0, raw_fish = 0, available_raw_fish = 0,
                    seeds = 0, wood = 0, stone = 0, bars = 0,
                    available_wood = 0,
                    empty_barrels = 0, empty_food_containers = 0}
    local tracked_ids = {barrels={}, drinks={}, raw_fish={}, prepared_fish={}}
    for _, item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f = item.flags
        if not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile) then
            local t = item:getType()
            for bucket, types in pairs(buckets) do
                if types[t] then
                    counts[bucket] = counts[bucket]
                        + math.max(1, item:getStackSize())
                end
            end
            if t == it.WOOD and not f.in_job and not f.in_building then
                counts.available_wood = counts.available_wood
                    + math.max(1, item:getStackSize())
            end
            if t == it.PLANT then
                local raw = df.global.world.raws.plants.all[
                    item:getMaterialIndex()]
                if raw and raw.flags.DRINK then
                    counts.brewable_plants = counts.brewable_plants
                        + math.max(1, item:getStackSize())
                    if not f.in_job and not f.in_building then
                        counts.available_brewable_plants =
                            counts.available_brewable_plants
                            + math.max(1, item:getStackSize())
                    end
                end
            elseif t == it.FISH_RAW and not f.in_job and not f.in_building then
                counts.available_raw_fish = counts.available_raw_fish
                    + math.max(1, item:getStackSize())
            end
            if t == it.BARREL and f.container
                and #dfhack.items.getContainedItems(item) == 0 then
                counts.empty_barrels = counts.empty_barrels + 1
            end
            if t == it.BARREL then
                tracked_ids.barrels[#tracked_ids.barrels+1] = item.id
            elseif t == it.DRINK then
                tracked_ids.drinks[#tracked_ids.drinks+1] = item.id
            elseif t == it.FISH_RAW then
                tracked_ids.raw_fish[#tracked_ids.raw_fish+1] = item.id
            elseif t == it.FISH then
                tracked_ids.prepared_fish[#tracked_ids.prepared_fish+1] = item.id
            end
        end
    end
    for _,item in ipairs(df.global.world.items.other.FOOD_STORAGE) do
        local f = item.flags
        if not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile or f.in_job)
            and #dfhack.items.getContainedItems(item) == 0 then
            counts.empty_food_containers = counts.empty_food_containers + 1
        end
    end
    -- edible food = prepared/raw food + plants (rough but stable)
    counts.food_total = counts.food + counts.plants
    for _,ids in pairs(tracked_ids) do table.sort(ids) end
    counts.tracked_item_ids = tracked_ids
    state.stocks = counts
end)

-- ---- current player-visible operations --------------------------------------
section('operations', function()
    local workshops, workshop_details = {}, {}
    local farm_plots, farms, stockpiles, zones = 0, {}, 0, 0
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Workshop then
            local subtype = df.workshop_type[b:getSubtype()]
            local complete = b:getBuildStage() >= b:getMaxBuildStage()
            if complete then
                workshops[subtype] = (workshops[subtype] or 0) + 1
            end
            local construction_job = nil
            if not complete and #b.jobs > 0 then
                local job = b.jobs[0]
                local worker = dfhack.job.getWorker(job)
                construction_job = {
                    id=job.id,
                    type=df.job_type[job.job_type],
                    suspended=job.flags.suspend,
                    high_priority=job.flags.do_now,
                    working=job.flags.working,
                    fetching=job.flags.fetching,
                    worker_id=worker and worker.id or nil,
                    worker_name=worker and U(dfhack.units.getReadableName(worker))
                        or nil,
                }
            end
            local jobs = {}
            for _,job in ipairs(b.jobs) do
                local worker = dfhack.job.getWorker(job)
                jobs[#jobs+1] = {
                    id=job.id,
                    type=job.job_type == df.job_type.CustomReaction
                        and job.reaction_name or df.job_type[job.job_type],
                    order_id=job.order_id,
                    suspended=job.flags.suspend,
                    high_priority=job.flags.do_now,
                    working=job.flags.working,
                    fetching=job.flags.fetching,
                    worker_id=worker and worker.id or nil,
                    worker_name=worker and U(dfhack.units.getReadableName(worker))
                        or nil,
                }
            end
            workshop_details[#workshop_details+1] = {
                id=b.id, subtype=subtype, x=b.centerx, y=b.centery, z=b.z,
                complete=complete, build_stage=b:getBuildStage(),
                max_build_stage=b:getMaxBuildStage(),
                construction_job=construction_job,
                jobs=jobs,
            }
        elseif b:getType() == df.building_type.FarmPlot then
            local complete = b:getBuildStage() >= b:getMaxBuildStage()
            if complete then farm_plots = farm_plots + 1 end
            local flags = dfhack.maps.getTileFlags(
                xyz2pos(b.centerx, b.centery, b.z))
            local crops = {}
            local season_names = {'spring', 'summer', 'autumn', 'winter'}
            for season=0,3 do
                local plant_idx = b.plant_id[season]
                crops[season_names[season+1]] = plant_idx >= 0
                    and df.global.world.raws.plants.all[plant_idx].id or nil
            end
            local construction_job = nil
            if not complete and #b.jobs > 0 then
                local job = b.jobs[0]
                local worker = dfhack.job.getWorker(job)
                construction_job = {
                    id=job.id,
                    type=df.job_type[job.job_type],
                    suspended=job.flags.suspend,
                    high_priority=job.flags.do_now,
                    working=job.flags.working,
                    fetching=job.flags.fetching,
                    worker_id=worker and worker.id or nil,
                    worker_name=worker and U(dfhack.units.getReadableName(worker))
                        or nil,
                }
            end
            farms[#farms+1] = {
                id=b.id, x=b.x1, y=b.y1, z=b.z,
                width=b.x2-b.x1+1, height=b.y2-b.y1+1,
                complete=complete,
                build_stage=b:getBuildStage(),
                max_build_stage=b:getMaxBuildStage(),
                environment=flags and flags.subterranean
                    and 'subterranean' or 'surface',
                crops=crops,
                construction_job=construction_job,
            }
        elseif b:getType() == df.building_type.Stockpile then
            stockpiles = stockpiles + 1
        elseif df.building_civzonest:is_instance(b) then
            zones = zones + 1
        end
    end
    local orders = {}
    for _,o in ipairs(df.global.world.manager_orders.all) do
        orders[#orders+1] = {
            id=o.id,
            job=o.job_type == df.job_type.CustomReaction
                and o.reaction_name or df.job_type[o.job_type],
            amount_left=o.amount_left,
            amount_total=o.amount_total,
            validated=o.status.validated,
            active=o.status.active,
        }
    end
    local designations = {plants=0, trees=0}
    for _,plant in ipairs(df.global.world.plants.all) do
        local block = dfhack.maps.getTileBlock(plant.pos)
        local des = block and
            block.designation[plant.pos.x % 16][plant.pos.y % 16]
        if des and not des.hidden then
            local tt = dfhack.maps.getTileType(plant.pos)
            local attrs = tt and df.tiletype.attrs[tt]
            if attrs and attrs.shape == df.tiletype_shape.SHRUB
                and des.dig == df.tile_dig_designation.Default then
                designations.plants = designations.plants + 1
            elseif attrs and attrs.material == df.tiletype_material.TREE
                and des.dig == df.tile_dig_designation.Default then
                designations.trees = designations.trees + 1
            end
        end
    end
    local manager=dfhack.units.getUnitByNobleRole('manager')
    local manager_state={assigned=manager ~= nil}
    if manager then
        manager_state.unit_id=manager.id
        manager_state.name=U(dfhack.units.getReadableName(manager))
        manager_state.current_job=manager.job.current_job
            and U(dfhack.job.getName(manager.job.current_job)) or nil
    end
    local work_details={}
    local observed_labors={'MINE','CUTWOOD','FISH','CLEAN_FISH','PLANT',
        'BREWER','CARPENTER','WOOD_CRAFT'}
    for idx,wd in ipairs(df.global.plotinfo.labor_info.work_details) do
        local labors={}
        for _,name in ipairs(observed_labors) do
            if wd.allowed_labors[df.unit_labor[name]] then
                labors[#labors+1]=name
            end
        end
        if #labors > 0 then
            local assigned={}
            for _,id in ipairs(wd.assigned_units) do assigned[#assigned+1]=id end
            work_details[#work_details+1]={index=idx,name=U(wd.name),
                mode=df.work_detail_mode[wd.flags.mode],labors=labors,
                assigned_unit_ids=assigned}
        end
    end
    state.operations = {completed_workshops=workshops,
                        workshops=workshop_details,
                        completed_farm_plots=farm_plots,
                        farm_plots_total=#farms,
                        farms=farms,
                        stockpiles=stockpiles,
                        activity_zones=zones,
                        manager=manager_state,
                        manager_orders=orders,
                        active_designations=designations,
                        work_details=work_details}
end)

-- ---- logistics and reachability --------------------------------------------
section('logistics', function()
    local reference = nil
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then reference=u; break end
    end
    local function reachable(pos)
        return not reference or dfhack.maps.canWalkBetween(reference.pos,pos)
    end
    local stockpiles, workshops = {}, {}
    local category_flags = {'animals','food','furniture','coins','corpses',
        'refuse','stone','wood','gems','bars_blocks','cloth','leather','ammo',
        'finished_goods','weapons','armor','sheet'}
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Stockpile then
            local categories={}
            for _,flag in ipairs(category_flags) do
                if b.settings.flags[flag] then categories[#categories+1]=flag end
            end
            local outside_tiles,total_tiles=0,0
            for y=b.y1,b.y2 do
                for x=b.x1,b.x2 do
                    total_tiles=total_tiles+1
                    local flags=dfhack.maps.getTileFlags(xyz2pos(x,y,b.z))
                    if flags and flags.outside then outside_tiles=outside_tiles+1 end
                end
            end
            local contents={item_records=0,drinks=0,plants=0,seeds=0,
                raw_fish=0,prepared_fish=0,food=0,barrels=0,wood=0}
            for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
                local pos=xyz2pos(dfhack.items.getPosition(item))
                if pos.z == b.z and pos.x >= b.x1 and pos.x <= b.x2
                    and pos.y >= b.y1 and pos.y <= b.y2 then
                    contents.item_records=contents.item_records+1
                    local typ=item:getType()
                    local count=math.max(1,item:getStackSize())
                    if typ == df.item_type.DRINK then
                        contents.drinks=contents.drinks+count
                    elseif typ == df.item_type.PLANT
                        or typ == df.item_type.PLANT_GROWTH then
                        contents.plants=contents.plants+count
                    elseif typ == df.item_type.SEEDS then
                        contents.seeds=contents.seeds+count
                    elseif typ == df.item_type.FISH_RAW then
                        contents.raw_fish=contents.raw_fish+count
                    elseif typ == df.item_type.FISH then
                        contents.prepared_fish=contents.prepared_fish+count
                    elseif typ == df.item_type.FOOD then
                        contents.food=contents.food+count
                    elseif typ == df.item_type.BARREL then
                        contents.barrels=contents.barrels+1
                    elseif typ == df.item_type.WOOD then
                        contents.wood=contents.wood+count
                    end
                end
            end
            stockpiles[#stockpiles+1]={
                id=b.id, number=b.stockpile_number, name=U(b.name),
                x=b.x1,y=b.y1,z=b.z,width=b.x2-b.x1+1,
                height=b.y2-b.y1+1,categories=categories,
                max_barrels=b.storage.max_barrels,
                max_bins=b.storage.max_bins,
                max_wheelbarrows=b.storage.max_wheelbarrows,
                outside_tiles=outside_tiles,total_tiles=total_tiles,
                contents=contents,
                reachable=reachable(xyz2pos(b.centerx,b.centery,b.z)),
            }
        elseif b:getType() == df.building_type.Workshop then
            workshops[#workshops+1]={
                id=b.id,subtype=df.workshop_type[b:getSubtype()],
                complete=b:getBuildStage() >= b:getMaxBuildStage(),
                reachable=reachable(xyz2pos(b.centerx,b.centery,b.z)),
            }
        end
    end
    table.sort(stockpiles,function(a,b) return a.id < b.id end)
    table.sort(workshops,function(a,b) return a.id < b.id end)

    local inputs={available_wood={total=0,reachable=0,unreachable=0},
        brewable_plants={total=0,reachable=0,unreachable=0},
        raw_fish={total=0,reachable=0,unreachable=0},
        empty_food_containers={total=0,reachable=0,unreachable=0}}
    local function add_input(bucket,item,count)
        local pos=xyz2pos(dfhack.items.getPosition(item))
        local can_reach=pos.x >= 0 and reachable(pos)
        bucket.total=bucket.total+count
        if can_reach then bucket.reachable=bucket.reachable+count
        else bucket.unreachable=bucket.unreachable+count end
    end
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f=item.flags
        if not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile or f.in_job or f.in_building) then
            local count=math.max(1,item:getStackSize())
            local typ=item:getType()
            if typ == df.item_type.WOOD then
                add_input(inputs.available_wood,item,count)
            elseif typ == df.item_type.PLANT then
                local raw=df.global.world.raws.plants.all[item:getMaterialIndex()]
                if raw and raw.flags.DRINK then
                    add_input(inputs.brewable_plants,item,count)
                end
            elseif typ == df.item_type.FISH_RAW then
                add_input(inputs.raw_fish,item,count)
            end
        end
    end
    for _,item in ipairs(df.global.world.items.other.FOOD_STORAGE) do
        local f=item.flags
        if not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile or f.in_job)
            and #dfhack.items.getContainedItems(item) == 0 then
            add_input(inputs.empty_food_containers,item,1)
        end
    end
    state.logistics={reference_citizen=reference and {
        id=reference.id,name=U(dfhack.units.getReadableName(reference))} or nil,
        stockpiles=stockpiles,workshops=workshops,inputs=inputs}
end)

-- ---- seasonal trade --------------------------------------------------------
section('trade', function()
    local caravan_common=reqscript('internal/caravan/common')
    local reference=nil
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then reference=u; break end
    end
    local function reachable(pos)
        return reference and pos and pos.x >= 0
            and dfhack.maps.canWalkBetween(reference.pos,pos) or false
    end
    local pathable_ok,pathable=pcall(require,'plugins.pathable')
    local wagon_access,animal_access=nil,nil
    if pathable_ok then
        local ok_w,val_w=pcall(pathable.getDepotAccessibleByWagons,true)
        local ok_a,val_a=pcall(pathable.getDepotAccessibleByAnimals)
        if ok_w then wagon_access=val_w end
        if ok_a then animal_access=val_a end
    end

    local function worker_for(job)
        local ok,unit=pcall(dfhack.job.getWorker,job)
        if ok and unit then
            return {id=unit.id,name=U(dfhack.units.getReadableName(unit))}
        end
    end
    local function job_record(job)
        return {id=job.id,type=df.job_type[job.job_type],
            suspended=job.flags.suspend,high_priority=job.flags.do_now,
            worker=worker_for(job)}
    end
    local depots={}
    local import_candidates={}
    local complete_depot=nil
    for _,b in ipairs(df.global.world.buildings.other.TRADE_DEPOT) do
        local construction_job,trader_job=nil,nil
        local jobs={}
        for _,job in ipairs(b.jobs) do
            local rec=job_record(job)
            jobs[#jobs+1]=rec
            if job.job_type == df.job_type.ConstructBuilding then
                construction_job=rec
            elseif job.job_type == df.job_type.TradeAtDepot then
                trader_job=rec
            end
        end
        local at_depot={item_records=0,stack_quantity=0,total_value=0,
                        item_ids={}}
        local merchant_goods={item_records=0,stack_quantity=0,total_value=0,
                              item_ids={}}
        local construction_materials={item_records=0,item_ids={}}
        for _,contained in ipairs(b.contained_items) do
            local item=contained.item
            if item and contained.use_mode == df.building_item_role_type.TEMP then
                local bucket=item.flags.trader and merchant_goods or at_depot
                local qty=math.max(1,item:getStackSize())
                local ok_value,value=pcall(dfhack.items.getValue,item)
                bucket.item_records=bucket.item_records+1
                bucket.stack_quantity=bucket.stack_quantity+qty
                bucket.total_value=bucket.total_value+
                    (ok_value and value or 0)
                if #bucket.item_ids < 40 then
                    bucket.item_ids[#bucket.item_ids+1]=item.id
                end
                if item.flags.trader then
                    local typ=item:getType()
                    local desc=U(dfhack.items.getReadableDescription(item))
                    local lower=desc:lower()
                    local role=nil
                    if typ == df.item_type.DRINK then role='drink'
                    elseif typ == df.item_type.SEEDS then role='seed'
                    elseif typ == df.item_type.MEAT
                        or typ == df.item_type.FISH
                        or typ == df.item_type.CHEESE
                        or typ == df.item_type.EGG
                        or typ == df.item_type.FOOD
                        or typ == df.item_type.PLANT
                        or typ == df.item_type.PLANT_GROWTH then
                        role='food'
                    elseif typ == df.item_type.BUCKET then
                        role='medical_water_container'
                    elseif typ == df.item_type.THREAD then
                        role='medical_thread'
                    elseif typ == df.item_type.CLOTH then
                        role='medical_cloth'
                    elseif lower:find('soap',1,true) then
                        role='medical_soap'
                    elseif lower:find('plaster',1,true) then
                        role='medical_plaster'
                    elseif item:isFoodStorage() then
                        role='food_or_drink_container'
                    end
                    if role then
                        import_candidates[#import_candidates+1]={
                            id=item.id,type=df.item_type[typ],
                            subtype=item:getSubtype(),description=desc,
                            stack_size=qty,value=ok_value and value or nil,
                            survival_role=role,depot_id=b.id,
                        }
                    end
                end
            elseif item then
                construction_materials.item_records=
                    construction_materials.item_records+1
                construction_materials.item_ids[
                    #construction_materials.item_ids+1]=item.id
            end
        end
        local complete=b:getBuildStage() >= b:getMaxBuildStage()
        if complete and not complete_depot then complete_depot=b end
        local trader_mode='none'
        if b.trade_flags.whole == 1 then trader_mode='broker'
        elseif b.trade_flags.whole == 3 then trader_mode='anyone'
        elseif b.trade_flags.whole ~= 0 then
            trader_mode='unknown:'..tostring(b.trade_flags.whole)
        end
        depots[#depots+1]={
            id=b.id,x=b.x1,y=b.y1,z=b.z,width=b.x2-b.x1+1,
            height=b.y2-b.y1+1,build_stage=b:getBuildStage(),
            max_build_stage=b:getMaxBuildStage(),complete=complete,
            citizen_reachable=reachable(xyz2pos(b.centerx,b.centery,b.z)),
            wagon_access_global=complete and wagon_access or nil,
            pack_animal_access_global=complete and animal_access or nil,
            trader_requested=b.trade_flags.trader_requested,
            trader_request_mode=trader_mode,
            construction_job=construction_job,trader_job=trader_job,
            jobs=jobs,goods_at_depot=at_depot,
            merchant_goods_at_depot=merchant_goods,
            construction_materials=construction_materials,
        }
    end
    table.sort(depots,function(a,b) return a.id < b.id end)
    local import_rank={drink=1,food=2,seed=3,
        food_or_drink_container=4,medical_water_container=5,
        medical_thread=6,medical_cloth=7,medical_soap=8,
        medical_plaster=9}
    table.sort(import_candidates,function(a,b)
        local ar=import_rank[a.survival_role] or 99
        local br=import_rank[b.survival_role] or 99
        if ar ~= br then return ar < br end
        if (a.value or 0) ~= (b.value or 0) then
            return (a.value or 0) < (b.value or 0)
        end
        return a.id < b.id
    end)
    while #import_candidates > 80 do table.remove(import_candidates) end

    local caravans={}
    local active_tree_lovers,active_animal_lovers=false,false
    for idx,car in pairs(df.global.plotinfo.caravans) do
        local entity=df.historical_entity.find(car.entity)
        local entity_name=nil
        if entity then
            entity_name=U(dfhack.translation.translateName(entity.name,true))
        end
        local active=car.time_remaining > 0 and
            (car.trade_state == df.caravan_state.T_trade_state.Approaching
             or car.trade_state == df.caravan_state.T_trade_state.AtDepot)
        local tree_lover=active and caravan_common.is_tree_lover_caravan(car)
            or false
        local animal_lover=active and caravan_common.is_animal_lover_caravan(car)
            or false
        active_tree_lovers=active_tree_lovers or tree_lover
        active_animal_lovers=active_animal_lovers or animal_lover
        caravans[#caravans+1]={index=idx,entity_id=car.entity,
            entity_name=entity_name,
            trade_state=df.caravan_state.T_trade_state[car.trade_state]
                or tostring(car.trade_state),
            time_remaining_ticks=car.time_remaining,
            days_remaining=math.floor(car.time_remaining/120),
            active=active,tree_lover=tree_lover,animal_lover=animal_lover,
            flags={tribute=car.flags.tribute,casualty=car.flags.casualty,
                   hardship=car.flags.hardship,seized=car.flags.seized,
                   offended=car.flags.offended}}
    end
    table.sort(caravans,function(a,b) return a.index < b.index end)

    local safe_types={
        [df.item_type.GOBLET]=true,[df.item_type.TOY]=true,
        [df.item_type.INSTRUMENT]=true,[df.item_type.FIGURINE]=true,
        [df.item_type.AMULET]=true,[df.item_type.SCEPTER]=true,
        [df.item_type.CROWN]=true,[df.item_type.RING]=true,
        [df.item_type.EARRING]=true,[df.item_type.BRACELET]=true,
    }
    local candidates={}
    local marked_jobs=0
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f=item.flags
        local haul=nil
        if f.in_job then
            local ref=dfhack.items.getSpecificRef(item,df.specific_ref_type.JOB)
            if ref and ref.data.job.job_type == df.job_type.BringItemToDepot then
                local job=ref.data.job
                local bref=dfhack.job.getGeneralRef(
                    job,df.general_ref_type.BUILDING_HOLDER)
                haul={job_id=job.id,depot_id=bref and bref.building_id or nil,
                      suspended=job.flags.suspend,
                      worker=worker_for(job)}
                marked_jobs=marked_jobs+1
            end
        end
        local typ=item:getType()
        if safe_types[typ] and not f.trader then
            local pos=xyz2pos(dfhack.items.getPosition(item))
            local holder=dfhack.items.getHolderBuilding(item)
            local at_depot=complete_depot and holder == complete_depot or false
            local mandate_ok=dfhack.items.checkMandates(item)
            local basic_ok=not (f.hostile or f.removed or f.dead_dwarf
                or f.spider_web or f.construction or f.encased or f.murder
                or f.trader or f.owned or f.garbage_collect or f.on_fire
                or f.artifact or f.forbid or f.in_inventory)
            local job_ok=not f.in_job or haul ~= nil
            local building_ok=not f.in_building or at_depot
            local route_ok=at_depot or (complete_depot and reachable(pos)) or false
            local ok_value,value=pcall(dfhack.items.getValue,item)
            local contains_wood=caravan_common.has_wood(item)
            local animal_product=item:isAnimalProduct()
            local caravan_compatible=not (active_tree_lovers and contains_wood)
                and not (active_animal_lovers and animal_product)
            candidates[#candidates+1]={id=item.id,type=df.item_type[typ],
                description=U(dfhack.items.getReadableDescription(item)),
                stack_size=math.max(1,item:getStackSize()),
                value=ok_value and value or nil,mandate_clear=mandate_ok,
                at_depot=at_depot,haul=haul,reachable_to_depot=route_ok,
                contains_wood=contains_wood,animal_product=animal_product,
                caravan_compatible=caravan_compatible,
                eligible=basic_ok and job_ok and building_ok and mandate_ok
                    and complete_depot ~= nil and route_ok
                    and caravan_compatible}
        end
    end
    table.sort(candidates,function(a,b)
        if a.eligible ~= b.eligible then return a.eligible end
        if (a.value or 0) ~= (b.value or 0) then
            return (a.value or 0) > (b.value or 0)
        end
        return a.id < b.id
    end)
    while #candidates > 40 do table.remove(candidates) end

    local broker=dfhack.units.getUnitByNobleRole('broker')
    state.trade={reference_citizen_id=reference and reference.id or nil,
        pathable_plugin_available=pathable_ok,
        wagon_access_global=wagon_access,
        pack_animal_access_global=animal_access,
        depots=depots,caravans=caravans,marked_haul_jobs=marked_jobs,
        active_ethics={tree_lovers=active_tree_lovers,
                       animal_lovers=active_animal_lovers},
        survival_import_candidates=import_candidates,
        safe_export_candidates=candidates,
        broker=broker and {id=broker.id,
            name=U(dfhack.units.getReadableName(broker)),
            current_job=broker.job.current_job and
                U(dfhack.job.getName(broker.job.current_job)) or nil} or nil}
end)

-- ---- healthcare ------------------------------------------------------------
section('healthcare', function()
    local site=dfhack.world.getCurrentSite() or {buildings={}}
    local reference=nil
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u,true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then reference=u; break end
    end
    local function reachable(pos)
        return reference and pos and pos.x >= 0
            and dfhack.maps.canWalkBetween(reference.pos,pos) or false
    end
    local locations={}
    local hospital_zone_ids={}
    for _,loc in ipairs(site.buildings) do
        if df.abstract_building_hospitalst:is_instance(loc)
            and not loc.flags.DOES_NOT_EXIST then
            local zones={}
            for _,id in ipairs(loc.contents.building_ids) do
                local zone=df.building.find(id)
                if zone then
                    hospital_zone_ids[id]=true
                    zones[#zones+1]={id=id,x=zone.x1,y=zone.y1,z=zone.z,
                        width=zone.x2-zone.x1+1,height=zone.y2-zone.y1+1,
                        active=zone.spec_sub_flag.active,
                        reachable=reachable(
                            xyz2pos(zone.centerx,zone.centery,zone.z))}
                end
            end
            local occupations={}
            for _,occ in ipairs(loc.occupations) do
                local unit=df.unit.find(occ.unit_id)
                occupations[#occupations+1]={type=df.occupation_type[occ.type]
                    or tostring(occ.type),unit_id=occ.unit_id,
                    unit_name=unit and U(dfhack.units.getReadableName(unit))
                        or nil,unit_alive=unit and not dfhack.units.isDead(unit)
                        or false}
            end
            locations[#locations+1]={id=loc.id,zones=zones,
                desired={splints=loc.contents.desired_splints,
                    thread=loc.contents.desired_thread,
                    cloth=loc.contents.desired_cloth,
                    crutches=loc.contents.desired_crutches,
                    plaster=loc.contents.desired_powder,
                    buckets=loc.contents.desired_buckets,
                    soap=loc.contents.desired_soap},
                need_more={splints=loc.contents.need_more.splints,
                    thread=loc.contents.need_more.thread,
                    cloth=loc.contents.need_more.cloth,
                    crutches=loc.contents.need_more.crutches,
                    plaster=loc.contents.need_more.powder,
                    buckets=loc.contents.need_more.buckets,
                    soap=loc.contents.need_more.soap},
                occupations=occupations}
        end
    end
    table.sort(locations,function(a,b) return a.id < b.id end)

    local furnishings={beds=0,tables=0,containers=0,traction_benches=0,
        planned={beds=0,tables=0,containers=0,traction_benches=0},records={}}
    for _,b in ipairs(df.global.world.buildings.all) do
        local in_hospital=false
        for zone_id in pairs(hospital_zone_ids) do
            local zone=df.building.find(zone_id)
            if zone and dfhack.buildings.containsTile(
                    zone,b.centerx,b.centery) then
                in_hospital=true; break
            end
        end
        if in_hospital then
            local typ=df.building_type[b:getType()] or tostring(b:getType())
            local logical=typ == 'Bed' and 'beds'
                or typ == 'Table' and 'tables'
                or (typ == 'Box' or typ == 'Container') and 'containers'
                or typ == 'TractionBench' and 'traction_benches' or nil
            if logical then
                local complete=b:getBuildStage() >= b:getMaxBuildStage()
                if complete then furnishings[logical]=furnishings[logical]+1
                else furnishings.planned[logical]=furnishings.planned[logical]+1 end
                local attached={}
                for _,rec in ipairs(b.contained_items) do
                    attached[#attached+1]=rec.item.id
                end
                furnishings.records[#furnishings.records+1]={id=b.id,type=typ,
                    logical_type=logical,complete=complete,
                    build_stage=b:getBuildStage(),
                    max_build_stage=b:getMaxBuildStage(),
                    attached_item_ids=attached}
            end
        end
    end

    local available_furniture={beds={},tables={},containers={},
                               traction_benches={}}
    local item_logical={
        [df.item_type.BED]='beds',[df.item_type.TABLE]='tables',
        [df.item_type.BOX]='containers',
        [df.item_type.TRACTION_BENCH]='traction_benches'}
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local logical=item_logical[item:getType()]
        local f=item.flags
        if logical and not (f.trader or f.forbid or f.rotten or f.hostile
                or f.garbage_collect or f.in_job or f.in_building
                or f.in_inventory) then
            available_furniture[logical][#available_furniture[logical]+1]={
                id=item.id,
                description=U(dfhack.items.getReadableDescription(item)),
                reachable=reachable(xyz2pos(dfhack.items.getPosition(item)))}
        end
    end
    for _,records in pairs(available_furniture) do
        table.sort(records,function(a,b) return a.id < b.id end)
    end

    local supplies={splints=0,thread=0,cloth=0,crutches=0,plaster=0,
                    buckets=0,soap=0}
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f=item.flags
        if not (f.trader or f.forbid or f.rotten or f.hostile
                or f.garbage_collect) then
            local typ=df.item_type[item:getType()] or ''
            local desc=U(dfhack.items.getReadableDescription(item)):lower()
            local count=math.max(1,item:getStackSize())
            if typ == 'SPLINT' then supplies.splints=supplies.splints+count
            elseif typ == 'THREAD' then supplies.thread=supplies.thread+count
            elseif typ == 'CLOTH' then supplies.cloth=supplies.cloth+count
            elseif typ == 'CRUTCH' then supplies.crutches=supplies.crutches+count
            elseif typ == 'BUCKET' then supplies.buckets=supplies.buckets+count
            end
            if desc:find('soap',1,true) then
                supplies.soap=supplies.soap+count
            elseif desc:find('plaster',1,true) then
                supplies.plaster=supplies.plaster+count
            end
        end
    end

    local patients={}
    -- Health-request flag names vary across struct revisions; read each one
    -- defensively so a renamed field degrades to omission, not section loss.
    local request_flags={'needs_recovery','rq_diagnosis','rq_cleaning',
        'rq_surgery','rq_sutures','rq_dressing','rq_traction',
        'rq_immobilize','rq_crutch'}
    for _,unit in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(unit,true) and not dfhack.units.isDead(unit)
            and unit.health and unit.health.flags.needs_healthcare then
            local job=unit.job.current_job
            local requests={}
            for _,name in ipairs(request_flags) do
                local ok,v=pcall(function() return unit.health.flags[name] end)
                if ok and v then requests[#requests+1]=name end
            end
            patients[#patients+1]={id=unit.id,
                name=U(dfhack.units.getReadableName(unit)),
                current_job=job and U(dfhack.job.getName(job)) or nil,
                job_type=job and df.job_type[job.job_type] or nil,
                wound_count=#unit.body.wounds,
                health_requests=requests,
                pos={x=unit.pos.x,y=unit.pos.y,z=unit.pos.z}}
        end
    end

    local doctor_candidates={}
    local medical_skills={'DIAGNOSE','DRESS_WOUNDS','SET_BONE','SUTURE',
                          'SURGERY'}
    local critical_labors={MINE=true,CUTWOOD=true,FISH=true}
    local utils=require('utils')
    local manager=dfhack.units.getUnitByNobleRole('manager')
    local broker=dfhack.units.getUnitByNobleRole('broker')
    for _,unit in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(unit,true) and not dfhack.units.isDead(unit)
            and dfhack.units.isAdult(unit) then
            local skills={}
            local score=0
            for _,name in ipairs(medical_skills) do skills[name]=0 end
            local soul=unit.status.current_soul
            if soul then
                for _,skill in ipairs(soul.skills) do
                    local name=df.job_skill[skill.id]
                    if skills[name] ~= nil then
                        skills[name]=skill.rating
                        score=score+skill.rating
                    end
                end
            end
            local critical_details={}
            for _,detail in ipairs(df.global.plotinfo.labor_info.work_details) do
                if utils.binsearch(detail.assigned_units,unit.id) then
                    for labor in pairs(critical_labors) do
                        if detail.allowed_labors[df.unit_labor[labor]] then
                            critical_details[#critical_details+1]=
                                U(detail.name)..':'..labor
                        end
                    end
                end
            end
            table.sort(critical_details)
            local administrative_role=(manager and manager.id == unit.id)
                or (broker and broker.id == unit.id) or false
            local burden=#critical_details+(administrative_role and 1 or 0)
            doctor_candidates[#doctor_candidates+1]={id=unit.id,
                name=U(dfhack.units.getReadableName(unit)),skills=skills,
                medical_skill_score=score,selection_burden=burden,
                critical_work_details=critical_details,
                administrative_role=administrative_role,
                current_job=unit.job.current_job
                    and U(dfhack.job.getName(unit.job.current_job)) or nil}
        end
    end
    table.sort(doctor_candidates,function(a,b)
        if a.medical_skill_score ~= b.medical_skill_score then
            return a.medical_skill_score > b.medical_skill_score
        end
        if a.selection_burden ~= b.selection_burden then
            return a.selection_burden < b.selection_burden
        end
        return a.id < b.id
    end)

    local medical_types={ApplyCast=true,BringCrutch=true,CleanPatient=true,
        DiagnosePatient=true,DressWound=true,GiveFood=true,GiveWater=true,
        ImmobilizeBreak=true,PlaceInTraction=true,RecoverWounded=true,
        SetBone=true,Surgery=true,Suture=true}
    local medical_jobs={}
    for _,job in utils.listpairs(df.global.world.jobs.list) do
        local typ=df.job_type[job.job_type]
        if medical_types[typ] then
            local worker=dfhack.job.getWorker(job)
            medical_jobs[#medical_jobs+1]={id=job.id,type=typ,
                suspended=job.flags.suspend,working=job.flags.working,
                worker=worker and {id=worker.id,
                    name=U(dfhack.units.getReadableName(worker))} or nil}
        end
    end

    local project=dfhack.persistent.getSiteData('dwarfciv/hospital-room-v1')
    local room_project=nil
    if project and project.room then
        local room=project.room
        local total=room.width*room.height
        local hidden,floors,active,unsafe=0,0,0,0
        for y=room.y1,room.y1+room.height-1 do
            for x=room.x1,room.x1+room.width-1 do
                local pos=xyz2pos(x,y,room.z)
                local flags=dfhack.maps.getTileFlags(pos)
                local tt=dfhack.maps.getTileType(pos)
                if not flags or not tt then unsafe=unsafe+1
                else
                    local block=dfhack.maps.getTileBlock(pos)
                    local des=block and block.designation[x%16][y%16]
                    if des and des.dig ~= df.tile_dig_designation.No then
                        active=active+1
                    end
                    if flags.hidden then
                        hidden=hidden+1
                        goto continue_hospital_tile
                    end
                    if flags.flow_size > 0 then unsafe=unsafe+1 end
                    local shape=df.tiletype.attrs[tt].shape
                    if df.tiletype_shape.attrs[shape].basic_shape
                        == df.tiletype_shape_basic.Floor then floors=floors+1 end
                end
                ::continue_hospital_tile::
            end
        end
        local native_zone_id=project.zone_id
        local native_location_id=project.location_id
        if not native_zone_id or not native_location_id then
            for _,loc in ipairs(locations) do
                for _,zone in ipairs(loc.zones) do
                    if zone.x == room.x1 and zone.y == room.y1
                        and zone.z == room.z and zone.width == room.width
                        and zone.height == room.height then
                        native_zone_id=zone.id
                        native_location_id=loc.id
                        break
                    end
                end
                if native_location_id then break end
            end
        end
        local status='designated'
        if hidden == 0 and floors == total and unsafe == 0 then status='ready'
        elseif hidden > 0 and active == 0 then status='blocked'
        elseif unsafe > 0 then status='unsafe' end
        if native_location_id then status='zoned' end
        local access_tiles={}
        if project.entry then
            local positions={
                xyz2pos(project.entry.x,project.entry.y,project.entry.z),
                xyz2pos(project.entry.x,project.entry.y,project.entry.z-1),
                xyz2pos(project.entry.x+1,project.entry.y,project.entry.z-1),
            }
            for _,pos in ipairs(positions) do
                local flags,occupancy=dfhack.maps.getTileFlags(pos)
                local tt=dfhack.maps.getTileType(pos)
                local block=dfhack.maps.getTileBlock(pos)
                local des=block and block.designation[pos.x%16][pos.y%16]
                access_tiles[#access_tiles+1]={x=pos.x,y=pos.y,z=pos.z,
                    hidden=flags and flags.hidden or nil,
                    tile_type=tt and df.tiletype[tt] or nil,
                    dig=des and df.tile_dig_designation[des.dig] or nil,
                    occupancy=occupancy and occupancy.building or nil}
            end
        end
        local picks={}
        for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
            local desc=U(dfhack.items.getReadableDescription(item))
            if desc:lower():find('pick',1,true) then
                local holder=dfhack.items.getHolderUnit(item)
                picks[#picks+1]={id=item.id,description=desc,
                    forbidden=item.flags.forbid,in_job=item.flags.in_job,
                    in_inventory=item.flags.in_inventory,
                    holder_id=holder and holder.id or nil}
            end
        end
        room_project={version=project.version,status=status,room=room,
            entry=project.entry,zone_id=native_zone_id,
            location_id=native_location_id,
            access_mode=project.access_mode,total_tiles=total,
            hidden_tiles=hidden,floor_tiles=floors,
            active_designations=active,unsafe_tiles=unsafe,
            access_tiles=access_tiles,picks=picks}
    end
    state.healthcare={room_project=room_project,locations=locations,
        furnishings=furnishings,available_furniture=available_furniture,
        supplies=supplies,patients=patients,medical_jobs=medical_jobs,
        doctor_candidates=doctor_candidates}
end)

-- ---- water ------------------------------------------------------------------
-- Clean water is a hospital and survival dependency. Report only visible
-- (unhidden) water tiles: revealing hidden aquifers or caverns here would
-- leak geology the governor is not entitled to know.
section('water', function()
    local reference=nil
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then reference=u; break end
    end
    local function reachable(pos)
        return not reference or dfhack.maps.canWalkBetween(reference.pos,pos)
    end

    local wells={}
    local source_zones={}
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Well then
            wells[#wells+1]={id=b.id,x=b.centerx,y=b.centery,z=b.z,
                complete=b:getBuildStage() >= b:getMaxBuildStage(),
                reachable=reachable(xyz2pos(b.centerx,b.centery,b.z))}
        elseif df.building_civzonest:is_instance(b)
            and b.type == df.civzone_type.WaterSource then
            source_zones[#source_zones+1]={id=b.id,
                x=b.x1,y=b.y1,z=b.z,
                width=b.x2-b.x1+1,height=b.y2-b.y1+1,
                active=b.spec_sub_flag.active}
        end
    end
    table.sort(wells,function(a,b) return a.id < b.id end)
    table.sort(source_zones,function(a,b) return a.id < b.id end)

    -- This DFHack build exposes tile_designation.liquid_type as a boolean
    -- (false = water, true = magma), not the tile_liquid enum. Comparing
    -- against df.tile_liquid.Water matched nothing and reported a dry map
    -- on an embark that actually has water, so accept either form.
    local function is_water(des)
        local lt=des.liquid_type
        if type(lt) == 'boolean' then return not lt end
        return lt == df.tile_liquid.Water
    end

    -- Visible water tiles by quality. Salt and stagnant water are recorded
    -- separately because wound cleaning with stagnant water risks infection.
    local counts={fresh=0,salt=0,stagnant=0}
    local fresh_access={}          -- bounded samples of water tiles with an
    local stagnant_access={}       -- adjacent walkable, reachable floor tile
    local MAX_SAMPLE=12
    local function walkable(pos)
        local tt=dfhack.maps.getTileType(pos)
        if not tt then return false end
        local shape=df.tiletype.attrs[tt].shape
        local basic=df.tiletype_shape.attrs[shape].basic_shape
        return basic == df.tiletype_shape_basic.Floor
            or basic == df.tiletype_shape_basic.Ramp
            or basic == df.tiletype_shape_basic.Stair
    end
    for _,block in ipairs(df.global.world.map.map_blocks) do
        for y=0,15 do
            for x=0,15 do
                local des=block.designation[x][y]
                if des.flow_size > 0 and not des.hidden and is_water(des) then
                    local class='fresh'
                    if des.water_salt then class='salt'
                    elseif des.water_stagnant then class='stagnant' end
                    counts[class]=counts[class]+1
                    local sample=nil
                    if class == 'fresh' and #fresh_access < MAX_SAMPLE then
                        sample=fresh_access
                    elseif class == 'stagnant'
                        and #stagnant_access < MAX_SAMPLE then
                        sample=stagnant_access
                    end
                    if sample then
                        local wx=block.map_pos.x+x
                        local wy=block.map_pos.y+y
                        local wz=block.map_pos.z
                        for _,d in ipairs({{0,-1},{0,1},{-1,0},{1,0}}) do
                            local npos=xyz2pos(wx+d[1],wy+d[2],wz)
                            local nflags=dfhack.maps.getTileFlags(npos)
                            if nflags and not nflags.hidden
                                and nflags.flow_size == 0 and walkable(npos)
                                and reachable(npos) then
                                sample[#sample+1]={
                                    x=wx,y=wy,z=wz,depth=des.flow_size,
                                    adjacent={x=npos.x,y=npos.y,z=npos.z}}
                                break
                            end
                        end
                    end
                end
            end
        end
    end

    -- Well components currently available for construction, by exact ids.
    local components={buckets={},chains={},mechanisms={},blocks={},boulders={}}
    local typemap={[df.item_type.BUCKET]='buckets',
                   [df.item_type.CHAIN]='chains',
                   [df.item_type.TRAPPARTS]='mechanisms',
                   [df.item_type.BLOCKS]='blocks',
                   [df.item_type.BOULDER]='boulders'}
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f=item.flags
        local key=typemap[item:getType()]
        if key and not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile or f.in_job or f.in_building) then
            local bucket=components[key]
            if #bucket < 20 then bucket[#bucket+1]=item.id end
        end
    end
    for _,ids in pairs(components) do table.sort(ids) end

    state.water={wells=wells,source_zones=source_zones,visible_tiles=counts,
        fresh_access_sample=fresh_access,
        stagnant_access_sample=stagnant_access,components=components}
end)

-- ---- agriculture ------------------------------------------------------------
section('agriculture', function()
    local by_plant = {}
    for _,item in ipairs(df.global.world.items.other.SEEDS) do
        local f = item.flags
        if not (f.rotten or f.trader or f.forbid or f.garbage_collect
                or f.hostile or f.in_job) then
            local plant_idx = item:getMaterialIndex()
            by_plant[plant_idx] = (by_plant[plant_idx] or 0)
                + math.max(1, item:getStackSize())
        end
    end
    local seed_types = {}
    for plant_idx,count in pairs(by_plant) do
        local raw = df.global.world.raws.plants.all[plant_idx]
        if raw then
            local seasons = {}
            if raw.flags.SPRING then seasons[#seasons+1] = 'spring' end
            if raw.flags.SUMMER then seasons[#seasons+1] = 'summer' end
            if raw.flags.AUTUMN then seasons[#seasons+1] = 'autumn' end
            if raw.flags.WINTER then seasons[#seasons+1] = 'winter' end
            seed_types[#seed_types+1] = {
                plant_id=raw.id, name=U(raw.name), count=count,
                environment=raw.underground_depth_max > 0
                    and 'subterranean' or 'surface',
                seasons=seasons, grow_duration=raw.growdur,
                brewable=raw.flags.DRINK,
            }
        end
    end
    table.sort(seed_types, function(a,b) return a.plant_id < b.plant_id end)
    local farm_room_project = nil
    local saved_room = dfhack.persistent.getSiteData(
        'dwarfciv/farm-room-v1')
    if saved_room and saved_room.room then
        local room = saved_room.room
        local total = room.width * room.height
        local active_designations, hidden_tiles = 0, 0
        local visible_suitable_tiles = 0
        for y=room.y1,room.y1+room.height-1 do
            for x=room.x1,room.x1+room.width-1 do
                local pos = xyz2pos(x,y,room.z)
                local block = dfhack.maps.getTileBlock(pos)
                local des = block and block.designation[x % 16][y % 16]
                if des and des.dig ~= df.tile_dig_designation.No then
                    active_designations = active_designations + 1
                end
                local flags, occupancy = dfhack.maps.getTileFlags(pos)
                local tt = dfhack.maps.getTileType(pos)
                if flags and flags.hidden then
                    hidden_tiles = hidden_tiles + 1
                elseif flags and occupancy and tt and flags.subterranean
                    and flags.flow_size <= 1 and occupancy.building == 0 then
                    local attrs = df.tiletype.attrs[tt]
                    local basic_floor = df.tiletype_shape.attrs[attrs.shape].basic_shape
                        == df.tiletype_shape_basic.Floor
                    local farm_material = attrs.material == df.tiletype_material.SOIL
                        or attrs.material == df.tiletype_material.GRASS_LIGHT
                        or attrs.material == df.tiletype_material.GRASS_DARK
                        or attrs.material == df.tiletype_material.GRASS_DRY
                        or attrs.material == df.tiletype_material.GRASS_DEAD
                        or attrs.material == df.tiletype_material.PLANT
                    if basic_floor and farm_material then
                        visible_suitable_tiles = visible_suitable_tiles + 1
                    end
                end
            end
        end
        local farm_id = nil
        for _,b in ipairs(df.global.world.buildings.all) do
            if b:getType() == df.building_type.FarmPlot and b.z == room.z
                and b.x1 >= room.x1 and b.x2 < room.x1+room.width
                and b.y1 >= room.y1 and b.y2 < room.y1+room.height then
                farm_id = b.id
                break
            end
        end
        local status = 'blocked'
        if farm_id then status = 'farm_built'
        elseif hidden_tiles == 0 and visible_suitable_tiles == total then
            status = 'ready'
        elseif active_designations > 0 then status = 'digging' end
        farm_room_project = {
            version=saved_room.version,
            status=status,
            entry=saved_room.entry,
            room=room,
            total_tiles=total,
            hidden_tiles=hidden_tiles,
            active_designations=active_designations,
            visible_suitable_tiles=visible_suitable_tiles,
            farm_id=farm_id,
        }
    end
    local seed_protection = {available=false, enabled=false, targets={}}
    local ok, sw = pcall(require, 'plugins.seedwatch')
    if ok then
        local watch_map, seed_counts = sw.seedwatch_getData()
        local assigned_plants = {}
        for _,b in ipairs(df.global.world.buildings.all) do
            if b:getType() == df.building_type.FarmPlot then
                for season=0,3 do
                    if b.plant_id[season] >= 0 then
                        assigned_plants[b.plant_id[season]] = true
                    end
                end
            end
        end
        local targets, target_count = {}, 0
        for plant_idx,target in pairs(watch_map) do
            local raw = df.global.world.raws.plants.all[plant_idx]
            if raw and target > 0 then
                target_count = target_count + 1
                local available = seed_counts[plant_idx] or 0
                -- Enabling seedwatch initializes defaults for many crops that
                -- do not exist in this fort. Surface only targets with stock
                -- or a farm assignment so the model is not given a giant,
                -- operationally irrelevant raw list.
                if available > 0 or assigned_plants[plant_idx] then
                    targets[#targets+1] = {
                        plant_id=raw.id,
                        minimum=target,
                        available_seeds=available,
                        assigned_to_farm=assigned_plants[plant_idx] or false,
                    }
                end
            end
        end
        table.sort(targets, function(a,b) return a.plant_id < b.plant_id end)
        seed_protection = {available=true, enabled=sw.isEnabled(),
                           target_count=target_count, targets=targets}
    end
    state.agriculture = {available_seed_types=seed_types,
                         seed_protection=seed_protection,
                         farm_room_project=farm_room_project}
end)

-- ---- threats -----------------------------------------------------------------
section('threats', function()
    local visible_dangers = {}
    for _, u in ipairs(df.global.world.units.active) do
        local tile_hidden = true
        if dfhack.maps.isValidTilePos(u.pos) then
            local block = dfhack.maps.getTileBlock(u.pos)
            if block then
                tile_hidden = block.designation[u.pos.x % 16]
                                               [u.pos.y % 16].hidden
            end
        end
        -- The governor may only learn what an ordinary player could see.
        -- isDanger() includes unrevealed cavern/underworld creatures, which
        -- previously leaked their names into the prompt.
        if dfhack.units.isActive(u) and dfhack.units.isDanger(u)
            and not dfhack.units.isDead(u)
            and not dfhack.units.isHidden(u) and not tile_hidden then
            visible_dangers[#visible_dangers + 1] = {
                id = u.id,
                name = U(dfhack.units.getReadableName(u)),
                invader = dfhack.units.isInvader(u),
                great_danger = dfhack.units.isGreatDanger(u),
                visibility = 'visible',
            }
        end
    end
    state.threats = {
        -- Keep the old key for briefing compatibility, but its semantics are
        -- now explicitly the player-visible dangerous-unit set.
        hostiles = visible_dangers,
        visible_dangers = visible_dangers,
        siege_active = #df.global.plotinfo.invasions.list > 0 and (function()
            for _, inv in ipairs(df.global.plotinfo.invasions.list) do
                if inv.flags.siege and inv.flags.active then return true end
            end
            return false
        end)() or false,
    }
end)

-- ---- military ----------------------------------------------------------------
section('squads', function()
    local squads = {}
    local fort = df.historical_entity.find(df.global.plotinfo.group_id)
    if fort then
        for _, squad_id in ipairs(fort.squads) do
            local sq = df.squad.find(squad_id)
            if sq then
                local members = 0
                for _, pos in ipairs(sq.positions) do
                    if pos.occupant ~= -1 then members = members + 1 end
                end
                squads[#squads + 1] = {
                    id = squad_id,
                    name = U(dfhack.translation.translateName(sq.name, true)),
                    alias = U(sq.alias),
                    members = members,
                }
            end
        end
    end
    state.squads = squads
end)

-- ---- pending matters -----------------------------------------------------------
section('mandates', function()
    -- `df.mandate.T_mode` does not exist in this build, and indexing it
    -- killed the whole section. That went unnoticed while the fort had no
    -- nobility: the section only mattered from the month a queen declared
    -- herself, which is exactly when it stopped reporting. Read each field
    -- defensively so a renamed type costs one field, not the mandates.
    local function enum_name(value, enum)
        if enum and enum[value] ~= nil then return enum[value] end
        return tostring(value)
    end
    local mandates = {}
    for _, m in ipairs(df.global.world.mandates.all) do
        local unit = m.unit
        mandates[#mandates + 1] = {
            noble = unit and U(dfhack.units.getReadableName(unit)) or 'unknown',
            mode = enum_name(m.mode, df.mandate_mode),
            item_type = enum_name(m.item_type, df.item_type),
            amount_total = m.amount_total,
            timeout_left = m.timeout_limit - m.timeout_counter,
        }
    end
    state.mandates = mandates
end)

section('petitions', function()
    -- Approximate: open agreements that name our fort entity as a party
    -- (petitions to the fort, guild/temple requests, ...).
    local us = df.global.plotinfo.group_id
    local n = 0
    for _, agr in ipairs(df.global.world.agreements.all) do
        local involves_us = false
        for _, party in ipairs(agr.parties) do
            for _, eid in ipairs(party.entity_ids) do
                if eid == us then involves_us = true end
            end
        end
        if involves_us and not agr.flags.convicted_accepted then
            n = n + 1
        end
    end
    state.open_petitions_approx = n
end)

section('jobs', function()
    local n = 0
    local link = df.global.world.jobs.list.next
    while link do
        n = n + 1
        link = link.next
    end
    state.open_jobs = n
end)

-- ---- write -------------------------------------------------------------------
json.encode_file(state, out_path)
print('state written to ' .. out_path)
