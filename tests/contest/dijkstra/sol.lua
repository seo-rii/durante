local data = io.read("a")
local nums, cnt = {}, 0
for tok in data:gmatch("%S+") do cnt = cnt + 1; nums[cnt] = tok end
local n, m = tonumber(nums[1]), tonumber(nums[2])
local start, to, wt = {}, {}, {}
for i = 1, n + 2 do start[i] = 0 end
local eu, ev, ew = {}, {}, {}
for i = 1, m do
  local b = 3 * i
  eu[i], ev[i], ew[i] = tonumber(nums[b]), tonumber(nums[b + 1]), tonumber(nums[b + 2])
  start[eu[i] + 1] = start[eu[i] + 1] + 1
end
for i = 2, n + 2 do start[i] = start[i] + start[i - 1] end
local fill = {}
for i = 1, n + 2 do fill[i] = start[i] end
for i = 1, m do
  local p = fill[eu[i]] + 1
  fill[eu[i]] = p
  to[p], wt[p] = ev[i], ew[i]
end
local inf = math.maxinteger
local dist = {}
for i = 1, n do dist[i] = inf end
dist[1] = 0
local hd, hv, size = {0}, {1}, 1
local reach, total = 0, 0
while size > 0 do
  local d, u = hd[1], hv[1]
  local ld, lv = hd[size], hv[size]
  hd[size], hv[size] = nil, nil
  size = size - 1
  if size > 0 then
    local k = 1
    while true do
      local c = 2 * k
      if c > size then break end
      if c + 1 <= size and hd[c + 1] < hd[c] then c = c + 1 end
      if hd[c] >= ld then break end
      hd[k], hv[k] = hd[c], hv[c]
      k = c
    end
    hd[k], hv[k] = ld, lv
  end
  if d == dist[u] then
    reach = reach + 1
    total = total + d
    for p = start[u] + 1, start[u + 1] do
      local v, nd = to[p], d + wt[p]
      if nd < dist[v] then
        dist[v] = nd
        size = size + 1
        local k = size
        while k > 1 do
          local pa = k // 2
          if hd[pa] <= nd then break end
          hd[k], hv[k] = hd[pa], hv[pa]
          k = pa
        end
        hd[k], hv[k] = nd, v
      end
    end
  end
end
print(string.format("%d %d %d", dist[n] < inf and dist[n] or -1, reach, total))
