-- Lua 5.4 stops near 200k nested Lua calls ("stack overflow"), below depth n,
-- so this submission uses an explicit stack (iterative post-order) instead.
local data = io.read("a")
local nums, cnt = {}, 0
for tok in data:gmatch("%S+") do cnt = cnt + 1; nums[cnt] = tok end
local MOD = 1000000007
local n = math.tointeger(tonumber(nums[1]))
local head, nxt, to = {}, {}, {}
for i = 1, n do head[i] = 0 end
local e = 0
for i = 1, n - 1 do
  local u, v = math.tointeger(tonumber(nums[2 * i])), math.tointeger(tonumber(nums[2 * i + 1]))
  e = e + 1; to[e] = v; nxt[e] = head[u]; head[u] = e
  e = e + 1; to[e] = u; nxt[e] = head[v]; head[v] = e
end
local size, parent, iter = {}, {}, {}
local stack, sp = {1}, 1
parent[1], size[1], iter[1] = 0, 1, head[1]
local maxDepth, total = 0, 0
while sp > 0 do
  local v = stack[sp]
  local ed = iter[v]
  if ed ~= 0 then
    iter[v] = nxt[ed]
    local u = to[ed]
    if u ~= parent[v] then
      parent[u], size[u], iter[u] = v, 1, head[u]
      sp = sp + 1
      stack[sp] = u
      if sp - 1 > maxDepth then maxDepth = sp - 1 end
    end
  else
    sp = sp - 1
    total = (total + size[v] * v) % MOD
    local pa = parent[v]
    if pa ~= 0 then size[pa] = size[pa] + size[v] end
  end
end
print(maxDepth .. " " .. total)
