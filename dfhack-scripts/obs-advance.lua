-- obs-advance <out.json> [target_abs_tick]
-- One poll step of the harness tick loop. Reports current progress and, if
-- a target tick is given, keeps the simulation moving toward it:
--   * dismisses blocking popups (anything that pulls focus off
--     dwarfmode/Default gets one LEAVESCREEN per poll)
--   * unpauses if DF auto-paused (announcements, sieges, ...)
--   * hard-pauses once the target tick is reached
local json = require('json')
local gui = require('gui')

local args = {...}
local out_path = args[1]
if not out_path then qerror('usage: obs-advance <out.json> [target_tick]') end
local target = args[2] and tonumber(args[2]) or nil

local MONTHS = {'Granite', 'Slate', 'Felsite', 'Hematite', 'Malachite',
                'Galena', 'Limestone', 'Sandstone', 'Timber', 'Moonstone',
                'Opal', 'Obsidian'}
local SEASONS = {'spring', 'summer', 'autumn', 'winter'}

local function date_table()
    local year, tick = df.global.cur_year, df.global.cur_year_tick
    local month = tick // 33600
    local day = (tick % 33600) // 1200 + 1
    return {
        year = year,
        tick_of_year = tick,
        absolute_tick = year * 403200 + tick,
        month = MONTHS[month + 1],
        day = day,
        season = SEASONS[tick // 100800 + 1],
        pretty = ('%d %s, year %d'):format(day, MONTHS[month + 1], year),
    }
end

local focus = table.concat(dfhack.gui.getFocusStrings(
    dfhack.gui.getCurViewscreen()), ',')
local status = {
    date = date_table(),
    paused = df.global.pause_state,
    focus = focus,
    map_loaded = dfhack.isMapLoaded(),
    fortress_mode = dfhack.world.isFortressMode(),
    action = 'none',
}

if target then
    local cur = status.date.absolute_tick
    if cur >= target then
        df.global.pause_state = true
        status.action = 'paused_at_target'
    else
        -- A popup (e.g. megabeast announcement) blocks sim progress and
        -- shifts focus away from dwarfmode/Default. Nudge it closed.
        if status.fortress_mode and not focus:find('dwarfmode/Default', 1, true) then
            gui.simulateInput(dfhack.gui.getCurViewscreen(), 'LEAVESCREEN')
            status.action = 'dismissed_popup:' .. focus
        elseif df.global.pause_state then
            df.global.pause_state = false
            status.action = 'unpaused'
        else
            status.action = 'running'
        end
    end
end

json.encode_file(status, out_path)
print('ok')
