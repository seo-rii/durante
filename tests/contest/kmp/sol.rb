t, p = STDIN.read.split
t = t.bytes
p = p.bytes
m = p.size
fail = Array.new(m, 0)
k = 0
(1...m).each do |i|
  k = fail[k - 1] while k > 0 && p[i] != p[k]
  k += 1 if p[i] == p[k]
  fail[i] = k
end
count = total = k = 0
i = 0
n = t.size
while i < n
  ch = t[i]
  k = fail[k - 1] while k > 0 && ch != p[k]
  k += 1 if ch == p[k]
  if k == m
    count += 1
    total += i - m + 2
    k = fail[k - 1]
  end
  i += 1
end
puts "#{count} #{total}"
