-- obs-mapclick <x> <y> [hold_frames]: frame-pinned left click at grid (x,y).
--
-- gui.simulateInput's _MOUSE_L only feeds the interface layer synchronously
-- and reverts the enabler button state before the next frame, so clicks on
-- frame-polled UI (the world/local maps, bottom-bar buttons on the embark
-- screen) never register. Headless DF also resets gps.mouse_* to -1 every
-- frame (no real SDL mouse), so the coordinates must be re-pinned each
-- frame across the press/release cycle. This script installs a per-frame
-- timeout that pins the cursor, presses after a few frames, releases, and
-- expires.
local args = {...}
local x, y = tonumber(args[1]), tonumber(args[2])
local hold = tonumber(args[3]) or 3
if not x or not y then qerror('usage: obs-mapclick <x> <y> [hold_frames]') end

local PRESS_AT = 4
local total = PRESS_AT + hold + 8
local count = 0

local function pin()
    local g = df.global.gps
    g.mouse_x = x
    g.mouse_y = y
    g.precise_mouse_x = x * g.tile_pixel_x
    g.precise_mouse_y = y * g.tile_pixel_y
    df.global.enabler.tracking_on = 1
    count = count + 1
    if count == PRESS_AT then
        df.global.enabler.mouse_lbut = 1
        df.global.enabler.mouse_lbut_down = 1
    elseif count == PRESS_AT + hold then
        df.global.enabler.mouse_lbut = 0
        df.global.enabler.mouse_lbut_down = 0
    end
    if count < total then
        dfhack.timeout(1, 'frames', pin)
    end
end

pin()
print(('mapclick scheduled at %d,%d'):format(x, y))
