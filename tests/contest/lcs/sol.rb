a, b = STDIN.read.split
a = a.bytes
b = b.bytes
m = b.size
prev = Array.new(m + 1, 0)
a.each do |ca|
  cur = Array.new(m + 1, 0)
  j = 1
  while j <= m
    if ca == b[j - 1]
      cur[j] = prev[j - 1] + 1
    else
      x = prev[j]
      y = cur[j - 1]
      cur[j] = x > y ? x : y
    end
    j += 1
  end
  prev = cur
end
puts prev[m]
