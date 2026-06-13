-- obs-clicktext <text...>: find a string on the rendered screen and click it.
local gui = require('gui')
local target = table.concat({...}, ' ')
local sw, sh = dfhack.screen.getWindowSize()
for y = 0, sh - 1 do
    local line = {}
    for x = 0, sw - 1 do
        local pen = dfhack.screen.readTile(x, y)
        local ch = pen and pen.ch or 32
        if ch < 32 or ch > 126 then ch = 32 end
        line[#line + 1] = string.char(ch)
    end
    local s = table.concat(line)
    local i = s:find(target, 1, true)
    if i then
        local cx = i - 1 + math.floor(#target / 2)
        df.global.gps.mouse_x = cx
        df.global.gps.precise_mouse_x = cx * df.global.gps.tile_pixel_x
        df.global.gps.mouse_y = y
        df.global.gps.precise_mouse_y = y * df.global.gps.tile_pixel_y
        gui.simulateInput(dfhack.gui.getCurViewscreen(), '_MOUSE_L')
        print(('clicked %q at %d,%d'):format(target, cx, y))
        return
    end
end
qerror(('text not found on screen: %q'):format(target))
