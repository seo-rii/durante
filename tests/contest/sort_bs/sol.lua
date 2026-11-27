local data = io.read("a")
local nums, cnt = {}, 0
for tok in data:gmatch("%S+") do cnt = cnt + 1; nums[cnt] = tok end
local n, q = math.tointeger(tonumber(nums[1])), math.tointeger(tonumber(nums[2]))
local a = {}
for i = 1, n do a[i] = math.tointeger(tonumber(nums[2 + i])) end
table.sort(a)
local out = {}
for k = 1, q do
  local x = math.tointeger(tonumber(nums[2 + n + k]))
  local lo, hi = 1, n + 1
  while lo < hi do
    local mid = (lo + hi) // 2
    if a[mid] <= x then lo = mid + 1 else hi = mid end
  end
  out[k] = lo - 1
end
io.write(table.concat(out, "\n"), "\n")
