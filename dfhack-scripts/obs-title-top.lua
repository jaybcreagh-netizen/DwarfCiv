-- obs-title-top: click the top-center button on the title screen.
-- Replicates click_top_title_button() from DFHack's ci/test.lua: the title
-- menus (continue game -> world list -> save list) all put the relevant
-- button in the same centered position.
local gui = require('gui')

local scr = dfhack.gui.getCurViewscreen(true)
local sw, sh = dfhack.screen.getWindowSize()
df.global.gps.mouse_x = sw // 2
df.global.gps.precise_mouse_x = df.global.gps.mouse_x * df.global.gps.tile_pixel_x
if sh < 60 then
    df.global.gps.mouse_y = 25
else
    df.global.gps.mouse_y = (sh // 2) + 3
end
df.global.gps.precise_mouse_y = df.global.gps.mouse_y * df.global.gps.tile_pixel_y
gui.simulateInput(scr, '_MOUSE_L')
print('clicked title button at', df.global.gps.mouse_x, df.global.gps.mouse_y)
