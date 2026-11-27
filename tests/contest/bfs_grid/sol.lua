local data = io.read("a")
local it = data:gmatch("%S+")
local h, w = tonumber(it()), tonumber(it())
local rows = {}
for i = 1, h do rows[i] = it() end
local grid = table.concat(rows)
local size = h * w
local dist = {}
for i = 0, size - 1 do dist[i] = -1 end
local byte = string.byte
local q, tail = {}, 0
if byte(grid, 1) == 46 then dist[0] = 0; q[0] = 0; tail = 1 end
local head = 0
while head < tail do
  local cur = q[head]
  head = head + 1
  local d = dist[cur] + 1
  local c = cur % w
  local nb = cur - w
  if nb >= 0 and dist[nb] < 0 and byte(grid, nb + 1) == 46 then dist[nb] = d; q[tail] = nb; tail = tail + 1 end
  nb = cur + w
  if nb < size and dist[nb] < 0 and byte(grid, nb + 1) == 46 then dist[nb] = d; q[tail] = nb; tail = tail + 1 end
  nb = cur - 1
  if c > 0 and dist[nb] < 0 and byte(grid, nb + 1) == 46 then dist[nb] = d; q[tail] = nb; tail = tail + 1 end
  nb = cur + 1
  if c + 1 < w and dist[nb] < 0 and byte(grid, nb + 1) == 46 then dist[nb] = d; q[tail] = nb; tail = tail + 1 end
end
print(dist[size - 1] .. " " .. tail)
