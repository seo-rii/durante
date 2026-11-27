local data = io.read("a")
local nums, cnt = {}, 0
for tok in data:gmatch("%S+") do cnt = cnt + 1; nums[cnt] = tok end
local n, q = math.tointeger(tonumber(nums[1])), math.tointeger(tonumber(nums[2]))
local bit = {}
for i = 0, n do bit[i] = 0 end
for i = 1, n do
  bit[i] = bit[i] + math.tointeger(tonumber(nums[2 + i]))
  local j = i + (i & -i)
  if j <= n then bit[j] = bit[j] + bit[i] end
end
local out, oc = {}, 0
local p = 3 + n
for _ = 1, q do
  local t, x, y = nums[p], math.tointeger(tonumber(nums[p + 1])), math.tointeger(tonumber(nums[p + 2]))
  p = p + 3
  if t == "1" then
    while x <= n do bit[x] = bit[x] + y; x = x + (x & -x) end
  else
    local s = 0
    while y > 0 do s = s + bit[y]; y = y - (y & -y) end
    x = x - 1
    while x > 0 do s = s - bit[x]; x = x - (x & -x) end
    oc = oc + 1
    out[oc] = s
  end
end
io.write(table.concat(out, "\n"), "\n")
