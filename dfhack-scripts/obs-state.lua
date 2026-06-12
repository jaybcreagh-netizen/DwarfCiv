-- obs-state <output.json>: dump fortress state for the harness briefing.
-- Every section is pcall-guarded: a struct mismatch in one section must not
-- take down the whole dump (its absence is recorded in state.errors).
--@ module = false

local json = require('json')

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
        site_name = site and dfhack.translation.translateName(site.name, true) or nil,
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
        if dfhack.units.isCitizen(u, true) then
            local entry = {
                id = u.id,
                name = dfhack.units.getReadableName(u),
                profession = dfhack.units.getProfessionName(u),
                stress_category = dfhack.units.getStressCategory(u),
                stress_label = STRESS_LABEL[dfhack.units.getStressCategory(u)]
                    or tostring(dfhack.units.getStressCategory(u)),
                adult = dfhack.units.isAdult(u),
            }
            if u.mood ~= df.mood_type.None then
                entry.strange_mood = df.mood_type[u.mood]
            end
            local job = u.job.current_job
            entry.current_job = job and dfhack.job.getName(job) or nil
            list[#list + 1] = entry
        end
    end
    state.dwarves = list
    state.population = #list
end)

section('idlers', function()
    local n = 0
    for _, u in ipairs(df.global.world.units.active) do
        if dfhack.units.isCitizen(u, true) and dfhack.units.isAdult(u)
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
        seeds = {[it.SEEDS] = true},
        wood = {[it.WOOD] = true},
        stone = {[it.BOULDER] = true},
        bars = {[it.BAR] = true},
    }
    local counts = {food = 0, plants = 0, drink = 0, seeds = 0, wood = 0,
                    stone = 0, bars = 0}
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
        end
    end
    -- edible food = prepared/raw food + plants (rough but stable)
    counts.food_total = counts.food + counts.plants
    state.stocks = counts
end)

-- ---- threats -----------------------------------------------------------------
section('threats', function()
    local hostiles = {}
    for _, u in ipairs(df.global.world.units.active) do
        if dfhack.units.isActive(u) and dfhack.units.isDanger(u)
            and not dfhack.units.isDead(u) then
            hostiles[#hostiles + 1] = {
                id = u.id,
                name = dfhack.units.getReadableName(u),
                invader = dfhack.units.isInvader(u),
                great_danger = dfhack.units.isGreatDanger(u),
            }
        end
    end
    state.threats = {
        hostiles = hostiles,
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
                    name = dfhack.translation.translateName(sq.name, true),
                    alias = sq.alias,
                    members = members,
                }
            end
        end
    end
    state.squads = squads
end)

-- ---- pending matters -----------------------------------------------------------
section('mandates', function()
    local mandates = {}
    for _, m in ipairs(df.global.world.mandates.all) do
        local unit = m.unit
        mandates[#mandates + 1] = {
            noble = unit and dfhack.units.getReadableName(unit) or 'unknown',
            mode = df.mandate.T_mode[m.mode],
            item_type = df.item_type[m.item_type],
            amount_total = m.amount_total,
            timeout_left = m.timeout_limit - m.timeout_counter,
        }
    end
    state.mandates = mandates
end)

section('petitions', function()
    local n = 0
    for _, agr in ipairs(df.global.world.agreements.all) do
        local d = agr.details
        if #d > 0 and not agr.flags.convicted_accepted
            and not agr.flags.petition_not_accepted then
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
