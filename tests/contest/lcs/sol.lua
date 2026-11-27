local it = io.read("a"):gmatch("%S+")
local a, b = it(), it()
local n, m = #a, #b
local bb = {b:byte(1, -1)}
local prev = {}
for j = 0, m do prev[j] = 0 end
for i = 1, n do
  local ca = a:byte(i)
  local cur = {[0] = 0}
  for j = 1, m do
    if ca == bb[j] then
      cur[j] = prev[j - 1] + 1
    else
      local x, y = prev[j], cur[j - 1]
      cur[j] = x > y and x or y
    end
  end
  prev = cur
end
print(prev[m])
