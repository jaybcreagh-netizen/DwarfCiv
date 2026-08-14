-- ops-logistics <output.json> build-stockpile <kind> <width> <height> [near-id]
-- Bounded, reachable stockpile placement for the survival harness.
--@ module = false

local json = require('json')

local args = {...}
local out_path, command = args[1], args[2]
if not out_path or command ~= 'build-stockpile' then
    qerror('usage: ops-logistics <output.json> build-stockpile <kind> <width> <height> [near-building-id]')
end

local kind = args[3]
local width, height = tonumber(args[4]), tonumber(args[5])
local near_id = args[6] and tonumber(args[6]) or nil
local kinds = {
    food={symbol='f', preset='cat_food'},
    seeds={symbol='f', preset='seeds'},
    plants={symbol='f', preset='plants'},
    booze={symbol='f', preset='booze'},
    wood={symbol='w', preset='cat_wood'},
    refuse={symbol='r', preset='cat_refuse'},
}
if not kinds[kind] then qerror('unsupported stockpile kind: '..tostring(kind)) end
if not width or not height or width < 1 or height < 1
    or width > 10 or height > 10 or width*height > 100 then
    qerror('stockpile dimensions must be integers from 1 to 10 (area <= 100)')
end

local function write(value) json.encode_file(value, out_path) end

local function reference_citizen()
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and not dfhack.units.isDead(u)
            and dfhack.units.isAdult(u) then return u end
    end
end

local function anchor_pos()
    if near_id then
        local b = df.building.find(near_id)
        if not b then qerror('near-building id does not exist: '..near_id) end
        return xyz2pos(b.centerx, b.centery, b.z), b.id
    end
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Wagon then
            return xyz2pos(b.centerx,b.centery,b.z), b.id
        end
    end
    local u = reference_citizen()
    if u then return xyz2pos(u.pos.x,u.pos.y,u.pos.z), nil end
    qerror('cannot find a fortress anchor')
end

local function valid_tile(pos)
    local flags, occupancy = dfhack.maps.getTileFlags(pos)
    local tt = dfhack.maps.getTileType(pos)
    if not flags or not occupancy or not tt or flags.hidden
        or occupancy.building ~= 0 or flags.flow_size > 1 then return false end
    if kind == 'refuse' and not flags.outside then return false end
    local shape = df.tiletype.attrs[tt].shape
    local basic = df.tiletype_shape.attrs[shape].basic_shape
    return basic == df.tiletype_shape_basic.Floor
end

local function valid_rectangle(pos)
    for y=pos.y,pos.y+height-1 do
        for x=pos.x,pos.x+width-1 do
            if not valid_tile(xyz2pos(x,y,pos.z)) then return false end
        end
    end
    return true
end

local anchor, anchor_building_id = anchor_pos()
local citizen = reference_citizen()
local mapx,mapy,_ = dfhack.maps.getTileSize()
local best,best_score = nil,math.huge
for y=math.max(0,anchor.y-35),math.min(mapy-height,anchor.y+35) do
    for x=math.max(0,anchor.x-35),math.min(mapx-width,anchor.x+35) do
        local pos=xyz2pos(x,y,anchor.z)
        if valid_rectangle(pos)
            and (not citizen or dfhack.maps.canWalkBetween(citizen.pos,pos)) then
            local dx,dy=x-anchor.x,y-anchor.y
            local flags=dfhack.maps.getTileFlags(pos)
            local outside_penalty=(kind ~= 'refuse' and flags.outside) and 10000 or 0
            local score=dx*dx+dy*dy+outside_penalty
            if score < best_score then best,best_score=pos,score end
        end
    end
end
if not best then
    qerror('no visible reachable '..width..'x'..height..' footprint for '..kind..' stockpile within 35 tiles')
end

local name_prefix='DwarfCiv '..kind
for _,b in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if b.name:startswith(name_prefix) then
        write({status='no_effect', effect='stockpile_kind_already_exists',
               kind=kind, stockpile_id=b.id, name=b.name,
               reachable=not citizen or dfhack.maps.canWalkBetween(citizen.pos,
                   xyz2pos(b.centerx,b.centery,b.z))})
        return
    end
end

local before={}
for _,b in ipairs(df.global.world.buildings.other.STOCKPILE) do before[b.id]=true end
local spec=kinds[kind]
local name=name_prefix..' 1'
local token=spec.symbol..'{name="'..name..'"}:='..spec.preset..
    '('..width..'x'..height..')'
local data={[0]={[0]={[0]=token}}}
local quickfort=reqscript('quickfort')
quickfort.apply_blueprint{mode='place',data=data,pos=best,dry_run=true}
quickfort.apply_blueprint{mode='place',data=data,pos=best,dry_run=false}

local created=nil
for _,b in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if not before[b.id] then created=b; break end
end
if not created then qerror('quickfort reported no new stockpile') end
local categories={}
for _,flag in ipairs({'animals','food','furniture','coins','corpses','refuse',
                      'stone','wood','gems','bars_blocks','cloth','leather',
                      'ammo','finished_goods','weapons','armor','sheet'}) do
    if created.settings.flags[flag] then categories[#categories+1]=flag end
end
write({status='applied',effect='typed_stockpile_created',kind=kind,
       preset=spec.preset,stockpile_id=created.id,name=created.name,
       pos={x=created.x1,y=created.y1,z=created.z},
       width=created.x2-created.x1+1,height=created.y2-created.y1+1,
       categories=categories,anchor_building_id=anchor_building_id,
       reference_citizen_id=citizen and citizen.id or nil,
       reachable=not citizen or dfhack.maps.canWalkBetween(citizen.pos,
           xyz2pos(created.centerx,created.centery,created.z))})
