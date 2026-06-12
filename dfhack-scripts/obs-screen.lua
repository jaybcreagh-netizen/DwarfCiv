-- Dump the current text screen to stdout (headless debugging aid).
local sw, sh = dfhack.screen.getWindowSize()
for y = 0, sh - 1 do
    local line = {}
    for x = 0, sw - 1 do
        local pen = dfhack.screen.readTile(x, y)
        local ch = pen and pen.ch or 32
        if ch < 32 or ch > 126 then ch = 32 end
        line[#line + 1] = string.char(ch)
    end
    print(string.format('%02d|%s', y, table.concat(line)))
end
