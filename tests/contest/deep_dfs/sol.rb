# Plain recursion. Ruby's VM stack (1 MiB) cannot hold depth n, so the harness
# sets RUBY_THREAD_VM_STACK_SIZE (it sizes the main thread too); the recursion
# also runs on a new Thread, a common contest habit.
MOD = 1_000_000_007
data = STDIN.read.split.map!(&:to_i)
n = data[0]
$adj = Array.new(n + 1) { [] }
i = 1
(n - 1).times do
  u, v = data[i], data[i + 1]
  $adj[u] << v
  $adj[v] << u
  i += 2
end
$max_depth = 0
$total = 0

def dfs(v, parent, depth)
  $max_depth = depth if depth > $max_depth
  size = 1
  nbrs = $adj[v]
  k = 0
  while k < nbrs.size
    u = nbrs[k]
    size += dfs(u, v, depth + 1) if u != parent
    k += 1
  end
  $total = ($total + size * v) % MOD
  size
end

Thread.new { dfs(1, 0, 0) }.join
puts "#{$max_depth} #{$total}"
