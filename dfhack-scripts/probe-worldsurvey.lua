local json=require('json')
local wd=df.global.world.world_data
local out={world={w=wd.world_width,h=wd.world_height}}

-- What fields does a region tile actually expose here?
local sample=wd.region_map[0][0]
local keys={}
for k,_ in pairs(sample) do keys[#keys+1]=tostring(k) end
table.sort(keys)
out.region_fields=keys

-- Geo biome layer info: soil depth and aquifers decide farming and digging.
local function geo_profile(gi)
    local gb=wd.geo_biomes[gi]
    if not gb then return nil end
    local soil,aquifer,layers=0,false,0
    for _,l in ipairs(gb.layers) do
        layers=layers+1
        local ok,is_soil=pcall(function() return l.type == df.geo_layer_type.SOIL end)
        if ok and is_soil then soil=soil+1 end
        local ok2,aq=pcall(function() return l.flags.aquifer end)
        if ok2 and aq then aquifer=true end
    end
    return {soil_layers=soil,total_layers=layers,aquifer=aquifer}
end

local tiles={}
for x=0,wd.world_width-1 do
    for y=0,wd.world_height-1 do
        local r=wd.region_map[x][y]
        local river=false
        local ok=pcall(function()
            river=(r.flags.is_brook or false)
        end)
        local rv,rh=0,0
        pcall(function() rv=r.rivers_vertical.active and 1 or 0 end)
        pcall(function() rh=r.rivers_horizontal.active and 1 or 0 end)
        tiles[#tiles+1]={x=x,y=y,elevation=r.elevation,
            rainfall=r.rainfall,drainage=r.drainage,
            geo=r.geo_index,brook=river,rv=rv,rh=rh,
            salinity=r.salinity}
    end
end
out.tiles=tiles
local geos={}
for gi=0,#wd.geo_biomes-1 do geos[tostring(gi)]=geo_profile(gi) end
out.geo=geos
print(json.encode(out))
