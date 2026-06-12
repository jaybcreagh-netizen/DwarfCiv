local gui = require('gui')
local args = {...}
local scr = dfhack.gui.getCurViewscreen()
local sw, sh = dfhack.screen.getWindowSize()
df.global.gps.mouse_x = sw // 2
df.global.gps.precise_mouse_x = df.global.gps.mouse_x * df.global.gps.tile_pixel_x
if sh < 60 then df.global.gps.mouse_y = 25 else df.global.gps.mouse_y = (sh // 2) + 3 end
df.global.gps.precise_mouse_y = df.global.gps.mouse_y * df.global.gps.tile_pixel_y
print('window', sw, sh, 'mouse', df.global.gps.mouse_x, df.global.gps.mouse_y)
gui.simulateInput(scr, '_MOUSE_L')
