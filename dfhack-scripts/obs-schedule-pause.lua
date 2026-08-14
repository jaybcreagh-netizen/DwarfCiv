-- obs-schedule-pause <output.json> <absolute-target-tick>
-- Schedule a pause in simulation-tick time. Poll latency can then observe the
-- already-paused game without advancing it past the experimental boundary.
--@ module = false

local json = require('json')
local args = {...}
local out_path = args[1]
local target = tonumber(args[2])
if not out_path or not target then
    qerror('usage: obs-schedule-pause <output.json> <absolute-target-tick>')
end

local function absolute_tick()
    return df.global.cur_year * 403200 + df.global.cur_year_tick
end

local function schedule()
    local remaining = target - absolute_tick()
    if remaining <= 0 then
        df.global.pause_state = true
        return
    end
    dfhack.timeout(remaining, 'ticks', function()
        -- Be defensive if another system altered the calendar while paused.
        if absolute_tick() < target then
            schedule()
        else
            df.global.pause_state = true
        end
    end)
end

local start = absolute_tick()
schedule()
local f = assert(io.open(out_path, 'w'))
f:write(json.encode({scheduled=true, start_tick=start, target_tick=target,
                     remaining=math.max(0, target-start)}))
f:close()
