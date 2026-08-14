-- ops-hospital <output.json> <prepare|build-zone|furnish> ...
-- Bounded, epistemically honest hospital-room and location construction.
--@ module = false

local json=require('json')
local args={...}
local out_path,command=args[1],args[2]
if not out_path or not command then
    qerror('usage: ops-hospital <output.json> <prepare|repair-access|build-zone|furnish> ...')
end

local KEY='dwarfciv/hospital-room-v1'

local function write(value) json.encode_file(value,out_path) end

local function anchor_pos()
    for _,b in ipairs(df.global.world.buildings.all) do
        if b:getType() == df.building_type.Wagon then
            return xyz2pos(b.centerx,b.centery,b.z)
        end
    end
    for _,u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u,true) and not dfhack.units.isDead(u) then
            return xyz2pos(u.pos.x,u.pos.y,u.pos.z)
        end
    end
    qerror('cannot find a fortress anchor')
end

local function rectangles_overlap(a,b)
    if not a or not b or a.z ~= b.z then return false end
    return a.x1 < b.x1+b.width and b.x1 < a.x1+a.width
        and a.y1 < b.y1+b.height and b.y1 < a.y1+a.height
end

local function valid_surface_entry(pos,width,height)
    local mapx,mapy,_=dfhack.maps.getTileSize()
    local half=math.floor(height/2)
    if pos.x < 1 or pos.x+width+2 >= mapx
        or pos.y-half < 1 or pos.y-half+height >= mapy
        or pos.z < 1 then return false end
    local flags,occupancy=dfhack.maps.getTileFlags(pos)
    local tt=dfhack.maps.getTileType(pos)
    if not flags or not occupancy or not tt or flags.hidden
        or flags.subterranean or flags.flow_size > 0
        or occupancy.building ~= 0 then return false end
    local shape=df.tiletype.attrs[tt].shape
    return df.tiletype_shape.attrs[shape].basic_shape
        == df.tiletype_shape_basic.Floor
end

local function find_surface_entry(width,height)
    local anchor=anchor_pos()
    local mapx,mapy,_=dfhack.maps.getTileSize()
    local half=math.floor(height/2)
    local farm=dfhack.persistent.getSiteData('dwarfciv/farm-room-v1')
    local farm_room=farm and farm.room or nil
    local best,best_score=nil,math.huge
    for y=math.max(1,anchor.y-45),math.min(mapy-2,anchor.y+45) do
        for x=math.max(1,anchor.x-45),math.min(mapx-2,anchor.x+45) do
            local pos=xyz2pos(x,y,anchor.z)
            local room={x1=x+2,y1=y-half,z=anchor.z-1,
                        width=width,height=height}
            if valid_surface_entry(pos,width,height)
                and not rectangles_overlap(room,farm_room) then
                local dx,dy=x-anchor.x,y-anchor.y
                local score=dx*dx+dy*dy
                if score < best_score then best,best_score=pos,score end
            end
        end
    end
    return best,anchor
end

local function room_state(room)
    local total=room.width*room.height
    local hidden,floors,active,unsafe=0,0,0,0
    for y=room.y1,room.y1+room.height-1 do
        for x=room.x1,room.x1+room.width-1 do
            local pos=xyz2pos(x,y,room.z)
            local flags,occupancy=dfhack.maps.getTileFlags(pos)
            local tt=dfhack.maps.getTileType(pos)
            if not flags or not occupancy or not tt then
                unsafe=unsafe+1
            else
                local block=dfhack.maps.getTileBlock(pos)
                local des=block and block.designation[x%16][y%16]
                if des and des.dig ~= df.tile_dig_designation.No then
                    active=active+1
                end
                if flags.hidden then
                    hidden=hidden+1
                    goto continue
                end
                if flags.flow_size > 0 then unsafe=unsafe+1 end
                local shape=df.tiletype.attrs[tt].shape
                if df.tiletype_shape.attrs[shape].basic_shape
                    == df.tiletype_shape_basic.Floor then floors=floors+1 end
            end
            ::continue::
        end
    end
    local status='designated'
    if hidden == 0 and floors == total and unsafe == 0 then status='ready'
    elseif hidden > 0 and active == 0 then status='blocked'
    elseif unsafe > 0 then status='unsafe' end
    return {status=status,total_tiles=total,hidden_tiles=hidden,
            floor_tiles=floors,active_designations=active,
            unsafe_tiles=unsafe}
end

local function prepare(width,height)
    local existing=dfhack.persistent.getSiteData(KEY)
    if existing then
        local current=room_state(existing.room)
        write({status='no_effect',effect='hospital_room_already_registered',
               project=existing,current=current})
        return
    end
    local entry,anchor=find_surface_entry(width,height)
    if not entry then
        qerror('no safe visible surface floor exists for a bounded hospital-room entry')
    end
    local half=math.floor(height/2)
    local room={x1=entry.x+2,y1=entry.y-half,z=entry.z-1,
                width=width,height=height}
    -- Channeling a visible surface floor creates a ramp below and is more
    -- reliable than a down-stair designation on natural grass in v53. The
    -- adjacent hidden tile becomes the first ordinary mining target.
    local data={[0]={[0]={[0]='h1'}},[-1]={}}
    data[-1][0]={[1]='d1'}
    for y=0,height-1 do
        local ry=y-half
        data[-1][ry]=data[-1][ry] or {}
        for x=0,width-1 do data[-1][ry][x+2]='d1' end
    end
    local quickfort=reqscript('quickfort')
    quickfort.apply_blueprint{mode='dig',data=data,pos=entry,dry_run=true}
    quickfort.apply_blueprint{mode='dig',data=data,pos=entry,dry_run=false}
    local project={version=2,status='designated',access_mode='channel_ramp',
        entry={x=entry.x,y=entry.y,z=entry.z},room=room}
    local current=room_state(room)
    if current.active_designations == 0 and current.status ~= 'ready' then
        qerror('quickfort created no hospital-room designations')
    end
    dfhack.persistent.saveSiteData(KEY,project)
    write({status='applied',effect='hospital_room_designated',project=project,
           current=current,anchor={x=anchor.x,y=anchor.y,z=anchor.z},
           epistemic_scope='entry selected from visible surface facts only; hidden geology was not inspected'})
end

local function repair_access()
    local project=dfhack.persistent.getSiteData(KEY)
    if not project or not project.entry or not project.room then
        qerror('no registered hospital room; prepare it first')
    end
    if project.location_id then
        write({status='no_effect',effect='hospital_access_already_zoned',
               project=project})
        return
    end
    local entry=xyz2pos(project.entry.x,project.entry.y,project.entry.z)
    local flags=dfhack.maps.getTileFlags(entry)
    local tt=dfhack.maps.getTileType(entry)
    if not flags or not tt or flags.hidden or flags.flow_size > 0 then
        qerror('hospital entry is not a safe visible dry tile')
    end
    local block=dfhack.maps.getTileBlock(entry)
    local des=block and block.designation[entry.x%16][entry.y%16]
    if des and des.dig == df.tile_dig_designation.Channel then
        write({status='no_effect',effect='hospital_access_channel_exists',
               project=project})
        return
    end
    local quickfort=reqscript('quickfort')
    quickfort.apply_blueprint{mode='dig',data='h1',pos=entry,dry_run=true}
    quickfort.apply_blueprint{mode='dig',data='h1',pos=entry,dry_run=false}
    block=dfhack.maps.getTileBlock(entry)
    des=block and block.designation[entry.x%16][entry.y%16]
    if not des or des.dig ~= df.tile_dig_designation.Channel then
        qerror('quickfort did not create the hospital access channel')
    end
    project.version=2
    project.access_mode='channel_ramp'
    dfhack.persistent.saveSiteData(KEY,project)
    write({status='applied',effect='hospital_access_channel_designated',
           project=project,entry=project.entry,
           designation=df.tile_dig_designation[des.dig]})
end

local function build_zone()
    local project=dfhack.persistent.getSiteData(KEY)
    if not project or not project.room then
        qerror('no registered hospital room; prepare it first')
    end
    local current=room_state(project.room)
    if current.status ~= 'ready' then
        qerror('hospital room is not ready: '..current.status)
    end
    local site=dfhack.world.getCurrentSite()
    local function find_hospital_zone(excluded_locations)
        for _,loc in ipairs(site.buildings) do
            if (not excluded_locations or not excluded_locations[loc.id])
                and df.abstract_building_hospitalst:is_instance(loc) then
                for _,id in ipairs(loc.contents.building_ids) do
                    local b=df.building.find(id)
                    if b and df.building_civzonest:is_instance(b)
                        and b.x1 == project.room.x1
                        and b.y1 == project.room.y1
                        and b.z == project.room.z
                        and b.x2-b.x1+1 == project.room.width
                        and b.y2-b.y1+1 == project.room.height then
                        return b,loc
                    end
                end
            end
        end
    end
    local zone,hospital=find_hospital_zone()
    if zone and hospital then
        local already_registered=project.zone_id == zone.id
            and project.location_id == hospital.id
        project.status='zoned'
        project.zone_id=zone.id
        project.location_id=hospital.id
        dfhack.persistent.saveSiteData(KEY,project)
        write({status=already_registered and 'no_effect' or 'applied',
               effect=already_registered
                   and 'native_hospital_location_already_exists'
                   or 'hospital_location_receipt_reconciled',
               zone_id=zone.id,location_id=hospital.id,room=project.room,
               active=zone.spec_sub_flag.active,linked=true})
        return
    end
    local before_locations={}
    for _,loc in ipairs(site.buildings) do before_locations[loc.id]=true end
    local room=project.room
    local pos=xyz2pos(room.x1,room.y1,room.z)
    local quickfort=reqscript('quickfort')
    local data=('m{location=hospital name="Hospital" allow=residents}(%dx%d)')
        :format(room.width,room.height)
    quickfort.apply_blueprint{mode='zone',data=data,pos=pos,dry_run=true}
    local stats=quickfort.apply_blueprint{mode='zone',data=data,pos=pos,dry_run=false}
    zone,hospital=find_hospital_zone(before_locations)
    if not zone or not hospital then
        qerror('quickfort did not create both hospital zone and location')
    end
    local linked=false
    for _,id in ipairs(hospital.contents.building_ids) do
        if id == zone.id then linked=true; break end
    end
    if not linked then qerror('new hospital location is not linked to its zone') end
    project.status='zoned'
    project.zone_id=zone.id
    project.location_id=hospital.id
    dfhack.persistent.saveSiteData(KEY,project)
    write({status='applied',effect='native_hospital_location_created',
           zone_id=zone.id,location_id=hospital.id,
           room=room,active=zone.spec_sub_flag.active,linked=linked,
           quickfort_stats=stats})
end

local function furnish()
    local project=dfhack.persistent.getSiteData(KEY)
    if not project or not project.room then qerror('hospital room is not registered') end
    local site=dfhack.world.getCurrentSite()
    local location=nil
    local zone=project.zone_id and df.building.find(project.zone_id) or nil
    for _,candidate in ipairs(site and site.buildings or {}) do
        if candidate.id == project.location_id then location=candidate; break end
    end
    if not location or not zone then
        location,zone=nil,nil
        for _,candidate in ipairs(site and site.buildings or {}) do
            if df.abstract_building_hospitalst:is_instance(candidate) then
                for _,id in ipairs(candidate.contents.building_ids) do
                    local b=df.building.find(id)
                    if b and df.building_civzonest:is_instance(b)
                        and b.x1 == project.room.x1
                        and b.y1 == project.room.y1 and b.z == project.room.z
                        and b.x2-b.x1+1 == project.room.width
                        and b.y2-b.y1+1 == project.room.height then
                        location,zone=candidate,b
                        break
                    end
                end
            end
            if location then break end
        end
    end
    if not location or not df.abstract_building_hospitalst:is_instance(location)
        or not zone or not df.building_civzonest:is_instance(zone) then
        qerror('registered hospital location or zone no longer exists')
    end
    local linked=false
    for _,id in ipairs(location.contents.building_ids) do
        if id == zone.id then linked=true; break end
    end
    if not linked then qerror('registered hospital zone is no longer linked') end
    project.status='zoned'
    project.zone_id=zone.id
    project.location_id=location.id
    dfhack.persistent.saveSiteData(KEY,project)
    if room_state(project.room).status ~= 'ready' then
        qerror('hospital room is no longer a safe completed floor')
    end
    local targets={
        {key='bed',logical='beds',symbol='b',dx=1,dy=1,kind='Bed'},
        {key='table',logical='tables',symbol='t',dx=3,dy=1,kind='Table'},
        {key='container',logical='containers',symbol='h',dx=5,dy=1,
         kind='Box'},
    }
    local function at_target(target)
        local x=project.room.x1+target.dx
        local y=project.room.y1+target.dy
        for _,b in ipairs(df.global.world.buildings.all) do
            local kind=df.building_type[b:getType()] or ''
            if b.centerx == x and b.centery == y and b.z == project.room.z
                and (kind == target.kind
                    or target.kind == 'Box' and kind == 'Container') then
                return b
            end
        end
    end
    local data={[0]={}}
    local missing=0
    for _,target in ipairs(targets) do
        if not at_target(target) then
            data[0][target.dy]=data[0][target.dy] or {}
            data[0][target.dy][target.dx]=target.symbol..'{do_now=true}'
            missing=missing+1
        end
    end
    if missing == 0 then
        local existing={}
        for _,target in ipairs(targets) do
            local b=at_target(target)
            existing[#existing+1]={logical_type=target.logical,
                building_id=b.id,build_stage=b:getBuildStage(),
                max_build_stage=b:getMaxBuildStage()}
        end
        write({status='no_effect',effect='hospital_furnishings_already_designated',
               buildings=existing,zone_id=zone.id,location_id=location.id})
        return
    end
    local quickfort=reqscript('quickfort')
    quickfort.apply_blueprint{mode='build',data=data,
        pos=xyz2pos(project.room.x1,project.room.y1,project.room.z),dry_run=true}
    local stats=quickfort.apply_blueprint{mode='build',data=data,
        pos=xyz2pos(project.room.x1,project.room.y1,project.room.z),dry_run=false}
    local buildings={}
    for _,target in ipairs(targets) do
        local b=at_target(target)
        if not b then
            qerror('quickfort did not designate hospital '..target.key)
        end
        local attached={}
        for _,rec in ipairs(b.contained_items) do
            attached[#attached+1]=rec.item.id
        end
        buildings[#buildings+1]={logical_type=target.logical,
            building_id=b.id,build_stage=b:getBuildStage(),
            max_build_stage=b:getMaxBuildStage(),attached_item_ids=attached}
    end
    write({status='applied',effect='hospital_furnishings_designated',
           buildings=buildings,zone_id=zone.id,location_id=location.id,
           quickfort_stats=stats})
end

if command == 'prepare' then
    local width,height=tonumber(args[3]),tonumber(args[4])
    if not width or not height or width < 5 or height < 5
        or width > 11 or height > 11 then
        qerror('hospital room dimensions must be integers between 5 and 11')
    end
    prepare(width,height)
elseif command == 'build-zone' then
    build_zone()
elseif command == 'repair-access' then
    repair_access()
elseif command == 'furnish' then
    furnish()
else
    qerror('unknown ops-hospital command: '..tostring(command))
end
