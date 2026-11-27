data = STDIN.read.split.map!(&:to_i)
n, q = data[0], data[1]
bit = Array.new(n + 1, 0)
(1..n).each do |i|
  bit[i] += data[1 + i]
  j = i + (i & -i)
  bit[j] += bit[i] if j <= n
end
out = []
p = 2 + n
q.times do
  t, x, y = data[p], data[p + 1], data[p + 2]
  p += 3
  if t == 1
    while x <= n
      bit[x] += y
      x += x & -x
    end
  else
    s = 0
    while y > 0
      s += bit[y]
      y -= y & -y
    end
    x -= 1
    while x > 0
      s -= bit[x]
      x -= x & -x
    end
    out << s
  end
end
STDOUT.write(out.join("\n") << "\n")
