# Binary heap of [distance, vertex] pairs; Ruby has no standard priority queue.
data = STDIN.read.split.map!(&:to_i)
n, m = data[0], data[1]
adj = Array.new(n + 1) { [] }
i = 2
m.times do
  adj[data[i]] << data[i + 1] << data[i + 2]
  i += 3
end
inf = 1 << 62
dist = Array.new(n + 1, inf)
dist[1] = 0
hd = [0]
hv = [1]
reach = total = 0
until hd.empty?
  d = hd[0]
  u = hv[0]
  ld = hd.pop
  lv = hv.pop
  size = hd.size
  if size > 0
    k = 0
    loop do
      c = 2 * k + 1
      break if c >= size
      c += 1 if c + 1 < size && hd[c + 1] < hd[c]
      break if hd[c] >= ld
      hd[k] = hd[c]
      hv[k] = hv[c]
      k = c
    end
    hd[k] = ld
    hv[k] = lv
  end
  next if d != dist[u]
  reach += 1
  total += d
  e = adj[u]
  j = 0
  while j < e.size
    v = e[j]
    nd = d + e[j + 1]
    j += 2
    next unless nd < dist[v]
    dist[v] = nd
    k = hd.size
    hd << nd
    hv << v
    while k > 0
      pa = (k - 1) >> 1
      break if hd[pa] <= nd
      hd[k] = hd[pa]
      hv[k] = hv[pa]
      k = pa
    end
    hd[k] = nd
    hv[k] = v
  end
end
puts "#{dist[n] < inf ? dist[n] : -1} #{reach} #{total}"
