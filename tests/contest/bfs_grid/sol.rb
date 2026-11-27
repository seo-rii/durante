data = STDIN.read.split
h, w = data[0].to_i, data[1].to_i
grid = data[2, h].join
size = h * w
dist = Array.new(size, -1)
q = []
if grid.getbyte(0) == 46
  dist[0] = 0
  q << 0
end
head = 0
while head < q.size
  cur = q[head]
  head += 1
  d = dist[cur] + 1
  c = cur % w
  nb = cur - w
  if nb >= 0 && dist[nb] < 0 && grid.getbyte(nb) == 46 then dist[nb] = d; q << nb end
  nb = cur + w
  if nb < size && dist[nb] < 0 && grid.getbyte(nb) == 46 then dist[nb] = d; q << nb end
  nb = cur - 1
  if c > 0 && dist[nb] < 0 && grid.getbyte(nb) == 46 then dist[nb] = d; q << nb end
  nb = cur + 1
  if c + 1 < w && dist[nb] < 0 && grid.getbyte(nb) == 46 then dist[nb] = d; q << nb end
end
puts "#{dist[size - 1]} #{q.size}"
