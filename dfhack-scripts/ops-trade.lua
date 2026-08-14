-- ops-trade <output.json> <build-depot|prioritize-depot|prioritize-trader|mark-goods> ...
-- Verified, bounded trade-depot operations for the survival harness.
--@ module = false

local json = require('json')

local args = {...}
local out_path, command = args[1], args[2]
if not out_path or not command then
    qerror('usage: ops-trade <output.json> <build-depot|prioritize-depot|prioritize-trader|mark-goods> ...')
end

local function write(value)
    json.encode_file(value, out_path)
end

local function reference_citizen()
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then return u end
    end
end

local function wagon_anchor()
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Wagon then
            return xyz2pos(b.centerx, b.centery, b.z), b.id
        end
    end
    qerror('cannot find embark wagon anchor')
end

local function available_logs(reference)
    local logs = {}
    for _,item in ipairs(df.global.world.items.other.IN_PLAY) do
        local f = item.flags
        if item:getType() == df.item_type.WOOD and not f.forbid
            and not f.rotten and not f.trader and not f.hostile
            and not f.in_job and not f.in_building and not f.in_inventory
            and not f.owned and not f.removed then
            local pos = xyz2pos(dfhack.items.getPosition(item))
            if pos.x >= 0 and (not reference
                or dfhack.maps.canWalkBetween(reference.pos, pos)) then
                logs[#logs+1] = item
            end
        end
    end
    table.sort(logs, function(a,b) return a.id < b.id end)
    return logs
end

local function visible_floor(pos)
    local flags,occ = dfhack.maps.getTileFlags(pos)
    local tt = dfhack.maps.getTileType(pos)
    if not flags or not occ or not tt or flags.hidden
        or flags.flow_size > 0 or occ.building ~= 0 then return false end
    local shape = df.tiletype.attrs[tt].shape
    return df.tiletype_shape.attrs[shape].basic_shape
        == df.tiletype_shape_basic.Floor
end

local function valid_footprint(center)
    for y=center.y-2,center.y+2 do
        for x=center.x-2,center.x+2 do
            if not visible_floor(xyz2pos(x,y,center.z)) then return false end
        end
    end
    return true
end

local function build_depot()
    if #df.global.world.buildings.other.TRADE_DEPOT > 0 then
        local b = df.global.world.buildings.other.TRADE_DEPOT[0]
        write({status='no_effect', effect='trade_depot_already_exists',
               depot_id=b.id, complete=b:getBuildStage() >= b:getMaxBuildStage()})
        return
    end
    local reference = reference_citizen()
    local logs = available_logs(reference)
    if #logs < 3 then
        qerror(('trade depot requires three reachable available logs; found %d')
            :format(#logs))
    end
    local anchor, wagon_id = wagon_anchor()
    local mapx,mapy,_ = dfhack.maps.getTileSize()
    local best,best_score = nil,math.huge
    for y=math.max(2,anchor.y-35),math.min(mapy-3,anchor.y+35) do
        for x=math.max(2,anchor.x-35),math.min(mapx-3,anchor.x+35) do
            local pos=xyz2pos(x,y,anchor.z)
            if valid_footprint(pos)
                and (not reference or dfhack.maps.canWalkBetween(reference.pos,pos)) then
                local dx,dy=x-anchor.x,y-anchor.y
                local score=dx*dx+dy*dy
                if score < best_score then best,best_score=pos,score end
            end
        end
    end
    if not best then
        qerror('no visible reachable 5x5 depot footprint within 35 tiles of the wagon')
    end

    local materials={logs[1],logs[2],logs[3]}
    local b,err=dfhack.buildings.constructBuilding{
        pos=best,type=df.building_type.TradeDepot,items=materials,
        full_rectangle=true}
    if not b then qerror('could not construct trade depot: '..tostring(err)) end
    local job=#b.jobs > 0 and b.jobs[0] or nil
    if not job or job.job_type ~= df.job_type.ConstructBuilding then
        qerror('trade depot has no native construction job')
    end
    local material_ids={materials[1].id,materials[2].id,materials[3].id}
    write({status='applied',effect='trade_depot_designated',depot_id=b.id,
        pos={x=b.centerx,y=b.centery,z=b.z},width=b.x2-b.x1+1,
        height=b.y2-b.y1+1,build_stage=b:getBuildStage(),
        max_build_stage=b:getMaxBuildStage(),material_item_ids=material_ids,
        construction_job_id=job.id,suspended=job.flags.suspend,
        wagon_anchor_id=wagon_id,reference_citizen_id=reference and reference.id or nil,
        citizen_reachable=not reference or dfhack.maps.canWalkBetween(reference.pos,best)})
end

local function prioritize_depot(id)
    local b=df.building.find(id)
    if not b or b:getType() ~= df.building_type.TradeDepot then
        qerror('no trade depot with id '..tostring(id))
    end
    if b:getBuildStage() >= b:getMaxBuildStage() then
        qerror('trade depot is already complete')
    end
    if #b.jobs == 0 then qerror('trade depot has no construction job') end
    local job=b.jobs[0]
    if job.job_type ~= df.job_type.ConstructBuilding then
        qerror('trade depot job is not ConstructBuilding')
    end
    if job.flags.suspend then qerror('trade depot construction is suspended') end
    local before=job.flags.do_now
    job.flags.do_now=true
    write({status=before and 'no_effect' or 'applied',
        effect='trade_depot_construction_priority',depot_id=b.id,
        job_id=job.id,before=before,after=job.flags.do_now,
        suspended=job.flags.suspend})
end

local function prioritize_trader(id)
    local depot=df.building.find(id)
    if not depot or depot:getType() ~= df.building_type.TradeDepot then
        qerror('no trade depot with id '..tostring(id))
    end
    local trader_job=nil
    for _,job in ipairs(depot.jobs) do
        if job.job_type == df.job_type.TradeAtDepot then
            trader_job=job; break
        end
    end
    if not trader_job then qerror('depot has no native TradeAtDepot job') end
    if trader_job.flags.suspend then qerror('TradeAtDepot job is suspended') end
    local before=trader_job.flags.do_now
    trader_job.flags.do_now=true
    local worker=dfhack.job.getWorker(trader_job)
    write({status=before and 'no_effect' or 'applied',
        effect='trader_job_priority',depot_id=depot.id,
        job_id=trader_job.id,before=before,after=trader_job.flags.do_now,
        worker_id=worker and worker.id or nil,
        worker_name=worker and dfhack.df2utf(
            dfhack.units.getReadableName(worker)) or nil})
end

local SAFE_EXPORT_TYPES={
    [df.item_type.GOBLET]=true,[df.item_type.TOY]=true,
    [df.item_type.INSTRUMENT]=true,[df.item_type.FIGURINE]=true,
    [df.item_type.AMULET]=true,[df.item_type.SCEPTER]=true,
    [df.item_type.CROWN]=true,[df.item_type.RING]=true,
    [df.item_type.EARRING]=true,[df.item_type.BRACELET]=true,
}

local function mark_goods(depot_id,ids)
    local depot=df.building.find(depot_id)
    if not depot or depot:getType() ~= df.building_type.TradeDepot then
        qerror('no trade depot with id '..tostring(depot_id))
    end
    if depot:getBuildStage() < depot:getMaxBuildStage() then
        qerror('trade depot is incomplete')
    end
    local pathable=require('plugins.pathable')
    if not pathable.getDepotAccessibleByWagons(true) then
        qerror('completed depot is not accessible by wagons')
    end
    local caravan_common=reqscript('internal/caravan/common')
    local active_caravan=nil
    local tree_lovers,animal_lovers=false,false
    for idx,car in pairs(df.global.plotinfo.caravans) do
        if not car.flags.tribute and car.time_remaining > 0
            and (car.trade_state == df.caravan_state.T_trade_state.Approaching
                or car.trade_state == df.caravan_state.T_trade_state.AtDepot) then
            active_caravan={index=idx,entity_id=car.entity,
                state=df.caravan_state.T_trade_state[car.trade_state],
                days_remaining=math.floor(car.time_remaining/120)}
            tree_lovers=tree_lovers or caravan_common.is_tree_lover_caravan(car)
            animal_lovers=animal_lovers
                or caravan_common.is_animal_lover_caravan(car)
        end
    end
    if not active_caravan then
        qerror('no active caravan exists; native depot-hauling jobs would not persist')
    end
    if #ids < 1 or #ids > 20 then
        qerror('mark-goods requires between one and twenty unique item ids')
    end
    local seen,items={},{}
    for _,id in ipairs(ids) do
        if seen[id] then qerror('duplicate export item id: '..id) end
        seen[id]=true
        local item=df.item.find(id)
        if not item then qerror('no item with id '..id) end
        local f=item.flags
        if not SAFE_EXPORT_TYPES[item:getType()] then
            qerror('item '..id..' is not an allowed nonessential finished-good type')
        end
        if f.hostile or f.removed or f.dead_dwarf or f.spider_web
            or f.construction or f.encased or f.murder or f.trader
            or f.owned or f.garbage_collect or f.on_fire or f.artifact
            or f.in_inventory or f.in_job then
            qerror('item '..id..' is unavailable, owned, artifact, or already assigned')
        end
        if f.in_building and dfhack.items.getHolderBuilding(item) ~= depot then
            qerror('item '..id..' belongs to another building')
        end
        if not dfhack.items.checkMandates(item) then
            qerror('item '..id..' is blocked by a mandate')
        end
        if tree_lovers and caravan_common.has_wood(item) then
            qerror('item '..id..' contains wood rejected by an active caravan')
        end
        if animal_lovers and item:isAnimalProduct() then
            qerror('item '..id..' is an animal product rejected by an active caravan')
        end
        local pos=xyz2pos(dfhack.items.getPosition(item))
        if dfhack.items.getHolderBuilding(item) ~= depot
            and (pos.x < 0 or not dfhack.maps.canWalkBetween(
                pos,xyz2pos(depot.centerx,depot.centery,depot.z))) then
            qerror('item '..id..' cannot path to depot '..depot_id)
        end
        items[#items+1]=item
    end

    local jobs={}
    for _,item in ipairs(items) do
        item.flags.forbid=false
        if dfhack.items.getHolderBuilding(item) == depot then
            item.flags.in_building=true
        else
            dfhack.items.markForTrade(item,depot)
        end
        local ref=dfhack.items.getSpecificRef(item,df.specific_ref_type.JOB)
        if ref and ref.data.job.job_type == df.job_type.BringItemToDepot then
            local job=ref.data.job
            local bref=dfhack.job.getGeneralRef(
                job,df.general_ref_type.BUILDING_HOLDER)
            if not bref or bref.building_id ~= depot.id then
                qerror('item '..item.id..' hauling job targets the wrong depot')
            end
            jobs[#jobs+1]={item_id=item.id,job_id=job.id,
                depot_id=bref.building_id,suspended=job.flags.suspend}
        elseif dfhack.items.getHolderBuilding(item) == depot then
            jobs[#jobs+1]={item_id=item.id,depot_id=depot.id,
                already_at_depot=true}
        else
            qerror('item '..item.id..' has no verified BringItemToDepot job')
        end
    end
    write({status='applied',effect='goods_marked_for_depot',
        depot_id=depot.id,item_ids=ids,haul_jobs=jobs,
        wagon_access=true,caravan=active_caravan,
        ethics_checked={tree_lovers=tree_lovers,
                        animal_lovers=animal_lovers}})
end

if command == 'build-depot' then
    build_depot()
elseif command == 'prioritize-depot' then
    local id=tonumber(args[3])
    if not id or id < 0 then qerror('depot id must be non-negative') end
    prioritize_depot(id)
elseif command == 'prioritize-trader' then
    local id=tonumber(args[3])
    if not id or id < 0 then qerror('depot id must be non-negative') end
    prioritize_trader(id)
elseif command == 'mark-goods' then
    local depot_id=tonumber(args[3])
    if not depot_id or depot_id < 0 then qerror('depot id must be non-negative') end
    local ids={}
    for i=4,#args do
        local id=tonumber(args[i])
        if not id or id < 0 then qerror('item id must be non-negative') end
        ids[#ids+1]=id
    end
    mark_goods(depot_id,ids)
else
    qerror('unsupported ops-trade command: '..tostring(command))
end
