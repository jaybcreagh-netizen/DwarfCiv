-- ops-farm <output.json> <prepare|build|assign> ...
-- Verified farm construction and seasonal crop assignment for the harness.
--@ module = false

local json = require('json')
local utils = require('utils')

local args = {...}
local out_path, command = args[1], args[2]
if not out_path or not command then
    qerror('usage: ops-farm <output.json> <prepare|build|assign> ...')
end

local function U(s)
    if type(s) == 'string' then return dfhack.df2utf(s) end
    return s
end

local function write(value)
    json.encode_file(value, out_path)
end

local function anchor_pos()
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Wagon then
            return xyz2pos(b.centerx, b.centery, b.z)
        end
    end
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u) then
            return xyz2pos(u.pos.x, u.pos.y, u.pos.z)
        end
    end
    qerror('cannot find a fortress anchor')
end

local function has_mud(pos)
    local block = dfhack.maps.getTileBlock(pos)
    if not block then return false end
    for _,bev in ipairs(block.block_events) do
        if bev:getType() == df.block_square_event_type.material_spatter
            and bev.mat_type == df.builtin_mats.MUD
            and bev.mat_state == df.matter_state.Solid then
            return true
        end
    end
    return false
end

local function valid_farm_tile(pos, environment)
    local flags, occupancy = dfhack.maps.getTileFlags(pos)
    local tt = dfhack.maps.getTileType(pos)
    if not flags or not occupancy or not tt then return false end
    if flags.hidden or flags.flow_size > 1 or occupancy.building ~= 0 then
        return false
    end
    if environment == 'surface' and flags.subterranean then return false end
    if environment == 'subterranean' and not flags.subterranean then return false end

    local attrs = df.tiletype.attrs[tt]
    local shape, mat = attrs.shape, attrs.material
    local generic_shape = shape == df.tiletype_shape.FLOOR
        or shape == df.tiletype_shape.TWIG
        or shape == df.tiletype_shape.SAPLING
        or shape == df.tiletype_shape.SHRUB
    local dirt = mat == df.tiletype_material.SOIL
        or mat == df.tiletype_material.GRASS_LIGHT
        or mat == df.tiletype_material.GRASS_DARK
        or mat == df.tiletype_material.GRASS_DRY
        or mat == df.tiletype_material.GRASS_DEAD
        or mat == df.tiletype_material.PLANT
    local basic_floor = df.tiletype_shape.attrs[shape].basic_shape
        == df.tiletype_shape_basic.Floor
    return generic_shape and (dirt or (basic_floor and has_mud(pos)))
end

local function valid_rectangle(pos, width, height, environment)
    for y=pos.y,pos.y+height-1 do
        for x=pos.x,pos.x+width-1 do
            if not valid_farm_tile(xyz2pos(x, y, pos.z), environment) then
                return false
            end
        end
    end
    return true
end

local function find_rectangle(width, height, environment)
    local anchor = anchor_pos()
    local mapx, mapy, mapz = dfhack.maps.getTileSize()
    local radius = 80
    local xmin = math.max(0, anchor.x - radius)
    local xmax = math.min(mapx - width, anchor.x + radius)
    local ymin = math.max(0, anchor.y - radius)
    local ymax = math.min(mapy - height, anchor.y + radius)
    local best, best_score = nil, math.huge
    for z=0,mapz-1 do
        for y=ymin,ymax do
            for x=xmin,xmax do
                local pos = xyz2pos(x, y, z)
                if valid_rectangle(pos, width, height, environment) then
                    local dx, dy, dz = x-anchor.x, y-anchor.y, z-anchor.z
                    local score = dx*dx + dy*dy + dz*dz*400
                    if score < best_score then
                        best, best_score = pos, score
                    end
                end
            end
        end
    end
    return best, anchor
end

local FARM_ROOM_KEY = 'dwarfciv/farm-room-v1'

-- Choose only from visible surface facts. In particular, do not inspect the
-- hidden layer below when selecting an entry: doing so would leak geology to
-- the governor and turn a planning tool into map omniscience.
local function valid_surface_entry(pos, room_width, room_height)
    local mapx, mapy, _ = dfhack.maps.getTileSize()
    local half = math.floor(room_height / 2)
    if pos.x < 1 or pos.x + room_width + 2 >= mapx
        or pos.y - half < 1 or pos.y - half + room_height >= mapy
        or pos.z < 1 then
        return false
    end
    local flags, occupancy = dfhack.maps.getTileFlags(pos)
    local tt = dfhack.maps.getTileType(pos)
    if not flags or not occupancy or not tt or flags.hidden
        or flags.subterranean or flags.flow_size > 0
        or occupancy.building ~= 0 then
        return false
    end
    local shape = df.tiletype.attrs[tt].shape
    return df.tiletype_shape.attrs[shape].basic_shape
        == df.tiletype_shape_basic.Floor
end

local function find_surface_entry(room_width, room_height)
    local anchor = anchor_pos()
    local mapx, mapy, _ = dfhack.maps.getTileSize()
    local radius = 35
    local best, best_score = nil, math.huge
    for y=math.max(1, anchor.y-radius),math.min(mapy-2, anchor.y+radius) do
        for x=math.max(1, anchor.x-radius),math.min(mapx-2, anchor.x+radius) do
            local pos = xyz2pos(x, y, anchor.z)
            if valid_surface_entry(pos, room_width, room_height) then
                local dx,dy = x-anchor.x,y-anchor.y
                local score = dx*dx+dy*dy
                if score < best_score then best,best_score=pos,score end
            end
        end
    end
    return best, anchor
end

local function prepare_farm_room(width, height)
    local existing = dfhack.persistent.getSiteData(FARM_ROOM_KEY)
    if existing then
        write({status='no_effect', effect='farm_room_already_registered',
               project=existing})
        return
    end
    local entry, anchor = find_surface_entry(width, height)
    if not entry then
        qerror('no safe visible surface floor exists for a bounded farm-room entry')
    end
    local half = math.floor(height / 2)
    local room = {x1=entry.x+2, y1=entry.y-half, z=entry.z-1,
                  width=width, height=height}
    local data = {[0]={[0]={[0]='j1'}}, [-1]={}}
    data[-1][0] = {[0]='u1', [1]='d1'}
    for y=0,height-1 do
        local ry = y-half
        data[-1][ry] = data[-1][ry] or {}
        for x=0,width-1 do data[-1][ry][x+2] = 'd1' end
    end
    local quickfort = reqscript('quickfort')
    quickfort.apply_blueprint{mode='dig', data=data, pos=entry,
                              dry_run=true}
    quickfort.apply_blueprint{mode='dig', data=data, pos=entry,
                              dry_run=false}
    local designated = 0
    local function count_designation(pos)
        local block = dfhack.maps.getTileBlock(pos)
        local des = block and block.designation[pos.x % 16][pos.y % 16]
        if des and des.dig ~= df.tile_dig_designation.No then
            designated = designated + 1
        end
    end
    count_designation(entry)
    count_designation(xyz2pos(entry.x, entry.y, entry.z-1))
    count_designation(xyz2pos(entry.x+1, entry.y, entry.z-1))
    for y=room.y1,room.y1+height-1 do
        for x=room.x1,room.x1+width-1 do
            count_designation(xyz2pos(x,y,room.z))
        end
    end
    if designated == 0 then qerror('quickfort created no farm-room designations') end
    local project = {version=1, status='designated',
        entry={x=entry.x,y=entry.y,z=entry.z}, room=room}
    dfhack.persistent.saveSiteData(FARM_ROOM_KEY, project)
    write({status='applied', effect='farm_room_designated',
        epistemic_scope='entry selected from visible surface facts only; hidden geology was not inspected',
        project=project, designated_tiles=designated,
        anchor={x=anchor.x,y=anchor.y,z=anchor.z}})
end

local function farm_environment(farm)
    local flags = dfhack.maps.getTileFlags(
        xyz2pos(farm.centerx, farm.centery, farm.z))
    return flags and flags.subterranean and 'subterranean' or 'surface'
end

local SEASONS = {'spring', 'summer', 'autumn', 'winter'}
local SEASON_INDEX = {spring=0, summer=1, autumn=2, winter=3}

local function raw_supports_season(raw, season)
    if season == 'spring' then return raw.flags.SPRING end
    if season == 'summer' then return raw.flags.SUMMER end
    if season == 'autumn' then return raw.flags.AUTUMN end
    if season == 'winter' then return raw.flags.WINTER end
    return false
end

local function seed_count(plant_idx)
    local count = 0
    for _,item in ipairs(df.global.world.items.other.SEEDS) do
        local f = item.flags
        if item:getMaterialIndex() == plant_idx and not f.forbid
            and not f.rotten and not f.trader and not f.hostile
            and not f.in_job then
            count = count + math.max(1, item:getStackSize())
        end
    end
    return count
end

if command == 'prepare' then
    local width, height = tonumber(args[3]), tonumber(args[4])
    if not width or not height or width < 3 or height < 3
        or width > 9 or height > 9 then
        qerror('farm room dimensions must be integers between 3 and 9')
    end
    prepare_farm_room(width, height)
elseif command == 'build' then
    local environment = args[3]
    local width, height = tonumber(args[4]), tonumber(args[5])
    if environment ~= 'surface' and environment ~= 'subterranean' then
        qerror('farm environment must be surface or subterranean')
    end
    if not width or not height or width < 1 or height < 1
        or width > 10 or height > 10 then
        qerror('farm dimensions must be integers between 1 and 10')
    end
    local pos, anchor = find_rectangle(width, height, environment)
    if not pos then
        qerror('no visible ' .. environment .. ' ' .. width .. 'x' .. height ..
               ' soil/mud rectangle exists within 80 tiles of the fort')
    end
    local before = {}
    for _,b in ipairs(df.global.world.buildings.all) do before[b.id] = true end
    local quickfort = reqscript('quickfort')
    local stats = quickfort.apply_blueprint{
        mode='build', data=('p(%dx%d)'):format(width, height), pos=pos,
        dry_run=false,
    }
    local created = nil
    for _,b in ipairs(df.global.world.buildings.all) do
        if not before[b.id] and b:getType() == df.building_type.FarmPlot then
            created = b
            break
        end
    end
    if not created then qerror('quickfort created no farm plot') end
    write({status='applied', effect='farm_plot_designated',
        farm_id=created.id, environment=environment,
        pos={x=created.x1, y=created.y1, z=created.z},
        width=created.x2-created.x1+1, height=created.y2-created.y1+1,
        build_stage=created:getBuildStage(),
        max_build_stage=created:getMaxBuildStage(),
        anchor={x=anchor.x, y=anchor.y, z=anchor.z}})
elseif command == 'assign' then
    local farm_id, crop_id = tonumber(args[3]), args[4]
    local requested = {}
    for season in string.gmatch(args[5] or '', '[^,]+') do
        season = season:lower()
        if SEASON_INDEX[season] == nil then qerror('unknown season: ' .. season) end
        requested[#requested+1] = season
    end
    if not farm_id or not crop_id or #requested == 0 then
        qerror('assign requires farm id, crop id, and seasons')
    end
    local farm = df.building.find(farm_id)
    if not farm or farm:getType() ~= df.building_type.FarmPlot then
        qerror('no farm plot with id ' .. tostring(farm_id))
    end
    if farm:getBuildStage() < farm:getMaxBuildStage() then
        qerror('farm plot is not complete yet')
    end
    local plant_idx = utils.linear_index(
        df.global.world.raws.plants.all, crop_id, 'id')
    if not plant_idx then qerror('unknown crop raw id: ' .. crop_id) end
    local raw = df.global.world.raws.plants.all[plant_idx]
    local available_seeds = seed_count(plant_idx)
    if available_seeds < 1 then qerror('no available seeds for ' .. crop_id) end
    local environment = farm_environment(farm)
    local subterranean_crop = raw.underground_depth_max > 0
    if environment == 'subterranean' and not subterranean_crop then
        qerror(crop_id .. ' is not a subterranean crop')
    elseif environment == 'surface' and subterranean_crop then
        qerror(crop_id .. ' is not a surface crop')
    end
    local before, changed = {}, 0
    for _,season in ipairs(requested) do
        if not raw_supports_season(raw, season) then
            qerror(crop_id .. ' cannot be planted in ' .. season)
        end
        local idx = SEASON_INDEX[season]
        local prior = farm.plant_id[idx]
        before[season] = prior >= 0
            and df.global.world.raws.plants.all[prior].id or nil
        if prior ~= plant_idx then
            farm.plant_id[idx] = plant_idx
            changed = changed + 1
        end
    end
    local after = {}
    for _,season in ipairs(SEASONS) do
        local idx, assigned = SEASON_INDEX[season], farm.plant_id[SEASON_INDEX[season]]
        after[season] = assigned >= 0
            and df.global.world.raws.plants.all[assigned].id or nil
    end
    write({status=changed > 0 and 'applied' or 'no_effect',
        effect='farm_crop_assignment', farm_id=farm_id,
        environment=environment, crop_id=crop_id,
        crop_name=U(raw.name), seasons=requested,
        available_seeds=available_seeds, before=before, after=after,
        changed=changed})
else
    qerror('unknown ops-farm command: ' .. command)
end
