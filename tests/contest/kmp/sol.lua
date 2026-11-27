local it = io.read("a"):gmatch("%S+")
local t, p = it(), it()
local n, m = #t, #p
local pb = {p:byte(1, -1)}
local fail = {}
for i = 1, m do fail[i] = 0 end
local k = 0
for i = 2, m do
  while k > 0 and pb[i] ~= pb[k + 1] do k = fail[k] end
  if pb[i] == pb[k + 1] then k = k + 1 end
  fail[i] = k
end
local count, total = 0, 0
k = 0
local byte = string.byte
for i = 1, n do
  local ch = byte(t, i)
  while k > 0 and ch ~= pb[k + 1] do k = fail[k] end
  if ch == pb[k + 1] then k = k + 1 end
  if k == m then
    count = count + 1
    total = total + (i - m + 1)
    k = fail[k]
  end
end
print(count .. " " .. total)
